"""Request-scoped public execution events; no raw arguments or private tool output."""
from contextvars import ContextVar
from typing import Callable

event_sink: ContextVar[Callable | None] = ContextVar("workspace_event_sink", default=None)
task_identity: ContextVar[dict | None] = ContextVar("workspace_task_identity", default=None)


def emit(event_type: str, **data) -> None:
    sink = event_sink.get()
    if sink is not None:
        sink(event_type, {**(task_identity.get() or {}), **data})
