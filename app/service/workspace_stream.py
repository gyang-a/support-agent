"""SSE execution bridge. A disconnected client cancels child tasks and persists the trace."""
import asyncio
import json
import logging
from app.service.chat_service import stream_chat
from app.service.workspace_store import DEMO_USER, now, save_run
from core.observability.live_events import event_sink

logger = logging.getLogger(__name__)
active: dict[str, asyncio.Task] = {}
active_conversations: set[str] = set()


async def execute_stream(run_id, conversation_id, query):
    queue = asyncio.Queue()
    run = dict(id=run_id, conversation_id=conversation_id, query=query, response="", status="running", events=[], created_at=now())
    sequence = 0

    def emit(kind, data):
        nonlocal sequence
        sequence += 1
        event = dict(type=kind, sequence=sequence, timestamp=now(), data=data)
        run["events"].append(event)
        queue.put_nowait(event)

    async def execute():
        token = event_sink.set(emit)
        try:
            await save_run(run)
            emit("run.started", dict(label="正在理解需求并安排任务"))
            async for frame in stream_chat(query, DEMO_USER, conversation_id):
                if not frame.startswith("data:"):
                    continue
                data = json.loads(frame[5:].strip())
                if data.get("content"):
                    run["response"] += data["content"]
                    emit("message.delta", dict(content=data["content"]))
                if data.get("done"):
                    run["status"] = "completed"
                    emit("run.completed", dict(cached=data.get("cached", False)))
        except asyncio.CancelledError:
            run["status"] = "cancelled"
            emit("run.cancelled", dict(label="执行已停止"))
        except Exception:
            logger.exception("Workspace run failed")
            run["status"] = "failed"
            emit("run.failed", dict(error="本轮执行失败，请检查后端服务后重试。"))
        finally:
            event_sink.reset(token)
            # Once final persistence starts, repeated stop requests must not cancel it.
            active.pop(run_id, None)
            persistence = asyncio.create_task(save_run(run))
            try:
                try:
                    await asyncio.shield(persistence)
                except asyncio.CancelledError:
                    # A browser disconnect can cancel this worker while it is committing.
                    await persistence
            except Exception:
                logger.exception("Unable to persist workspace run")
                emit("run.failed", dict(error="执行记录保存失败，请检查数据库。"))
            finally:
                active_conversations.discard(conversation_id)
                queue.put_nowait(None)

    worker = asyncio.create_task(execute())
    active[run_id] = worker
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if event is None:
                break
            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
    finally:
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
