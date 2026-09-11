import httpx
import pytest

from core.knowledge.reranking import SiliconFlowReranker


def reranker(**kwargs):
    return SiliconFlowReranker(api_key="test-key", base_url="https://api.siliconflow.cn/v1", model_name="BAAI/bge-reranker-v2-m3", **kwargs)


def test_remote_indices_map_back_to_filtered_candidates(monkeypatch):
    candidates = [
        {"chunk_id": "a", "content": "first", "product_model": "airbook_14", "document_id": "doc-a"},
        {"chunk_id": "b", "content": "second", "product_model": "airbook_14", "document_id": "doc-b"},
        {"chunk_id": "c", "content": "wrong model", "product_model": "airbook_14_pro"},
    ]
    def post(url, **kwargs):
        assert url == "https://api.siliconflow.cn/v1/rerank"
        assert kwargs["headers"]["Authorization"] == "Bearer test-key"
        assert len(kwargs["json"]["documents"]) == 2
        return httpx.Response(200, request=httpx.Request("POST", url), json={"results": [{"index": 1, "relevance_score": .95}, {"index": 0, "relevance_score": .2}]})
    monkeypatch.setattr(httpx, "post", post)
    results = reranker().rerank("airbook 14", candidates, limit=2)
    assert [item["document_id"] for item in results] == ["doc-b", "doc-a"]
    assert all(item["reranker_mode"] == "siliconflow_api" for item in results)


@pytest.mark.parametrize("results", [[], [{"index": 99, "relevance_score": .8}], [{"index": 0, "relevance_score": float("inf")}], [{"index": 0, "relevance_score": .8}, {"index": 0, "relevance_score": .5}]])
def test_invalid_provider_response_falls_back_without_losing_metadata(monkeypatch, results):
    def post(url, **kwargs):
        response = httpx.Response(200, request=httpx.Request("POST", url))
        response.json = lambda: {"results": results}
        return response
    monkeypatch.setattr(httpx, "post", post)
    result = reranker().rerank("usb", [{"chunk_id": "a", "content": "usb", "document_id": "manual"}], limit=1)
    assert result[0]["document_id"] == "manual"
    assert result[0]["reranker_error"] == "api_unavailable"


def test_api_timeout_is_bounded_and_no_local_model_is_loaded(monkeypatch):
    def post(*args, **kwargs):
        assert kwargs["timeout"] == 15
        raise httpx.ReadTimeout("timeout")
    monkeypatch.setattr(httpx, "post", post)
    result = reranker().rerank("usb", [{"content": "usb"}], limit=1)
    assert result[0]["reranker_mode"] == "heuristic_fallback"
    with pytest.raises(httpx.ReadTimeout):
        reranker(allow_heuristic_fallback=False).rerank("usb", [{"content": "usb"}], limit=1)
