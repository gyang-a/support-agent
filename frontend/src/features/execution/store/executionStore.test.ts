import { it, expect } from "vitest";
import { useExecutionStore } from "./executionStore";
it("routes interleaved events by run and ignores duplicates", () => {
  const state = useExecutionStore.getState();
  for (const id of ["a", "b"])
    state.put({
      id,
      conversation_id: id,
      query: "",
      response: "",
      status: "running",
      events: [],
      created_at: "",
    });
  const event = {
    type: "message.delta",
    sequence: 1,
    timestamp: "",
    data: { content: "A" },
  };
  state.event("a", event);
  state.event("b", { ...event, data: { content: "B" } });
  state.event("a", event);
  expect(useExecutionStore.getState().runs.a.response).toBe("A");
  expect(useExecutionStore.getState().runs.b.response).toBe("B");
  state.select("b", "b");
  state.event("a", {
    type: "run.completed",
    sequence: 2,
    timestamp: "",
    data: {},
  });
  expect(useExecutionStore.getState().selected.b).toBe("b");
  expect(useExecutionStore.getState().runs.a.status).toBe("completed");
});
