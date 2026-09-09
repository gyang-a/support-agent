"""客服会话的 token 预算、滚动摘要与上下文剪枝。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage


logger = logging.getLogger(__name__)

CONTEXT_TOKEN_BUDGET = 100_000
SUMMARY_MAX_CHARS = 1000
RECENT_ROUNDS_TO_KEEP = 20
SUMMARY_CHUNK_TOKENS = 30_000


def estimate_text_tokens(text: str) -> int:
    """对中英文混合文本做保守估算，避免依赖特定供应商 tokenizer。"""
    ascii_count = sum(1 for char in text if ord(char) < 128)
    non_ascii_count = len(text) - ascii_count
    return non_ascii_count + (ascii_count + 3) // 4


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def estimate_message_tokens(message: BaseMessage) -> int:
    name = getattr(message, "name", "") or ""
    return 6 + estimate_text_tokens(
        f"{getattr(message, 'type', 'message')}\n{name}\n{_content_text(message.content)}"
    )


def _tool_context_text(tools: Iterable[Any]) -> str:
    payload: list[dict[str, Any]] = []
    for tool in tools:
        schema = getattr(tool, "args_schema", None)
        if hasattr(schema, "model_json_schema"):
            schema = schema.model_json_schema()
        payload.append(
            {
                "name": getattr(tool, "name", ""),
                "description": getattr(tool, "description", ""),
                "args_schema": schema,
            }
        )
    return json.dumps(payload, ensure_ascii=False, default=str)


def estimate_request_tokens(
    messages: Sequence[BaseMessage],
    *,
    system_prompt: str = "",
    tools: Iterable[Any] = (),
) -> int:
    return (
        estimate_text_tokens(system_prompt)
        + estimate_text_tokens(_tool_context_text(tools))
        + sum(estimate_message_tokens(message) for message in messages)
    )


def _round_start(messages: Sequence[BaseMessage], rounds: int) -> int:
    human_indexes = [
        index
        for index, message in enumerate(messages)
        if getattr(message, "type", "") == "human"
    ]
    if not human_indexes:
        return max(0, len(messages) - 1)
    retained = min(max(1, rounds), len(human_indexes))
    return human_indexes[-retained]


def _summary_message(summary: str) -> SystemMessage:
    return SystemMessage(
        name="conversation_summary",
        content=(
            "【较早会话的滚动摘要】\n"
            f"{summary}\n"
            "摘要中的动态业务状态只是历史记录；本轮必须重新调用对应工具核验。"
        ),
    )


def _render_transcript(messages: Sequence[BaseMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        role = getattr(message, "type", "message")
        name = getattr(message, "name", "") or ""
        source = f"({name})" if name else ""
        lines.append(f"{role}{source}: {_content_text(message.content)}")
    return "\n".join(lines)


def _chunk_messages(
    messages: Sequence[BaseMessage],
    max_tokens: int,
) -> list[list[BaseMessage]]:
    chunks: list[list[BaseMessage]] = []
    current: list[BaseMessage] = []
    current_tokens = 0
    for message in messages:
        message_tokens = estimate_message_tokens(message)
        if current and current_tokens + message_tokens > max_tokens:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(message)
        current_tokens += message_tokens
    if current:
        chunks.append(current)
    return chunks


async def _update_summary(
    summary_model: Any,
    existing_summary: str,
    messages: Sequence[BaseMessage],
    max_chars: int,
) -> str:
    summary = existing_summary.strip()
    for chunk in _chunk_messages(messages, SUMMARY_CHUNK_TOKENS):
        prompt = f"""请更新一份数码商城客服会话摘要，最终严格控制在{max_chars}个中文字符以内。
仅保留：当前目标、预算/品牌/用途、精确型号、SKU、订单号、工单号、用户已确认或拒绝的选项、未解决问题、回答来源 Agent 和必要时间。
价格、库存、物流、订单、政策、工单等动态结果必须注明是历史结果、后续需要重新查询。
删除寒暄、重复解释、大段参数和工具调用过程。不要编造信息。

已有摘要：
{summary or "无"}

新增历史：
{_render_transcript(chunk)}

只输出更新后的摘要正文。"""
        response = await summary_model.ainvoke([HumanMessage(content=prompt)])
        summary = _content_text(response.content).strip()[:max_chars]
    return summary


@dataclass
class PreparedAgentContext:
    messages: list[BaseMessage]
    summary: str
    summarized_message_count: int
    estimated_tokens: int
    compressed: bool
    retained_rounds: int
    budget_overflow: bool


async def prepare_agent_context(
    *,
    messages: Sequence[BaseMessage],
    summary_model: Any,
    existing_summary: str = "",
    summarized_message_count: int = 0,
    system_prompt: str = "",
    tools: Iterable[Any] = (),
    token_budget: int = CONTEXT_TOKEN_BUDGET,
    summary_max_chars: int = SUMMARY_MAX_CHARS,
    recent_rounds: int = RECENT_ROUNDS_TO_KEEP,
) -> PreparedAgentContext:
    """按预算返回模型视图；原始 messages 从不在这里修改。"""
    full_messages = list(messages)
    cursor = min(max(0, summarized_message_count), len(full_messages))
    summary = existing_summary.strip()[:summary_max_chars]
    unsummarized = full_messages[cursor:]
    candidate = ([_summary_message(summary)] if summary else []) + unsummarized
    estimated = estimate_request_tokens(
        candidate,
        system_prompt=system_prompt,
        tools=tools,
    )
    if estimated <= token_budget:
        return PreparedAgentContext(
            messages=candidate,
            summary=summary,
            summarized_message_count=cursor,
            estimated_tokens=estimated,
            compressed=False,
            retained_rounds=sum(
                getattr(message, "type", "") == "human" for message in unsummarized
            ),
            budget_overflow=False,
        )

    tail_start = _round_start(full_messages, recent_rounds)
    summarize_end = max(cursor, tail_start)
    if summarize_end > cursor:
        try:
            summary = await _update_summary(
                summary_model,
                summary,
                full_messages[cursor:summarize_end],
                summary_max_chars,
            )
            cursor = summarize_end
        except Exception:
            # 摘要失败不能阻断客服主链路；保留游标以便下轮重试。
            logger.exception("Conversation summary update failed")

    retained = full_messages[tail_start:]
    candidate = ([_summary_message(summary)] if summary else []) + retained
    estimated = estimate_request_tokens(
        candidate,
        system_prompt=system_prompt,
        tools=tools,
    )

    # 极端长消息下，从最旧完整轮次开始继续缩减；当前用户轮始终保留。
    while estimated > token_budget:
        next_start = _round_start(retained, max(1, sum(
            getattr(message, "type", "") == "human" for message in retained
        ) - 1))
        if next_start <= 0:
            break
        retained = retained[next_start:]
        candidate = ([_summary_message(summary)] if summary else []) + retained
        estimated = estimate_request_tokens(
            candidate,
            system_prompt=system_prompt,
            tools=tools,
        )

    retained_rounds_count = sum(
        getattr(message, "type", "") == "human" for message in retained
    )
    return PreparedAgentContext(
        messages=candidate,
        summary=summary,
        summarized_message_count=cursor,
        estimated_tokens=estimated,
        compressed=True,
        retained_rounds=retained_rounds_count,
        budget_overflow=estimated > token_budget,
    )
