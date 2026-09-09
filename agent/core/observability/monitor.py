"""运行时质量追踪、低分告警与 LangGraph 监控节点。"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.evaluation.evaluator import evaluate_interaction
from core.workflow.state import AgentState


_RUNTIME_DIR = Path(__file__).resolve().parents[2] / "runtime"
_SENSITIVE_PATTERN = re.compile(
    r"(?:密码|验证码|银行卡|身份证)\s*[:：]?\s*\S+",
    flags=re.IGNORECASE,
)
_USER_ID_PATTERN = re.compile(r"\buser[_-]\d+\b", flags=re.IGNORECASE)


class TraceStore:
    """以 JSONL 追加质量轨迹和告警，便于本地审计与离线统计。"""

    def __init__(
        self,
        trace_file: str | Path = _RUNTIME_DIR / "quality_traces.jsonl",
        alert_file: str | Path = _RUNTIME_DIR / "quality_alerts.jsonl",
    ):
        self.trace_file = Path(trace_file)
        self.alert_file = Path(alert_file)
        self._lock = threading.RLock()

    @staticmethod
    def _redact(value: str) -> str:
        """在落盘前遮蔽显式用户标识和常见敏感字段。"""
        value = _USER_ID_PATTERN.sub("[REDACTED_USER]", value)
        return _SENSITIVE_PATTERN.sub("[REDACTED_SENSITIVE]", value)

    @staticmethod
    def _append(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def write(self, trace: dict[str, Any]) -> None:
        """保存脱敏轨迹；低于阈值或安全失败时同时写告警。"""
        def redact_nested(value):
            if isinstance(value, str):
                return self._redact(value)
            if isinstance(value, dict):
                return {key: redact_nested(item) for key, item in value.items()}
            if isinstance(value, list):
                return [redact_nested(item) for item in value]
            return value
        sanitized = redact_nested(trace)
        with self._lock:
            self._append(self.trace_file, sanitized)
            evaluation = sanitized["evaluation"]
            if not evaluation["passed"]:
                alert = {
                    "trace_id": sanitized["trace_id"],
                    "created_at": sanitized["created_at"],
                    "overall_score": evaluation["scores"]["overall"],
                    "reasons": evaluation["reasons"],
                }
                self._append(self.alert_file, alert)


class QualityMonitorNode:
    """在专业 Agent 执行后评测本轮结果，不修改用户可见消息。"""

    def __init__(self, store: TraceStore | None = None):
        self.store = store or TraceStore()

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        messages = state.get("messages", [])
        user_messages = [message for message in messages if getattr(message, "type", "") == "human"]
        assistant_messages = [
            message for message in messages if getattr(message, "type", "") == "ai"
        ]
        query = str(user_messages[-1].content) if user_messages else ""
        response = str(assistant_messages[-1].content) if assistant_messages else ""
        metadata = dict(state.get("metadata", {}))
        execution = metadata.get("execution", {})
        actual_agent = execution.get("agent_name") or state.get("next_agent", "")
        evaluation = evaluate_interaction(
            query=query,
            response=response,
            actual_agent=actual_agent,
            tool_names=execution.get("tool_names", []),
            tool_errors=execution.get("tool_errors", []),
        )
        task_evaluations = []
        plans = {task["task_id"]: task for task in state.get("tasks", [])}
        for result in state.get("task_results", []):
            task = plans[result["task_id"]]
            task_execution = result.get("metadata", {}).get("execution", {})
            item = evaluate_interaction(
                query=task["query"], response=result["response"],
                actual_agent=result["agent_name"], expected_agent=task["agent_name"],
                tool_names=task_execution.get("tool_names", []),
                tool_errors=task_execution.get("tool_errors", []),
                # 追问不要求已经调用事实工具，但失败/阻塞不能计为业务成功。
                expected_tools=[] if result["status"] != "completed" else None,
            )
            item.update(task_id=result["task_id"], status=result["status"])
            if result["status"] in {"failed", "blocked"}:
                item["passed"] = False
                item["scores"]["overall"] = min(item["scores"]["overall"], 0.5)
                item["reasons"].append(f"任务未完成：{result['status']}")
            task_evaluations.append(item)
        if task_evaluations:
            # 汇总节点不是专业路由；按子问题评估工具，再检查最终答复安全性。
            evaluation = evaluate_interaction(
                query=query, response=response, actual_agent=actual_agent,
                expected_agent=actual_agent, expected_tools=[],
            )
            for key in ("route", "tool", "tool_health", "groundedness", "overall"):
                evaluation["scores"][key] = round(sum(
                    item["scores"][key] for item in task_evaluations
                ) / len(task_evaluations), 4)
            evaluation["passed"] = evaluation["passed"] and all(item["passed"] for item in task_evaluations)
            evaluation["reasons"].extend(
                f"{item['task_id']}: {reason}" for item in task_evaluations for reason in item["reasons"]
            )
            evaluation["tasks"] = task_evaluations
        if state.get("planning_error"):
            evaluation["passed"] = False
            evaluation["reasons"].append("本轮任务规划未完成")
        trace = {
            "trace_id": uuid.uuid4().hex,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "user_hash": hashlib.sha256(
                state.get("user_id", "unknown").encode("utf-8")
            ).hexdigest()[:16],
            "session_hash": hashlib.sha256(
                state.get("session_id", "unknown").encode("utf-8")
            ).hexdigest()[:16],
            "query": query,
            "response": response,
            "route": metadata.get("route", {}),
            "execution": execution,
            "tasks": [
                {"task_id": result["task_id"], "agent_name": result["agent_name"],
                 "status": result["status"], "metadata": result["metadata"]}
                for result in state.get("task_results", [])
            ],
            "evaluation": evaluation,
        }
        try:
            self.store.write(trace)
        except OSError as exc:
            # 监控落盘失败不能阻断用户客服主流程，但必须保留可观测错误。
            metadata["monitoring_error"] = str(exc)
        metadata["quality"] = evaluation
        metadata["trace_id"] = trace["trace_id"]
        return {"metadata": metadata}
