"""固定测试集路由检查与运行轨迹汇总报告。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.routing import keyword_fallback


_DATASET_FILE = Path(__file__).resolve().parents[2] / "data" / "evaluation_cases.json"
_TRACE_FILE = Path(__file__).resolve().parents[2] / "runtime" / "quality_traces.jsonl"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """容忍空行读取 JSONL；文件不存在时返回空列表。"""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def build_quality_report(
    dataset_file: str | Path = _DATASET_FILE,
    trace_file: str | Path = _TRACE_FILE,
) -> dict[str, Any]:
    """生成离线路由回归和线上轨迹指标摘要。"""
    with Path(dataset_file).open("r", encoding="utf-8") as file:
        dataset = json.load(file)
    route_results = [
        {
            "case_id": case["case_id"],
            "expected_agent": case["expected_agent"],
            "actual_agent": keyword_fallback(case["query"]),
            "passed": keyword_fallback(case["query"]) == case["expected_agent"],
        }
        for case in dataset["cases"]
    ]
    traces = _load_jsonl(Path(trace_file))
    passed_traces = sum(1 for trace in traces if trace["evaluation"]["passed"])
    average_score = (
        round(
            sum(trace["evaluation"]["scores"]["overall"] for trace in traces) / len(traces),
            4,
        )
        if traces
        else None
    )
    return {
        "dataset_version": dataset["dataset_version"],
        "route_regression": {
            "total": len(route_results),
            "passed": sum(1 for result in route_results if result["passed"]),
            "pass_rate": round(
                sum(1 for result in route_results if result["passed"]) / len(route_results),
                4,
            ),
            "failures": [result for result in route_results if not result["passed"]],
        },
        "runtime_quality": {
            "trace_count": len(traces),
            "passed": passed_traces,
            "pass_rate": round(passed_traces / len(traces), 4) if traces else None,
            "average_score": average_score,
        },
    }
