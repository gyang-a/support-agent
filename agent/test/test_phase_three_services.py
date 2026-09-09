"""第三阶段售后知识、诊断、工单和质量监控测试。"""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.diagnostics import diagnose_issue
from core.evaluation import build_quality_report, evaluate_interaction
from core.knowledge import TechnicalKnowledgeStore
from core.observability import QualityMonitorNode, TraceStore, invoke_traced_agent
from core.workflow.prompting import history_source_guidance
from core.policy import search_policies
from core.ticket import TicketRepository, TicketValidationError


def test_policy_search_returns_traceable_citations() -> None:
    """售后回答证据必须包含文档、章节与生效日期。"""
    result = search_policies("激活手机还能七天无理由退货吗", limit=2)

    assert result["results"]
    assert result["results"][0]["document_id"] == "POLICY-RETURN-001"
    assert result["results"][0]["citation"]
    assert result["effective_date"] == "2026-08-17"


def test_critical_diagnostic_prioritizes_safety_and_manual_service() -> None:
    """鼓包等严重症状必须优先停止使用并要求人工处理。"""
    result = diagnose_issue("手机充不进电，而且后盖已经鼓包并有异味", category="手机")

    assert result["rule_id"] == "DIAG-BATTERY-SWELLING"
    assert result["severity"] == "critical"
    assert result["manual_required"] is True
    assert any("停止" in step for step in result["safe_steps"])
    assert result["policy_evidence"]


def test_unknown_diagnostic_asks_questions_instead_of_guessing() -> None:
    """规则未覆盖时不得自行确诊。"""
    result = diagnose_issue("它偶尔表现得有一点奇怪", category="手机")

    assert result["status"] == "insufficient_information"
    assert len(result["questions"]) >= 3
    assert result["manual_required"] is False


def test_ticket_repository_enforces_ownership_and_atomic_updates(tmp_path) -> None:
    """创建、查询和升级始终绑定当前用户，公开结果不包含 user_id。"""
    repository = TicketRepository(tmp_path / "tickets.json")
    ticket = repository.create_ticket(
        user_id="user_1001",
        subject="笔记本无法正常开机",
        description="连接原装电源后仍然没有指示灯，也没有进液。",
        order_id="DG-1001-0002",
        sku_id="LAPTOP-LENOVO-XIAOXINPRO14-R7-32-1T",
    )

    assert "user_id" not in ticket
    assert repository.get_ticket_detail("user_1002", ticket["ticket_id"]) is None
    escalated = repository.escalate_ticket(
        "user_1001",
        ticket["ticket_id"],
        "基础排查后仍然无法开机，请人工检测。",
    )
    assert escalated is not None
    assert escalated["manual_required"] is True
    assert escalated["status"] == "processing"


def test_ticket_rejects_sensitive_data_and_cross_user_order(tmp_path) -> None:
    """敏感信息和不属于当前用户的订单不能写入工单。"""
    repository = TicketRepository(tmp_path / "tickets.json")
    with pytest.raises(TicketValidationError):
        repository.create_ticket(
            user_id="user_1001",
            subject="账号登录出现问题",
            description="我的密码: secret123，请客服直接登录检查。",
        )
    with pytest.raises(TicketValidationError):
        repository.create_ticket(
            user_id="user_1002",
            subject="查询其他账号订单售后",
            description="希望对这个不属于我的订单创建维修申请。",
            order_id="DG-1001-0002",
        )


def test_rule_evaluator_checks_route_tools_and_security() -> None:
    """正确路由和工具应通过，内部用户标识泄露必须失败。"""
    passed = evaluate_interaction(
        query="手机电池鼓包怎么办",
        response="请立即停止充电和使用，并联系人工售后。",
        actual_agent="after_sales_agent",
        tool_names=["diagnose_device_issue"],
    )
    failed = evaluate_interaction(
        query="查我的订单",
        response="正在查询 user_1001 的订单。",
        actual_agent="order_agent",
        tool_names=["query_user_orders"],
    )

    assert passed["passed"] is True
    assert failed["passed"] is False
    assert failed["scores"]["security"] == 0


