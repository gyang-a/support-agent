"""本次专业任务内复用 RAG 查询，并在连续无新增证据时结束检索。"""

import asyncio
import hashlib
import json
import logging

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import SystemMessage, ToolMessage

from core.workflow.failure import RetrievalStalled
from core.observability.retrieval_events import emit_tool_retrieval

logger = logging.getLogger(__name__)
RAG_TOOLS = {"search_technical_documents", "search_after_sales_policies"}


class RagProgressMiddleware(AgentMiddleware):
    def __init__(self, no_progress_limit=2):
        self.no_progress_limit = no_progress_limit
        self.cache = {}
        self.seen = {name: set() for name in RAG_TOOLS}
        self.stagnant = {name: 0 for name in RAG_TOOLS}
        self.closed = set()
        self.lock = asyncio.Lock()

    @staticmethod
    def _key(name, args):
        normalized = {**args, "query": " ".join(args.get("query", "").split()).casefold(),
                      "limit": args.get("limit", 3)}
        return name, json.dumps(normalized, sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _evidence(raw):
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return set()
            results = payload.get("data", {}).get("results", [])
            fingerprints = set()
            for item in results:
                # 不把查询改写、重排分数或结果顺序变化算成新证据。
                evidence = {key: item.get(key) for key in (
                    "document_id", "chunk_id", "section_path", "section", "version",
                    "content_hash", "content", "excerpt", "page_start", "page_end",
                )}
                fingerprints.add(hashlib.sha256(json.dumps(
                    evidence, sort_keys=True, ensure_ascii=False,
                ).encode()).hexdigest())
            return fingerprints
        except (ValueError, TypeError, AttributeError):
            return set()

    def seed(self, name, args, raw):
        """预检索也是本轮已有证据，必须与工具内追加检索共享。"""
        self.cache[self._key(name, args)] = raw
        self.seen[name].update(self._evidence(raw))
        logger.info("RAG prefetch tool=%s evidence=%d", name, len(self.seen[name]))

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        name = call["name"]
        if name not in RAG_TOOLS:
            return await handler(request)
        async with self.lock:
            if name in self.closed:
                # 工具已从下一轮模型可用列表移除；忽略工具契约的调用不得继续空转。
                raise RetrievalStalled("Repeated retrieval after no-progress stop")
            key = self._key(name, call["args"])
            cached = key in self.cache
            if cached:
                result = ToolMessage(content=self.cache[key], tool_call_id=call["id"], name=name)
            else:
                result = await handler(request)
                if not isinstance(result, ToolMessage):
                    return result
                self.cache[key] = result.content
            emit_tool_retrieval(call, result.content, cached=cached)
            evidence = self._evidence(result.content)
            added = evidence - self.seen[name]
            self.seen[name].update(evidence)
            self.stagnant[name] = 0 if added else self.stagnant[name] + 1
            if self.stagnant[name] >= self.no_progress_limit:
                self.closed.add(name)
            logger.info(
                "RAG progress tool=%s cached=%s new_evidence=%d no_progress=%d stopped=%s query_hash=%s",
                name, cached, len(added), self.stagnant[name], name in self.closed,
                hashlib.sha256(key[1].encode()).hexdigest()[:12],
            )
            return result

    async def awrap_model_call(self, request, handler):
        if not self.closed:
            return await handler(request)
        tools = [tool for tool in request.tools if getattr(tool, "name", None) not in self.closed]
        guidance = (
            "\n【检索收敛】以下检索工具连续未提供新证据，已停止："
            + ", ".join(sorted(self.closed))
            + "。请用预检索和本轮已有证据回答，保留引用；不足的部分明确说无法确认或追问。"
            "不要换同义问法重查，不要改用目录查询冒充说明书证据。"
            "使用 SpecialistResponse 返回最终结果；缺少必要信息用 needs_input。"
        )
        system = request.system_message
        content = system.content if system else ""
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        return await handler(request.override(tools=tools, system_message=SystemMessage(content=content + guidance)))
