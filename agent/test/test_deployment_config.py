from core.mcp.config import load_mcp_connections
from core.cache import SemanticAnswerCache
from core.knowledge.milvus_store import TechnicalKnowledgeStore


def test_mcp_inherits_application_environment_but_not_unrelated_secrets(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-embedding")
    monkeypatch.setenv("RERANKER_API_KEY", "test-reranking")
    monkeypatch.setenv("MYSQL_URL", "mysql+asyncmy://example")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-forward")
    env = load_mcp_connections()["digital_commerce"]["env"]
    assert env["MYSQL_URL"] == "mysql+asyncmy://example"
    assert env["RERANKER_API_KEY"] == "test-reranking"
    assert env["EMBEDDING_API_KEY"] == "test-embedding"
    assert "UNRELATED_SECRET" not in env


def test_provider_namespace_selects_explicit_migration_target():
    kwargs = dict(host="localhost", port=19530, api_key=None, embedding_api_key=None,
                  embedding_model="", embedding_base_url="", embedding_dimension=1024)
    for cls in (SemanticAnswerCache, TechnicalKnowledgeStore):
        old = cls(**kwargs)
        new = cls(**kwargs, embedding_namespace="sf_qwen3_06b_v1")
        assert new.collection == old.collection + "_sf_qwen3_06b_v1"
