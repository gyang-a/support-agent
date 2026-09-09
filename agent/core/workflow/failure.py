"""不可恢复的本轮任务失败，直接结束工具循环。"""

import json
from langchain_core.messages import AIMessage

UNAVAILABLE_RESPONSE = "暂时无法查询所需资料，这次查询已停止。请稍后重试。"


class AgentLoopAborted(RuntimeError):
    pass


class RetrievalStalled(AgentLoopAborted):
    """服务可用，但模型在检索停止后仍尝试重复调用。"""


def retrieval_unavailable(content):
    try:
        payload = json.loads(content)
        return isinstance(payload, dict) and payload.get("status") == "knowledge_unavailable"
    except (ValueError, TypeError):
        return False


def failed_task(agent_name, tool_names=None, *, response=UNAVAILABLE_RESPONSE, reason="dependency_unavailable"):
    return {
        "messages": [AIMessage(content=response, name=agent_name)],
        "task_output": {"status": "failed", "response": response, "data": {}},
        "metadata": {"execution": {
            "agent_name": agent_name, "tool_names": tool_names or [],
            "tool_call_count": len(tool_names or []),
            "tool_errors": tool_names or [reason],
            "termination_reason": reason,
        }},
    }
