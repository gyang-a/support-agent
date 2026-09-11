import { request } from "@/shared/api/httpClient";
import { apiFetch } from "@/shared/api/apiFetch";
import type { Run } from "../types";
export const runApi = {
  history: (id: string) =>
    request<Run[]>("/conversations/" + encodeURIComponent(id) + "/runs"),
  cancel: (id: string) =>
    request<{ ok: boolean }>("/runs/" + encodeURIComponent(id) + "/cancel", {
      method: "POST",
    }),
  stream: (id: string, conversationId: string, query: string) =>
    apiFetch("/api/workspace/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, conversation_id: conversationId, query }),
    }),
};
