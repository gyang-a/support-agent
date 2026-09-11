"""混合召回后的候选重排与去重。"""

from __future__ import annotations

import re
import logging
import threading
import math
import httpx
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


_LATIN_TERM = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]*")
_CHINESE_RUN = re.compile(r"[\u3400-\u9fff]+")
logger = logging.getLogger(__name__)


def _terms(text: str) -> set[str]:
    """同时保留型号词和中文二元词，弥补简单空格分词对中文的不足。"""

    terms = {term.casefold() for term in _LATIN_TERM.findall(text)}
    for run in _CHINESE_RUN.findall(text):
        terms.add(run)
        terms.update(run[index : index + 2] for index in range(max(0, len(run) - 1)))
    return terms


def _model_tokens(value: str) -> tuple[str, ...]:
    """把自然型号或 snake_case 元数据拆成可比较的英文数字序列。"""

    return tuple(re.findall(r"[a-z0-9]+", value.casefold()))


def _resolved_models(query: str, candidates: list[dict[str, Any]]) -> set[str]:
    """从问题与召回候选共同确认用户明确提到的商品型号。

    同一起点命中 ``airbook 14`` 与 ``airbook 14 pro`` 时只保留最长
    型号；若问题在不同位置分别提到多个型号，则视为对比问题并全部保留。
    """

    query_tokens = _model_tokens(query)
    model_tokens = {
        str(candidate.get("product_model", "")): _model_tokens(
            str(candidate.get("product_model", "")).replace("_", " ")
        )
        for candidate in candidates
        if candidate.get("product_model")
    }
    occurrences: dict[str, set[int]] = {}
    for model, tokens in model_tokens.items():
        if not tokens:
            continue
        positions = {
            index
            for index in range(len(query_tokens) - len(tokens) + 1)
            if query_tokens[index : index + len(tokens)] == tokens
        }
        if positions:
            occurrences[model] = positions

    resolved: set[str] = set()
    for model, positions in occurrences.items():
        tokens = model_tokens[model]
        # 短型号只有在至少一个出现位置未被更长型号覆盖时才独立成立。
        has_unshadowed_occurrence = any(
            not any(
                other != model
                and position in other_positions
                and len(model_tokens[other]) > len(tokens)
                and model_tokens[other][: len(tokens)] == tokens
                for other, other_positions in occurrences.items()
            )
            for position in positions
        )
        if has_unshadowed_occurrence:
            resolved.add(model)
    return resolved


class CandidateReranker(Protocol):
    """后续可替换为 BGE/CrossEncoder 的最小重排接口。"""

    def rerank(self, query: str, candidates: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        ...


class SiliconFlowReranker:
    """Remote scoring with the same scope filtering and public result contract."""

    def __init__(self, *, api_key: str | None, base_url: str, model_name: str,
                 timeout: float = 15, allow_heuristic_fallback: bool = True,
                 enforce_model_scope: bool = True):
        self.api_key, self.base_url, self.model_name = api_key, base_url, model_name
        self.timeout = timeout
        self.allow_heuristic_fallback = allow_heuristic_fallback
        self.enforce_model_scope = enforce_model_scope
        self.fallback = MetadataAwareReranker(enforce_model_scope=enforce_model_scope)

    def rerank(self, query, candidates, *, limit):
        prepared = _prepare_candidates(query, candidates, enforce_model_scope=self.enforce_model_scope)
        if not prepared:
            return []
        try:
            if not self.api_key:
                raise ValueError("RERANKER_API_KEY is required")
            response = httpx.post(
                self.base_url.rstrip("/") + "/rerank",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model_name, "query": query,
                      "documents": [_passage(item) for item in prepared],
                      "top_n": min(max(1, limit), len(prepared)), "return_documents": False},
                timeout=self.timeout,
            )
            response.raise_for_status()
            results = response.json()["results"]
            rescored, seen = [], set()
            for item in results:
                index, score = item["index"], float(item["relevance_score"])
                if type(index) is not int or not 0 <= index < len(prepared) or index in seen or not math.isfinite(score):
                    raise ValueError("Invalid rerank response")
                seen.add(index)
                rescored.append({**prepared[index], "rerank_score": score,
                                 "reranker_mode": "siliconflow_api", "reranker_model": self.model_name})
            if len(rescored) != min(max(1, limit), len(prepared)):
                raise ValueError("Incomplete rerank response")
            return sorted(rescored, key=lambda item: item["rerank_score"], reverse=True)
        except Exception as exc:
            if not self.allow_heuristic_fallback:
                raise
            # Do not log request headers, candidate contents, or provider error bodies.
            logger.warning("Rerank API unavailable (%s); using heuristic fallback", type(exc).__name__)
            return [{**item, "reranker_model": self.model_name, "reranker_error": "api_unavailable"}
                    for item in self.fallback.rerank(query, prepared, limit=limit)]


