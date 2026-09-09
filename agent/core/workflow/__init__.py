"""多智能体系统的工作流编排。

状态类型与图管理器采用惰性加载，避免 Agent 导入 ``workflow.state`` 时，
包入口提前加载 GraphManager 并形成循环依赖。
"""

from typing import Any


__all__ = ["AgentOutput", "AgentState", "AgentGraphManager"]


def __getattr__(name: str) -> Any:
    """按需加载工作流公共对象，同时保留原有包级导入接口。"""
    if name in {"AgentOutput", "AgentState"}:
        from .state import AgentOutput, AgentState

        return {"AgentOutput": AgentOutput, "AgentState": AgentState}[name]
    if name == "AgentGraphManager":
        from .graph_manager import AgentGraphManager

        return AgentGraphManager
    raise AttributeError(f"module 'core.workflow' has no attribute {name!r}")
