"""按依赖调度独立 Agent 调用，主图状态只由调度节点统一提交。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from time import perf_counter
from typing import Any

from core.workflow.tasks import SpecialistResponse, TaskPlan


class TaskScheduler:
    def __init__(self, agents: dict[str, Any], *, max_concurrency=4, timeout_seconds=None):
        self.agents = agents
        self.max_concurrency = max(1, max_concurrency)
        self.timeout_seconds = timeout_seconds

    async def _execute(self, state, task, dependencies):
        started = perf_counter()
        local = deepcopy(state)
        local.update(
            current_task=deepcopy(task), dependency_results=deepcopy(dependencies),
            next_agent=task["agent_name"], metadata={}, task_results=[],
        )
        result = {
            "task_id": task["task_id"], "agent_name": task["agent_name"],
            "status": "failed", "response": "该事项暂未完成，请稍后重试。",
            "data": {}, "metadata": {},
        }
        try:
            output = await asyncio.wait_for(
                self.agents[task["agent_name"]](local), timeout=self.timeout_seconds
            )
            # 不从自然语言猜测完成状态，也不默认把追问当作完成。
            structured = SpecialistResponse.model_validate(output["task_output"])
            result.update(structured.model_dump())
            result["metadata"] = output.get("metadata", {})
            # 摘要只来自未追加子任务消息的原始历史，游标仍对应主图 messages。
            context_update = {
                "conversation_summary": output.get("conversation_summary", state.get("conversation_summary", "")),
                "summarized_message_count": output.get("summarized_message_count", state.get("summarized_message_count", 0)),
            }
        except Exception as exc:
            result["metadata"] = {"error_type": type(exc).__name__}
            context_update = {}
        result["metadata"]["task_latency_ms"] = round((perf_counter() - started) * 1000, 2)
        return result, context_update

    async def __call__(self, state):
        if state.get("planning_error"):
            return {}
        try:
            plan = TaskPlan.model_validate({"tasks": state.get("tasks", [])})
        except ValueError:
            return {"planning_error": "暂时无法完整处理这次请求，请分别说明需要查询或办理的事项。"}
        tasks = [task.model_dump() for task in plan.tasks]
        pending = {task["task_id"]: task for task in tasks}
        results = {}
        running: dict[asyncio.Task, str] = {}
        context_update = {}
        started = perf_counter()
        try:
            while pending or running:
                for task_id, task in list(pending.items()):
                    deps = task["depends_on"]
                    if any(dep in results and results[dep]["status"] != "completed" for dep in deps):
                        results[task_id] = {
                            "task_id": task_id, "agent_name": task["agent_name"],
                            "status": "blocked", "response": "前置事项尚未完成，该事项暂未执行。",
                            "data": {}, "metadata": {"blocked_by": [
                                dep for dep in deps if dep in results and results[dep]["status"] != "completed"
                            ]},
                        }
                        del pending[task_id]
                    elif all(dep in results for dep in deps) and len(running) < self.max_concurrency:
                        future = asyncio.create_task(self._execute(
                            state, task, [results[dep] for dep in deps]
                        ))
                        running[future] = task_id
                        del pending[task_id]
                if not running:
                    # 被阻塞的下游可能排在前置任务之前，下一轮继续传播阻塞。
                    continue
                done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for future in done:
                    task_id = running.pop(future)
                    result, summary = future.result()
                    results[task_id] = result
                    if summary.get("summarized_message_count", -1) > context_update.get("summarized_message_count", -1):
                        context_update = summary
        finally:
            # 取消请求时清理未结束的工具/模型调用，避免后台孤儿任务。
            for future in running:
                future.cancel()
            if running:
                await asyncio.gather(*running, return_exceptions=True)
        ordered = [results[task["task_id"]] for task in tasks]
        metadata = dict(state.get("metadata", {}))
        executions = [r["metadata"].get("execution", {}) for r in ordered]
        metadata["execution"] = {
            "agent_name": tasks[0]["agent_name"] if len(tasks) == 1 else "task_orchestrator",
            "agent_names": list(dict.fromkeys(task["agent_name"] for task in tasks)),
            "tool_names": [name for execution in executions for name in execution.get("tool_names", [])],
            "tool_errors": [name for execution in executions for name in execution.get("tool_errors", [])],
            "tool_call_count": sum(execution.get("tool_call_count", 0) for execution in executions),
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "task_count": len(tasks),
        }
        return {"task_results": ordered, "metadata": metadata, **context_update}
