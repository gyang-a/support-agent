import { create } from "zustand";
import type { Run, TraceEvent } from "../types";
interface State {
  runs: Record<string, Run>;
  selected: Record<string, string>;
  put: (run: Run) => void;
  select: (conversationId: string, runId: string) => void;
  event: (id: string, event: TraceEvent) => void;
  fail: (id: string, error: string) => void;
}
export const useExecutionStore = create<State>((set) => ({
  runs: {},
  selected: {},
  put: (run) =>
    set((s) => ({
      runs: {
        ...s.runs,
        [run.id]: {
          ...run,
          error:
            run.error ?? run.events.findLast((e) => e.data.error)?.data.error,
        },
      },
    })),
  select: (conversationId, runId) =>
    set((s) => ({ selected: { ...s.selected, [conversationId]: runId } })),
  event: (id, event) =>
    set((s) => {
      const run = s.runs[id];
      if (!run || run.events.some((e) => e.sequence === event.sequence))
        return s;
      const status =
        event.type === "run.completed"
          ? "completed"
          : event.type === "run.failed"
            ? "failed"
            : event.type === "run.cancelled"
              ? "cancelled"
              : run.status;
      return {
        runs: {
          ...s.runs,
          [id]: {
            ...run,
            status,
            response:
              run.response +
              (event.type === "message.delta"
                ? (event.data.content ?? "")
                : ""),
            error: event.data.error ?? run.error,
            events: [...run.events, event],
          },
        },
      };
    }),
  fail: (id, error) =>
    set((s) =>
      s.runs[id]
        ? {
            runs: {
              ...s.runs,
              [id]: { ...s.runs[id], status: "failed", error },
            },
          }
        : s,
    ),
}));
