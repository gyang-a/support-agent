"""基于 DeepSeek OpenAI 兼容接口创建 LangChain ChatModel。"""

from langchain_openai import ChatOpenAI

from config import get_settings
from .resilient import ResilientChatOpenAI


def create_chat_model(temperature: float = 0.1) -> ResilientChatOpenAI:
    """创建供 ``langchain.agents.create_agent`` 使用的 DeepSeek 模型。

    ``create_agent`` 负责 Agent 循环与工具编排，模型供应商仍需显式配置。
    DeepSeek 提供 OpenAI 兼容 Chat Completions 接口，因此继续使用
    ``ChatOpenAI`` 适配器，但 API Key、模型名和地址全部来自 DeepSeek 配置。
    """
    settings = get_settings()
    # SDK 内建重试无法统一控制退避参数，也无法切换模型，因此关闭后交由
    # ResilientChatOpenAI 在模型调用最底层统一处理。
    primary = ResilientChatOpenAI(
        api_key=settings.deepseek_api_key,
        model=settings.deepseek_model,
        base_url=settings.deepseek_base_url,
        temperature=temperature,
        timeout=settings.deepseek_timeout,
        max_retries=0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    fallback: ChatOpenAI | None = None
    if (
        settings.deepseek_fallback_model
        and settings.deepseek_fallback_model != settings.deepseek_model
    ):
        fallback = ChatOpenAI(
            api_key=settings.deepseek_fallback_api_key or settings.deepseek_api_key,
            model=settings.deepseek_fallback_model,
            base_url=(
                settings.deepseek_fallback_base_url or settings.deepseek_base_url
            ),
            temperature=temperature,
            timeout=settings.deepseek_timeout,
            extra_body={"thinking": {"type": "disabled"}},
            max_retries=0,
        )
    return primary.configure_resilience(
        fallback_model=fallback,
        retry_count=settings.deepseek_max_retries,
        initial_delay=settings.deepseek_retry_initial_delay,
        backoff_multiplier=settings.deepseek_retry_multiplier,
        max_delay=settings.deepseek_retry_max_delay,
    )
