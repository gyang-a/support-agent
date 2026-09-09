"""共享 Embedding 熔断：有限重试、冷却拒绝与串行恢复探测。"""

import asyncio
from time import monotonic
from threading import Lock

from langchain_openai import OpenAIEmbeddings
from config import get_settings


class EmbeddingUnavailable(RuntimeError):
    pass


class EmbeddingCircuit:
    def __init__(self, attempts=3, timeout=5.0, cooldown=60.0, clock=monotonic):
        self.attempts, self.timeout, self.cooldown = attempts, timeout, cooldown
        self.clock = clock
        self.open_until = 0.0
        self.lock = asyncio.Lock()

    async def call(self, operation):
        # 同一服务共享预算，避免多个 Agent 在服务停机时同时重试。
        async with self.lock:
            if self.clock() < self.open_until:
                raise EmbeddingUnavailable("Embedding circuit is open")
            recovering = self.open_until > 0
            for attempt in range(1 if recovering else self.attempts):
                try:
                    result = await asyncio.wait_for(operation(), self.timeout)
                    self.open_until = 0.0
                    return result
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = exc
                    # 参数/鉴权错误继续重试没有意义。
                    code = getattr(exc, "status_code", None)
                    if code is not None and 400 <= code < 500 and code not in {408, 429}:
                        break
            self.open_until = self.clock() + self.cooldown
            raise EmbeddingUnavailable("Embedding retries exhausted") from last_error


_circuits = {}
_registry_lock = Lock()


class GuardedEmbeddings:
    def __init__(self, client, circuit):
        self.client, self.circuit = client, circuit

    async def aembed_query(self, text):
        return await self.circuit.call(lambda: self.client.aembed_query(text))

    async def aembed_documents(self, texts):
        return await self.circuit.call(lambda: self.client.aembed_documents(texts))


def create_embeddings(*, api_key, model, base_url, **kwargs):
    settings = get_settings()
    # API key 仅用于内部隔离，不记录到日志。
    key = (base_url.rstrip("/"), model, api_key)
    with _registry_lock:
        circuit = _circuits.setdefault(key, EmbeddingCircuit(
            attempts=settings.embedding_max_attempts,
            timeout=settings.embedding_timeout,
            cooldown=settings.embedding_circuit_cooldown,
        ))
    client = OpenAIEmbeddings(
        api_key=api_key, model=model, base_url=base_url,
        max_retries=0, request_timeout=settings.embedding_timeout, **kwargs,
    )
    return GuardedEmbeddings(client, circuit)
