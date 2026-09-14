"""旧规划器名称的兼容入口；与统一 Orchestrator 使用同一实现。"""

from agents.orchestrator import OrchestratorAgent


class TaskPlannerAgent(OrchestratorAgent):
    """兼容既有导入；主图不再单独注册或调用此节点。"""
