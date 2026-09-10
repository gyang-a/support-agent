import { useEffect, useState } from "react";
import { runApi } from "../api/runApi";
import { useExecutionStore } from "../store/executionStore";
import { hasActiveRun } from "../services/runController";

export function useRunHistory(conversationId?: string) {
  const [result, setResult] = useState({ id: "", error: "" });
  useEffect(() => {
    if (!conversationId) return;
    let ignore = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const history = await runApi.history(conversationId!);
        if (ignore) return;
        for (const run of history) {
          const existing = useExecutionStore.getState().runs[run.id];
          if (!existing || !hasActiveRun(conversationId!))
            useExecutionStore.getState().put(run);
        }
        setResult({ id: conversationId!, error: "" });
        // A reloaded tab has no local stream. Poll only until the server settles its run.
        if (
          history.some((run) => run.status === "running") &&
          !hasActiveRun(conversationId!)
        )
          timer = setTimeout(load, 2000);
      } catch (error) {
        if (!ignore)
          setResult({
            id: conversationId!,
            error: error instanceof Error ? error.message : "加载失败",
          });
      }
    }
    void load();
    return () => {
      ignore = true;
      clearTimeout(timer);
    };
  }, [conversationId]);
  return {
    loading: !!conversationId && result.id !== conversationId,
    historyError: result.id === conversationId ? result.error : "",
  };
}
