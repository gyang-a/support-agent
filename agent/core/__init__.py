"""核心 Agent 框架组件。

包入口保持轻量，不在导入 ``core.catalog`` 或 ``core.order`` 时提前加载
LangChain、数据库等可选基础设施。公共类型通过惰性属性加载，兼顾导入
便利性与领域层的独立可测试性。
"""

from typing import Any


__all__ = ["AgentOutput", "AgentState", "AgentGraphManager"]


def __getattr__(name: str) -> Any:
    """按需加载原有公共对象，避免无关模块产生重依赖副作用。"""
    if name in {"AgentOutput", "AgentState"}:
        from .workflow.state import AgentOutput, AgentState

        return {"AgentOutput": AgentOutput, "AgentState": AgentState}[name]
    if name == "AgentGraphManager":
        from .workflow.graph_manager import AgentGraphManager

        return AgentGraphManager
    raise AttributeError(f"module 'core' has no attribute {name!r}")