def create_reranker(settings, *, enforce_model_scope=True):
    if settings.reranker_provider == "siliconflow":
        return SiliconFlowReranker(
            api_key=settings.reranker_api_key, base_url=settings.reranker_base_url,
            model_name=settings.reranker_model, timeout=settings.reranker_timeout,
            allow_heuristic_fallback=settings.reranker_allow_heuristic_fallback,
            enforce_model_scope=enforce_model_scope,
        )
    return BgeCrossEncoderReranker(
        model_name=settings.reranker_model, device=settings.reranker_device,
        batch_size=settings.reranker_batch_size, max_length=settings.reranker_max_length,
        cache_dir=settings.reranker_cache_dir,
        allow_heuristic_fallback=settings.reranker_allow_heuristic_fallback,
        enforce_model_scope=enforce_model_scope,
    )


def _prepare_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    enforce_model_scope: bool = True,
) -> list[dict[str, Any]]:
    """执行与相关性模型无关的适用范围约束和稳定去重。"""

    resolved_models = _resolved_models(query, candidates)
    prepared: list[dict[str, Any]] = []
    seen_hashes: set[tuple[str, str]] = set()
    for candidate in candidates:
        if (
            enforce_model_scope
            and resolved_models
            and str(candidate.get("product_model", "")) not in resolved_models
        ):
            continue
        document_id = str(candidate.get("document_id", ""))
        content_hash = str(candidate.get("content_hash", ""))
        dedupe_key = (document_id, content_hash)
        if content_hash and dedupe_key in seen_hashes:
            continue
        seen_hashes.add(dedupe_key)
        prepared.append(candidate)
    return prepared


def _passage(candidate: dict[str, Any]) -> str:
    """把结构元数据与正文组合成 Cross-Encoder 的 passage。"""

    lines = [
        f"标题：{candidate.get('title', '')}",
        f"型号：{candidate.get('product_model', '')}",
        f"类目：{candidate.get('category', '')}",
        f"章节：{candidate.get('section_path') or candidate.get('section', '')}",
        f"正文：{candidate.get('content', '')}",
    ]
    return "\n".join(line for line in lines if not line.endswith("："))


