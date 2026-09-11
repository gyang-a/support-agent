import { consumeSSE } from "@/shared/api/sseClient";
import { useExecutionStore } from "../store/executionStore";
import { parseEvent } from "../api/eventAdapter";
import { runApi } from "../api/runApi";
import { newRunId } from "@/shared/lib/uuid";
const active = new Set<string>();
export function hasActiveRun(conversationId: string) {
  return active.has(conversationId);
}
export async function startRun(conversationId: string, query: string) {
  if (active.has(conversationId)) return;
  active.add(conversationId);
  const id = newRunId();
  const store = useExecutionStore.getState();
  store.put({
    id,
    conversation_id: conversationId,
    query,
    response: "",
    status: "running",
    events: [],
    created_at: new Date().toISOString(),
  });
  store.select(conversationId, id);
  try {
    await consumeSSE(await runApi.stream(id, conversationId, query), (data) =>
      useExecutionStore.getState().event(id, parseEvent(data)),
    );
    if (useExecutionStore.getState().runs[id]?.status === "running")
      throw new Error("连接已断开，未收到完成事件。请刷新查看服务端结果。");
  } catch (error) {
    useExecutionStore
      .getState()
      .fail(id, error instanceof Error ? error.message : "请求失败");
  } finally {
    active.delete(conversationId);
  }
}
