"""带指数退避和备用模型切换的 OpenAI 兼容聊天模型。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, TypeVar

from langchain_openai import ChatOpenAI
from openai import APIConnectionError
from pydantic import PrivateAttr


logger = logging.getLogger(__name__)
T = TypeVar("T")


def _is_retryable(exc: Exception) -> bool:
    """只重试超时、连接失败、限流和服务端错误。"""
    if isinstance(
        exc,
        (TimeoutError, ConnectionError, asyncio.TimeoutError, APIConnectionError),
    ):
        return True
    status_code = getattr(exc, "status_code", None)
    return status_code in {408, 409, 429} or (
        isinstance(status_code, int) and status_code >= 500
    )


class ResilientChatOpenAI(ChatOpenAI):
    """保持 ChatOpenAI 工具绑定能力，同时统一处理重试和模型降级。"""

    _fallback_model: ChatOpenAI | None = PrivateAttr(default=None)
    _retry_count: int = PrivateAttr(default=2)
    _initial_delay: float = PrivateAttr(default=1.0)
    _backoff_multiplier: float = PrivateAttr(default=2.0)
    _max_delay: float = PrivateAttr(default=8.0)

    def configure_resilience(
        self,
        *,
        fallback_model: ChatOpenAI | None,
        retry_count: int,
        initial_delay: float,
        backoff_multiplier: float,
        max_delay: float,
    ) -> "ResilientChatOpenAI":
        """由模型工厂注入退避策略和备用模型。"""
        self._fallback_model = fallback_model
        self._retry_count = max(0, retry_count)
        self._initial_delay = max(0.0, initial_delay)
        self._backoff_multiplier = max(1.0, backoff_multiplier)
        self._max_delay = max(0.0, max_delay)
        return self

    @property
    def fallback_model_name(self) -> str | None:
        """暴露非敏感的备用模型名，便于启动检查和测试。"""
        return self._fallback_model.model_name if self._fallback_model else None

    def _delay_for_retry(self, retry_index: int) -> float:
        return min(
            self._initial_delay * (self._backoff_multiplier ** retry_index),
            self._max_delay,
        )

    def _run_with_retry(self, call: Callable[[], T], model_name: str) -> T:
        for attempt in range(self._retry_count + 1):
            try:
                return call()
            except Exception as exc:
                if attempt >= self._retry_count or not _is_retryable(exc):
                    raise
                delay = self._delay_for_retry(attempt)
                logger.warning(
                    "Model %s failed (%s); retry %d/%d in %.1fs",
                    model_name,
                    type(exc).__name__,
                    attempt + 1,
                    self._retry_count,
                    delay,
                )
                time.sleep(delay)
        raise RuntimeError("unreachable")

    async def _run_with_retry_async(
        self,
        call: Callable[[], Any],
        model_name: str,
    ) -> Any:
        for attempt in range(self._retry_count + 1):
            try:
                return await call()
            except Exception as exc:
                if attempt >= self._retry_count or not _is_retryable(exc):
                    raise
                delay = self._delay_for_retry(attempt)
                logger.warning(
                    "Model %s failed (%s); retry %d/%d in %.1fs",
                    model_name,
                    type(exc).__name__,
                    attempt + 1,
                    self._retry_count,
                    delay,
                )
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable")

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        primary_call = super()._generate
        try:
            return self._run_with_retry(
                lambda: primary_call(
                    messages,
                    stop=stop,
                    run_manager=run_manager,
                    **kwargs,
                ),
                self.model_name,
            )
        except Exception as primary_exc:
            # 参数、鉴权等不可恢复错误必须原样暴露；只有网络、超时、限流和
            # 服务端瞬时错误在主模型重试耗尽后才允许切换备用模型。
            if self._fallback_model is None or not _is_retryable(primary_exc):
                raise
            logger.error(
                "Primary model %s exhausted; switching to fallback model %s",
                self.model_name,
                self._fallback_model.model_name,
            )
            try:
                return self._run_with_retry(
                    lambda: self._fallback_model._generate(
                        messages,
                        stop=stop,
                        run_manager=run_manager,
                        **kwargs,
                    ),
                    self._fallback_model.model_name,
                )
            except Exception as fallback_exc:
                raise fallback_exc from primary_exc

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        primary_call = super()._agenerate
        try:
            return await self._run_with_retry_async(
                lambda: primary_call(
                    messages,
                    stop=stop,
                    run_manager=run_manager,
                    **kwargs,
                ),
                self.model_name,
            )
        except Exception as primary_exc:
            if self._fallback_model is None or not _is_retryable(primary_exc):
                raise
            logger.error(
                "Primary model %s exhausted; switching to fallback model %s",
                self.model_name,
                self._fallback_model.model_name,
            )
            try:
                return await self._run_with_retry_async(
                    lambda: self._fallback_model._agenerate(
                        messages,
                        stop=stop,
                        run_manager=run_manager,
                        **kwargs,
                    ),
                    self._fallback_model.model_name,
                )
            except Exception as fallback_exc:
                raise fallback_exc from primary_exc