def test_trace_store_redacts_sensitive_values_and_writes_alert(tmp_path) -> None:
    """运行轨迹落盘前必须脱敏，低分结果同时写入告警。"""
    trace_file = tmp_path / "traces.jsonl"
    alert_file = tmp_path / "alerts.jsonl"
    store = TraceStore(trace_file=trace_file, alert_file=alert_file)
    store.write(
        {
            "trace_id": "trace-1",
            "created_at": "2026-08-17T16:00:00+08:00",
            "query": "替 user_1002 查询，验证码: 123456",
            "response": "不能查询 user_1002",
            "evaluation": {
                "passed": False,
                "scores": {"overall": 0.5},
                "reasons": ["安全检查失败"],
            },
        }
    )

    saved = json.loads(trace_file.read_text(encoding="utf-8").strip())
    assert "user_1002" not in saved["query"]
    assert "123456" not in saved["query"]
    assert alert_file.exists()


def test_traced_agent_collects_tool_calls_and_quality_node(tmp_path) -> None:
    """专业 Agent 的工具调用应进入元数据并由质量节点写入轨迹。"""

    class FakeAgent:
        async def ainvoke(self, _inputs, context):
            assert context == "trusted-context"
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "diagnose_device_issue",
                                "args": {"symptom": "电池鼓包"},
                                "id": "call-1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    ToolMessage(
                        content='{"status":"success"}',
                        tool_call_id="call-1",
                        name="diagnose_device_issue",
                    ),
                    AIMessage(content="请立即停止充电和使用，并联系人工售后。"),
                ]
            }

    state = {
        "messages": [HumanMessage(content="手机电池鼓包怎么办")],
        "user_id": "user_1001",
        "session_id": "session-test",
        "memory_context": "",
        "next_agent": "after_sales_agent",
        "metadata": {},
    }
    agent_result = asyncio.run(
        invoke_traced_agent(
            inner_agent=FakeAgent(),
            state=state,
            context="trusted-context",
            agent_name="after_sales_agent",
        )
    )
    monitored_state = {
        **state,
        "messages": [*state["messages"], *agent_result["messages"]],
        "metadata": agent_result["metadata"],
    }
    store = TraceStore(
        trace_file=tmp_path / "traces.jsonl",
        alert_file=tmp_path / "alerts.jsonl",
    )
    monitor_result = asyncio.run(QualityMonitorNode(store)(monitored_state))

    assert agent_result["metadata"]["execution"]["tool_names"] == [
        "diagnose_device_issue"
    ]
    assert agent_result["messages"][-1].name == "after_sales_agent"
    assert monitor_result["metadata"]["quality"]["passed"] is True
    assert (tmp_path / "traces.jsonl").exists()


def test_history_source_guidance_identifies_current_agent_and_requires_refresh() -> None:
    prompt = history_source_guidance("order_agent")

    assert "name=order_agent" in prompt
    assert "其他 Agent 的回答只能用于理解用户指代和对话背景" in prompt
    assert "重新调用工具核验" in prompt


def test_fixed_dataset_route_report_passes(tmp_path) -> None:
    """固定测试集可以在不调用模型的情况下做确定性路由回归。"""
    report = build_quality_report(trace_file=tmp_path / "missing.jsonl")

    assert report["route_regression"]["total"] == 10
    assert report["route_regression"]["pass_rate"] == 1.0
    assert report["runtime_quality"]["trace_count"] == 0


def test_technical_knowledge_store_is_optional_without_embeddings() -> None:
    """未配置 Embedding 时技术文档向量库安全降级。"""
    store = TechnicalKnowledgeStore(
        host="localhost",
        port=19530,
        api_key=None,
        embedding_api_key=None,
        embedding_model="",
        embedding_base_url="",
        embedding_dimension=1536,
    )

    asyncio.run(store.initialize())

    assert store.available is False
