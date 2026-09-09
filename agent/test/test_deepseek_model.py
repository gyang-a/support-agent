"""DeepSeek 模型工厂与 LangGraph 主图构建测试。"""

from config import get_settings
from core.model import create_chat_model
from core.workflow.graph_manager import AgentGraphManager


def _configure_deepseek(monkeypatch) -> None:
    """为不访问外网的构造测试提供临时 DeepSeek 配置。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    get_settings.cache_clear()


def test_deepseek_model_factory(monkeypatch) -> None:
    """模型工厂必须使用 DeepSeek 地址和当前有效模型名。"""
    _configure_deepseek(monkeypatch)

    model = create_chat_model()

    assert model.model_name == "deepseek-v4-flash"
    assert str(model.openai_api_base).rstrip("/") == "https://api.deepseek.com"
    assert model.fallback_model_name == "deepseek-chat"
    get_settings.cache_clear()


def test_graph_builds_with_deepseek_model(monkeypatch) -> None:
    """完整数码客服主图可以在不发起模型请求的情况下完成构建。"""
    _configure_deepseek(monkeypatch)

    graph = AgentGraphManager().build_graph()
    nodes = set(graph.get_graph().nodes)

    assert {
        "orchestrator",
        "task_planner",
        "task_scheduler",
        "synthesis",
        "quality_monitor",
    }.issubset(nodes)
    get_settings.cache_clear()
