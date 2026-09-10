import type { TraceEvent } from "../types";
export function parseEvent(value: unknown): TraceEvent {
  if (!value || typeof value !== "object") throw new Error("无效执行事件");
  const event = value as Record<string, unknown>;
  if (
    typeof event.type !== "string" ||
    typeof event.sequence !== "number" ||
    typeof event.timestamp !== "string" ||
    !event.data ||
    typeof event.data !== "object"
  )
    throw new Error("执行事件格式不兼容");
  return event as unknown as TraceEvent;
}
