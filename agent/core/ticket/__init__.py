"""用户隔离的售后工单领域服务。"""

from .repository import (
    TicketRepository,
    TicketValidationError,
    create_ticket,
    escalate_ticket,
    get_ticket_detail,
    list_user_tickets,
)

__all__ = [
    "TicketRepository",
    "TicketValidationError",
    "create_ticket",
    "escalate_ticket",
    "get_ticket_detail",
    "list_user_tickets",
]
