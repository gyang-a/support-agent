"""Agent 工具循环中的模型调用预算保护。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from .context_window import CONTEXT_TOKEN_BUDGET, estimate_request_tokens
from core.workflow.failure import AgentLoopAborted, retrieval_unavailable


TOOL_RESULT_PLACEHOLDER = "[较早的工具结果已因上下文预算被清理]"
TRUNCATION_MARKER = "\n...[工具结果因上下文预算被截断]...\n"


class ToolResultBudgetMiddleware(AgentMiddleware):
    """在每次内部模型调用前清理过大的本轮工具结果。"""

    def __init__(self, token_budget: int = CONTEXT_TOKEN_BUDGET) -> None:
        super().__init__()
        self.token_budget = token_budget
        self.max_estimated_tokens = 0
        self.pruned_tool_results = 0
        self.truncated_tool_results = 0
        self.budget_overflow = False
        self.model_calls = 0

    @staticmethod
    def _replace_tool_content(message: ToolMessage, content: str, strategy: str) -> ToolMessage:
        metadata = dict(message.response_metadata)
        metadata["context_budget"] = {"edited": True, "strategy": strategy}
        return message.model_copy(
            update={
                "artifact": None,
                "content": content,
                "response_metadata": metadata,
            }
        )

    @staticmethod
    def _truncated_content(content: str, retained_chars: int) -> str:
        if retained_chars <= 0:
            return TRUNCATION_MARKER.strip()
        if retained_chars >= len(content):
            return content
        head_chars = max(1, retained_chars * 2 // 3)
        tail_chars = max(0, retained_chars - head_chars)
        tail = content[-tail_chars:] if tail_chars else ""
        return f"{content[:head_chars]}{TRUNCATION_MARKER}{tail}"

    @staticmethod
    def _system_prompt(request: ModelRequest[Any]) -> str:
        return request.system_prompt or ""

    def _estimate(self, request: ModelRequest[Any], messages: Sequence[BaseMessage]) -> int:
        return estimate_request_tokens(
            messages,
            system_prompt=self._system_prompt(request),
            tools=request.tools,
        )

    def _fit_messages(
        self,
        request: ModelRequest[Any],
    ) -> tuple[list[BaseMessage], int]:
        edited = list(request.messages)
        estimated = self._estimate(request, edited)
        self.max_estimated_tokens = max(self.max_estimated_tokens, estimated)
        if estimated <= self.token_budget:
            return edited, estimated

        tool_indexes = [
            index
            for index, message in enumerate(edited)
            if isinstance(message, ToolMessage)
        ]

        # 优先清理较早结果，最新一次工具结果尽量完整保留给下一次模型判断。
        for index in tool_indexes[:-1]:
            message = edited[index]
            if message.content == TOOL_RESULT_PLACEHOLDER:
                continue
            edited[index] = self._replace_tool_content(
                message,
                TOOL_RESULT_PLACEHOLDER,
                "clear_older_tool_result",
            )
            self.pruned_tool_results += 1
            estimated = self._estimate(request, edited)
            if estimated <= self.token_budget:
                return edited, estimated

        # 单个最新结果仍可能异常巨大；二分保留尽可能多的头尾内容。
        if tool_indexes and estimated > self.token_budget:
            latest_index = tool_indexes[-1]
            latest_message = edited[latest_index]
            original = str(latest_message.content)
            low, high = 0, len(original)
            best_message = self._replace_tool_content(
                latest_message,
                TRUNCATION_MARKER.strip(),
                "truncate_latest_tool_result",
            )
            best_estimate = self._estimate(
                request,
                [*edited[:latest_index], best_message, *edited[latest_index + 1 :]],
            )
            while low <= high:
                middle = (low + high) // 2
                candidate_message = self._replace_tool_content(
                    latest_message,
                    self._truncated_content(original, middle),
                    "truncate_latest_tool_result",
                )
                candidate = [
                    *edited[:latest_index],
                    candidate_message,
                    *edited[latest_index + 1 :],
                ]
                candidate_estimate = self._estimate(request, candidate)
                if candidate_estimate <= self.token_budget:
                    best_message = candidate_message
                    best_estimate = candidate_estimate
                    low = middle + 1
                else:
                    high = middle - 1
            edited[latest_index] = best_message
            estimated = best_estimate
            if best_message.content != original:
                self.truncated_tool_results += 1

        self.budget_overflow = estimated > self.token_budget
        return edited, estimated

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        if any(
            isinstance(message, ToolMessage) and retrieval_unavailable(message.content)
            for message in request.messages
        ):
            raise AgentLoopAborted("Retrieval dependency unavailable")
        self.model_calls += 1
        edited, estimated = self._fit_messages(request)
        self.max_estimated_tokens = max(self.max_estimated_tokens, estimated)
        return await handler(request.override(messages=edited))
