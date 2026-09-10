"""收集专业 Agent 的工具调用与耗时元数据。"""

from __future__ import annotations

from time import perf_counter
from typing import Any
from langchain_core.messages import AIMessage

from core.memory.context_window import prepare_agent_context
from core.workflow.state import AgentState
from core.workflow.tasks import SpecialistResponse
from core.workflow.failure import AgentLoopAborted, RetrievalStalled, failed_task


async def invoke_traced_agent(
    inner_agent: Any,
    state: AgentState,
    context: Any,
    agent_name: str,
    summary_model: Any | None = None,
    system_prompt: str = "",
    tools: list[Any] | None = None,
    tool_context_guard: Any | None = None,
) -> dict[str, Any]:
    """调用 LangChain Agent，并仅把最终答复与审计元数据返回主图。"""
    prepared = await prepare_agent_context(
        messages=state["messages"],
        summary_model=summary_model,
        existing_summary=state.get("conversation_summary", ""),
        summarized_message_count=state.get("summarized_message_count", 0),
        system_prompt=system_prompt,
        tools=tools or [],
    )
    started_at = perf_counter()
    try:
        result = await inner_agent.ainvoke(
            {"messages": prepared.messages},
            context=context,
        )
    except RetrievalStalled:
        return failed_task(
            agent_name, response="重复查询没有获得更多资料，本次检索已停止，尚未形成可靠的完整答复。请补充具体功能场景或转人工确认。",
            reason="retrieval_no_progress",
        )
    except AgentLoopAborted:
        return failed_task(agent_name)
    latency_ms = round((perf_counter() - started_at) * 1000, 2)
    tool_names: list[str] = []
    tool_errors: list[str] = []

    for message in result.get("messages", []):
        for tool_call in getattr(message, "tool_calls", []) or []:
            name = tool_call.get("name", "unknown")
            if name != "SpecialistResponse":
                tool_names.append(name)
        if getattr(message, "type", "") == "tool" and getattr(message, "status", "") == "error":
            tool_errors.append(getattr(message, "name", "unknown"))

    from core.observability.live_events import emit
    for name in tool_names:
        emit("tool.completed", label=name, status="failed" if name in tool_errors else "completed", stage="summary")
    metadata = dict(state.get("metadata", {}))
    metadata["execution"] = {
        "agent_name": agent_name,
        "tool_names": tool_names,
        "tool_call_count": len(tool_names),
        "tool_errors": tool_errors,
        "latency_ms": latency_ms,
    }
    metadata["context"] = {
        "estimated_input_tokens": prepared.estimated_tokens,
        "compressed": prepared.compressed,
        "retained_rounds": prepared.retained_rounds,
        "budget_overflow": prepared.budget_overflow or bool(
            getattr(tool_context_guard, "budget_overflow", False)
        ),
        "max_internal_model_tokens": getattr(
            tool_context_guard, "max_estimated_tokens", prepared.estimated_tokens
        ),
        "pruned_tool_results": getattr(tool_context_guard, "pruned_tool_results", 0),
        "truncated_tool_results": getattr(
            tool_context_guard, "truncated_tool_results", 0
        ),
    }
    task_output = None
    if state.get("current_task"):
        task_output = SpecialistResponse.model_validate(result["structured_response"]).model_dump()
        final_message = AIMessage(content=task_output["response"], name=agent_name)
    else:
        final_message = result["messages"][-1].model_copy(update={"name": agent_name})
    return {
        # 共享历史只保留最终答复，并用稳定的图节点名标记来源。
        "messages": [final_message],
        "conversation_summary": prepared.summary,
        "summarized_message_count": prepared.summarized_message_count,
        "metadata": metadata,
        **({"task_output": task_output} if task_output is not None else {}),
    }
