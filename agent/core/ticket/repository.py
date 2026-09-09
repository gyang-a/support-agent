"""本地售后工单仓储与人工升级流程。

所有读取和写入都同时校验当前 user_id。JSON 文件仅用于第三阶段演示；生产
环境应替换为具备事务、审计和并发控制的工单数据库或 CRM 接口。
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.catalog import get_product_by_sku
from core.order import get_order_detail


_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "digital_tickets.json"
_SENSITIVE_PATTERN = re.compile(
    r"(?:密码|验证码|银行卡|cvv|身份证)\s*[:：]?\s*\S+",
    flags=re.IGNORECASE,
)


class TicketValidationError(ValueError):
    """工单输入、商品或订单归属不合法。"""


class TicketRepository:
    """提供线程安全、原子写入的本地工单仓储。"""

    def __init__(self, storage_file: str | Path = _DATA_FILE):
        self.storage_file = Path(storage_file)
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        """读取工单文件；测试仓储不存在时创建空内存结构。"""
        if not self.storage_file.exists():
            return {"schema_version": "3.0.0", "tickets": []}
        with self.storage_file.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _save(self, payload: dict[str, Any]) -> None:
        """通过同目录临时文件替换，避免中断时留下半个 JSON。"""
        self.storage_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.storage_file.with_suffix(f"{self.storage_file.suffix}.tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        temporary.replace(self.storage_file)

    @staticmethod
    def _public(ticket: dict[str, Any]) -> dict[str, Any]:
        """移除内部用户身份字段后再返回给 Agent。"""
        return {key: value for key, value in ticket.items() if key != "user_id"}

    @staticmethod
    def _now() -> str:
        """生成带秒精度的本地审计时间。"""
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def list_user_tickets(self, user_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """查询当前用户的最近工单。"""
        with self._lock:
            tickets = [item for item in self._load()["tickets"] if item["user_id"] == user_id]
        tickets.sort(key=lambda item: item["created_at"], reverse=True)
        safe_limit = max(1, min(int(limit), 20))
        return [self._public(item) for item in tickets[:safe_limit]]

    def get_ticket_detail(self, user_id: str, ticket_id: str) -> dict[str, Any] | None:
        """按用户和工单号联合查询；越权与不存在统一返回空。"""
        normalized = ticket_id.strip().upper()
        with self._lock:
            ticket = next(
                (
                    item
                    for item in self._load()["tickets"]
                    if item["user_id"] == user_id and item["ticket_id"].upper() == normalized
                ),
                None,
            )
        return self._public(ticket) if ticket else None

    def create_ticket(
        self,
        user_id: str,
        subject: str,
        description: str,
        category: str = "device_issue",
        order_id: str = "",
        sku_id: str = "",
        priority: str = "normal",
        manual_required: bool = False,
    ) -> dict[str, Any]:
        """校验归属和敏感信息后创建售后工单。"""
        if not user_id or user_id == "unknown":
            raise TicketValidationError("缺少可信用户身份，不能创建售后工单。")
        clean_subject = subject.strip()
        clean_description = description.strip()
        if len(clean_subject) < 4 or len(clean_description) < 8:
            raise TicketValidationError("请提供至少 4 个字的主题和 8 个字的故障描述。")
        if _SENSITIVE_PATTERN.search(f"{clean_subject} {clean_description}"):
            raise TicketValidationError("工单内容疑似包含密码、验证码或证件等敏感信息，请删除后重试。")

        order = get_order_detail(user_id, order_id) if order_id else None
        if order_id and not order:
            raise TicketValidationError("未找到该订单，请确认订单号属于当前登录账号。")
        product = get_product_by_sku(sku_id) if sku_id else None
        if sku_id and not product:
            raise TicketValidationError("未找到该 SKU，请先确认商品型号。")
        if order and sku_id and sku_id not in {item["sku_id"] for item in order["items"]}:
            raise TicketValidationError("该 SKU 不属于所提供的订单。")

        safe_priority = priority if priority in {"low", "normal", "high", "critical"} else "normal"
        if manual_required and safe_priority in {"low", "normal"}:
            safe_priority = "high"
        now = self._now()
        ticket = {
            "ticket_id": f"AS-{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}",
            "user_id": user_id,
            "order_id": order["order_id"] if order else "",
            "sku_id": product["sku_id"] if product else "",
            "category": category.strip() or "device_issue",
            "subject": clean_subject,
            "description": clean_description,
            "status": "processing" if manual_required else "open",
            "priority": safe_priority,
            "manual_required": bool(manual_required),
            "created_at": now,
            "updated_at": now,
            "timeline": [
                {"time": now, "event": "created", "description": "售后工单已创建"}
            ],
        }
        if manual_required:
            ticket["timeline"].append(
                {"time": now, "event": "escalated", "description": "已自动升级人工售后"}
            )

        with self._lock:
            payload = self._load()
            payload["tickets"].append(ticket)
            self._save(payload)
        return self._public(ticket)

    def escalate_ticket(
        self,
        user_id: str,
        ticket_id: str,
        reason: str,
    ) -> dict[str, Any] | None:
        """将当前用户的未关闭工单升级给人工售后。"""
        normalized = ticket_id.strip().upper()
        clean_reason = reason.strip()
        if len(clean_reason) < 4:
            raise TicketValidationError("请提供明确的人工升级原因。")
        if _SENSITIVE_PATTERN.search(clean_reason):
            raise TicketValidationError("升级原因包含疑似敏感信息，请删除后重试。")

        with self._lock:
            payload = self._load()
            ticket = next(
                (
                    item
                    for item in payload["tickets"]
                    if item["user_id"] == user_id and item["ticket_id"].upper() == normalized
                ),
                None,
            )
            if not ticket:
                return None
            if ticket["status"] in {"closed", "cancelled"}:
                raise TicketValidationError("已关闭或已取消的工单不能直接升级，请创建新工单。")
            now = self._now()
            ticket["manual_required"] = True
            ticket["priority"] = "high" if ticket["priority"] != "critical" else "critical"
            ticket["status"] = "processing"
            ticket["updated_at"] = now
            ticket["timeline"].append(
                {"time": now, "event": "escalated", "description": clean_reason}
            )
            self._save(payload)
            return self._public(ticket)


_repository = TicketRepository()


def list_user_tickets(user_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """使用默认仓储查询当前用户工单。"""
    return _repository.list_user_tickets(user_id=user_id, limit=limit)


def get_ticket_detail(user_id: str, ticket_id: str) -> dict[str, Any] | None:
    """使用默认仓储查询当前用户工单详情。"""
    return _repository.get_ticket_detail(user_id=user_id, ticket_id=ticket_id)


def create_ticket(**kwargs: Any) -> dict[str, Any]:
    """使用默认仓储创建工单。"""
    return _repository.create_ticket(**kwargs)


def escalate_ticket(**kwargs: Any) -> dict[str, Any] | None:
    """使用默认仓储升级工单。"""
    return _repository.escalate_ticket(**kwargs)