@dataclass(slots=True)
class MetadataAwareReranker:
    """无外部模型时使用的确定性二阶段重排器。"""

    # 二阶段应允许精确术语覆盖纠正第一阶段名次，因此不让 RRF 名次占绝对优势。
    hybrid_weight: float = 0.15
    content_weight: float = 0.40
    heading_weight: float = 0.15
    metadata_weight: float = 0.20
    phrase_weight: float = 0.10
    enforce_model_scope: bool = True

    def rerank(self, query: str, candidates: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        query_terms = _terms(query)
        normalized_query = re.sub(r"\s+", "", query).casefold()
        rescored: list[dict[str, Any]] = []

        for rank, candidate in enumerate(
            _prepare_candidates(
                query, candidates, enforce_model_scope=self.enforce_model_scope
            )
        ):

            content_terms = _terms(str(candidate.get("content", "")))
            heading_text = " ".join(
                str(candidate.get(field, "")) for field in ("title", "section_path")
            )
            heading_terms = _terms(heading_text)
            metadata_text = " ".join(
                str(candidate.get(field, "")).replace("_", " ")
                for field in ("product_model", "category", "document_type")
            )
            metadata_terms = _terms(metadata_text)
            denominator = max(1, len(query_terms))
            content_coverage = len(query_terms & content_terms) / denominator
            heading_coverage = len(query_terms & heading_terms) / denominator
            metadata_coverage = len(query_terms & metadata_terms) / denominator
            phrase_match = float(
                bool(normalized_query)
                and normalized_query in re.sub(r"\s+", "", str(candidate.get("content", ""))).casefold()
            )
            # 使用名次而不是 Milvus 原始分数，避免稠密、BM25 与 RRF 分值量纲不同。
            hybrid_rank_score = 1.0 / (rank + 1)
            rerank_score = (
                self.hybrid_weight * hybrid_rank_score
                + self.content_weight * content_coverage
                + self.heading_weight * heading_coverage
                + self.metadata_weight * metadata_coverage
                + self.phrase_weight * phrase_match
            )
            rescored.append(
                {
                    **candidate,
                    "hybrid_rank": rank + 1,
                    "rerank_score": round(rerank_score, 6),
                    "reranker_mode": "heuristic_fallback",
                }
            )

        rescored.sort(key=lambda item: item["rerank_score"], reverse=True)
        return rescored[: max(1, limit)]


class BgeCrossEncoderReranker:
    """使用 BAAI BGE Cross-Encoder 对 query-passage 候选进行二阶段重排。"""

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        device: str = "auto",
        batch_size: int = 4,
        max_length: int = 512,
        cache_dir: Path | None = None,
        allow_heuristic_fallback: bool = True,
        enforce_model_scope: bool = True,
    ) -> None:
        self.model_name = model_name
        self.device_name = device
        self.batch_size = max(1, batch_size)
        self.max_length = max(64, max_length)
        self.cache_dir = cache_dir
        self.allow_heuristic_fallback = allow_heuristic_fallback
        self.enforce_model_scope = enforce_model_scope
        self.fallback = MetadataAwareReranker(enforce_model_scope=enforce_model_scope)
        self._load_lock = threading.Lock()
        self._tokenizer: Any = None
        self._model: Any = None
        self._torch: Any = None
        self._device = "cpu"

    def _ensure_loaded(self) -> None:
        """延迟且只加载一次模型，避免每次客服查询重复占用内存。"""

        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            resolved_device = self.device_name
            if resolved_device == "auto":
                resolved_device = "cuda" if torch.cuda.is_available() else "cpu"
            cache_dir = str(self.cache_dir) if self.cache_dir else None
            tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                cache_dir=cache_dir,
            )
            model = AutoModelForSequenceClassification.from_pretrained(
                self.model_name,
                cache_dir=cache_dir,
            )
            model.eval()
            model.to(resolved_device)
            self._torch = torch
            self._tokenizer = tokenizer
            self._model = model
            self._device = resolved_device
            logger.info("BGE reranker ready: model=%s device=%s", self.model_name, resolved_device)

    def _model_rerank(
        self, query: str, candidates: list[dict[str, Any]], *, limit: int
    ) -> list[dict[str, Any]]:
        self._ensure_loaded()
        prepared = _prepare_candidates(
            query,
            candidates,
            enforce_model_scope=self.enforce_model_scope,
        )
        if not prepared:
            return []
        logits: list[float] = []
        for start in range(0, len(prepared), self.batch_size):
            batch = prepared[start : start + self.batch_size]
            pairs = [[query, _passage(candidate)] for candidate in batch]
            inputs = self._tokenizer(
                pairs,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            inputs = {name: tensor.to(self._device) for name, tensor in inputs.items()}
            with self._torch.inference_mode():
                scores = self._model(**inputs, return_dict=True).logits.view(-1).float().cpu()
            logits.extend(float(score) for score in scores)

        rescored = []
        for candidate, logit in zip(prepared, logits, strict=True):
            probability = float(self._torch.sigmoid(self._torch.tensor(logit)))
            rescored.append(
                {
                    **candidate,
                    "rerank_logit": round(logit, 6),
                    "rerank_score": round(probability, 6),
                    "reranker_mode": "bge_cross_encoder",
                    "reranker_model": self.model_name,
                }
            )
        rescored.sort(key=lambda item: item["rerank_logit"], reverse=True)
        return rescored[: max(1, limit)]

    def rerank(
        self, query: str, candidates: list[dict[str, Any]], *, limit: int
    ) -> list[dict[str, Any]]:
        try:
            return self._model_rerank(query, candidates, limit=limit)
        except Exception:
            if not self.allow_heuristic_fallback:
                raise
            logger.exception("BGE reranker unavailable; using explicit heuristic fallback")
            fallback = self.fallback.rerank(query, candidates, limit=limit)
            return [
                {
                    **item,
                    "reranker_model": self.model_name,
                    "reranker_error": "model_unavailable",
                }
                for item in fallback
            ]
