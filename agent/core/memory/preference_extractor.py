"""从会话中提取适合长期保存的用户偏好。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_EXPLICIT_PREFERENCE = re.compile(
    r"(?:记住|记一下|帮我记|请记|以后记得|"
    r"我(?:一直|通常|平时|更|最)?(?:喜欢|偏好|常用|习惯用|不喜欢|讨厌)|"
    r"我的(?:品牌|手机|系统|生态|尺寸|预算|用途)偏好)"
)


def is_explicit_preference_message(message: str) -> bool:
    return bool(message.strip() and _EXPLICIT_PREFERENCE.search(message))


class PreferenceResult(BaseModel):
    """模型返回的完整偏好快照。"""

    full_items: list[str] = Field(default_factory=list, max_length=10)


class PreferenceExtractor:
    """使用当前 DeepSeek Chat 模型提取稳定、非敏感的长期偏好。"""

    def __init__(self, llm: Any):
        self.llm = llm

    async def extract_full(
        self,
        conversation_text: str,
        existing: list[str] | None = None,
    ) -> list[str] | None:
        """返回更新后的完整偏好快照；调用或格式失败时返回 ``None``。"""
        prompt = (
            "下面是用户当前已保存的全部偏好。结合最新对话，输出更新之后的完整全部偏好列表。\n"
            "规则：\n"
            "1. 用户修改偏好时，更新对应条目；其余未改动偏好必须保留，不要无故丢失。\n"
            "2. 只有用户明确要求忘掉、删除或否定某项偏好时，才移除对应条目。\n"
            "3. 只保留跨会话仍有用、长期稳定的数码偏好，过滤临时需求和敏感信息。\n"
            "4. 合并重复或语义冲突的条目，不要编造；最多保留10条。\n"
            "5. 每条使用简洁完整的中文句子。只输出合法JSON，禁止Markdown和解释。\n"
            '格式必须是：{"full_items":["偏好一","偏好二"]}\n\n'
            f"用户已有全部偏好：{existing or []}\n\n最新对话：\n{conversation_text}"
        )
        try:
            # 不使用 with_structured_output：部分 OpenAI 兼容服务（包括当前
            # DeepSeek 接口）不支持 response_format，会直接返回 HTTP 400。
            response = await self.llm.ainvoke(
                [
                    SystemMessage(content="你是严格的用户偏好抽取器。"),
                    HumanMessage(content=prompt),
                ]
            )
            result = self._parse_response(response)
            normalized: list[str] = []
            seen: set[str] = set()
            for raw_item in result.full_items:
                item = raw_item.strip()
                if not item or len(item) > 2048:
                    raise ValueError("Preference item is empty or too long")
                if item not in seen:
                    seen.add(item)
                    normalized.append(item)
            return normalized
        except Exception as exc:
            logger.warning("Preference extraction failed: %s", exc)
            return None

    async def extract(
        self,
        conversation_text: str,
        existing: list[str] | None = None,
    ) -> list[str]:
        """兼容旧调用方；返回完整快照，失败时安全降级为空列表。"""
        result = await self.extract_full(conversation_text, existing)
        return result if result is not None else []

    @staticmethod
    def _parse_response(response: Any) -> PreferenceResult:
        """解析普通聊天响应中的 JSON，不依赖供应商结构化输出能力。"""
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        if not isinstance(content, str):
            raise ValueError("Preference extractor returned non-text content")

        text = _JSON_FENCE.sub("", content.strip()).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            # 兼容模型偶尔在 JSON 前后添加一句简短说明；仍只接受其中的对象。
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise
            payload = json.loads(text[start : end + 1])
        return PreferenceResult.model_validate(payload)



# 现存短板 & 可以优化点
# 模型幻觉风险：LLM 可能编造不存在的偏好，没有校验层。
# 只支持显式偏好（配合正则触发）；不会自动从普通对话提取隐式偏好。
# 去重是简单字符串精确匹配。
# 问题："预算上限6000" 和 "用户预算不要超过6000" 语义等价，字符串不同，会被判定成两条，重复存储。
# 优化：新增时做向量相似度判断，语义重复就丢弃。
# 没有偏好更新逻辑：用户修改偏好（比如 “把我的预算改成 8000”），提取器只会新增，不会删除旧的预算6000，偏好库会同时存在新旧两条冲突偏好。
# 改进思路：
# Prompt 增加指令：如果新偏好覆盖旧偏好，在 items 输出新的，上层做语义冲突检测；
# 或者增加一个独立的 “偏好更新 / 删除” 工具。
# 不做偏好时效标记：所有偏好都是同等长期，没有expire_at过期时间。
# 典型故障场景
# LLM 输出 JSON 字段写错，例如{"preferences":[...]}，Pydantic 校验直接抛异常，返回空列表。
# LLM 输出多条超过 5 条，代码会截断取前 5。
# 对话没有任何偏好，返回空列表，上层不写入。
#思路：LLM 输出完整候选全集，后端做校验，再决定是否覆盖
# prompt = (
# "下面是用户当前已保存全部偏好，结合用户最新对话，输出更新之后的【完整全部偏好列表】。\n"
# "规则：\n"
# "1. 用户修改偏好：更新对应条目，其余未改动偏好务必原样保留，不要无故丢失。\n"
# "2. 用户要求忘掉/删除某偏好，则移除该条目。\n"
# "3. 只保留长期稳定数码偏好，过滤临时需求、敏感信息；最多保留10条。\n"
# "4. 不要编造不存在的偏好。输出JSON，禁止markdown解释。格式："
# '{"full_items":["偏好1","偏好2"]}\n\n'
# f"用户已有全部偏好：{existing or []}\n\n最新对话：\n{conversation_text}"
# )

# 锁定本批次消息范围
#         ↓
# 读取 profile + version + cursor
#         ↓
# 过滤系统消息、工具内容和敏感内容
#         ↓
# 模型生成完整 profile + 删除依据
#         ↓
# Schema / 数量 / 重复 key / 空值校验
#         ↓
# 检查旧条目消失是否存在明确删除证据
#         ↓
# 规范化并比较 profile hash
#         ↓
# 基于 version 执行 CAS 更新
#         ↓
# 成功后推进游标
#         ↓
# 版本冲突则读取最新状态后重试

