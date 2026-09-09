"""模型指数退避与备用模型切换测试。"""

import asyncio

import httpx
import pytest
from langchain_openai import ChatOpenAI
from openai import APIConnectionError

from core.model.resilient import ResilientChatOpenAI, _is_retryable


class _ServiceUnavailable(Exception):
    status_code = 503


def _model() -> ResilientChatOpenAI:
    return ResilientChatOpenAI(
        api_key="test-key",
        model="primary-model",
        base_url="https://example.invalid/v1",
        max_retries=0,
    ).configure_resilience(
        fallback_model=None,
        retry_count=2,
        initial_delay=1,
        backoff_multiplier=2,
        max_delay=10,
    )


def test_exponential_backoff_retries_transient_errors(monkeypatch) -> None:
    model = _model()
    calls = 0
    delays: list[float] = []

    def operation():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _ServiceUnavailable()
        return "ok"

    monkeypatch.setattr("core.model.resilient.time.sleep", delays.append)

    assert model._run_with_retry(operation, "primary-model") == "ok"
    assert calls == 3
    assert delays == [1, 2]


def test_non_retryable_error_fails_without_delay(monkeypatch) -> None:
    model = _model()
    delays: list[float] = []
    monkeypatch.setattr("core.model.resilient.time.sleep", delays.append)

    with pytest.raises(ValueError):
        model._run_with_retry(lambda: (_ for _ in ()).throw(ValueError()), "model")

    assert delays == []
    assert _is_retryable(ValueError()) is False
    assert _is_retryable(
        APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
    ) is True


def test_async_retry_uses_same_backoff(monkeypatch) -> None:
    model = _model()
    calls = 0
    delays: list[float] = []

    async def operation():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError()
        return "ok"

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("core.model.resilient.asyncio.sleep", fake_sleep)

    result = asyncio.run(model._run_with_retry_async(operation, "primary-model"))

    assert result == "ok"
    assert delays == [1, 2]


def test_async_generation_switches_to_fallback_after_primary_failure(
    monkeypatch,
) -> None:
    """主模型失败后，绑定工具前的底层生成调用会切换备用模型。"""
    model = _model()
    # 指数退避次数已由独立测试覆盖；这里隔离验证耗尽后的模型切换。
    model._retry_count = 0
    model._fallback_model = ChatOpenAI(
        api_key="fallback-key",
        model="fallback-model",
        base_url="https://fallback.example.invalid/v1",
        max_retries=0,
    )
    calls: list[str] = []

    async def fake_agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        calls.append(self.model_name)
        if self.model_name == "primary-model":
            raise _ServiceUnavailable("primary unavailable")
        return "fallback-result"

    monkeypatch.setattr(ChatOpenAI, "_agenerate", fake_agenerate)

    result = asyncio.run(model._agenerate([]))

    assert result == "fallback-result"
    assert calls == ["primary-model", "fallback-model"]


def test_non_retryable_generation_error_does_not_switch_model(monkeypatch) -> None:
    """400/鉴权/参数类错误应直接暴露，不能被备用模型掩盖。"""
    model = _model()
    model._fallback_model = ChatOpenAI(
        api_key="fallback-key",
        model="fallback-model",
        base_url="https://fallback.example.invalid/v1",
        max_retries=0,
    )
    calls: list[str] = []

    async def fake_agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        calls.append(self.model_name)
        raise ValueError("invalid request")

    monkeypatch.setattr(ChatOpenAI, "_agenerate", fake_agenerate)

    with pytest.raises(ValueError, match="invalid request"):
        asyncio.run(model._agenerate([]))

    assert calls == ["primary-model"]
