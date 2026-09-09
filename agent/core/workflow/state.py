from typing import Annotated, Any, NotRequired, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from core.workflow.tasks import TaskResult, merge_task_results

class AgentState(TypedDict):
    """
    LangGraph 全局状态。
    负责在 Router、各个子 Agent 以及 Memory 之间传递信息。
    """
    # 使用 LangGraph 1.x 的 add_messages reducer：既支持追加新消息，也能按
    # message id 更新已有消息，并自动把消息字典反序列化为 BaseMessage。
    messages: Annotated[list[BaseMessage], add_messages]
    
    # 决定下一步走向哪个节点的路由标记
    next_agent: str
    
    # 用户信息，用于鉴权和记忆隔离
    user_id: str
    session_id: str
    
    # 注入的记忆信息 (长短期记忆提取出的背景上下文)
    memory_context: str

    # 会话级滚动摘要只用于模型上下文组装；完整 messages 始终保留。
    conversation_summary: NotRequired[str]
    summarized_message_count: NotRequired[int]
    
    # 工具调用的附带信息或元数据
    metadata: dict[str, Any]

    # 本轮任务与结果；Router 用 None 显式清空结果 reducer。
    tasks: NotRequired[list[dict[str, Any]]]
    task_results: Annotated[list[TaskResult], merge_task_results]
    planning_error: NotRequired[str]
    # 以下字段仅存在于专业 Agent 的私有调用视图，不合并回共享主图。
    current_task: NotRequired[dict[str, Any]]
    dependency_results: NotRequired[list[TaskResult]]

class AgentOutput(TypedDict):
    """Agent 执行的标准输出格式。"""
    response: str
    tool_calls: list[dict[str, Any]]
    metadata: dict[str, Any]
