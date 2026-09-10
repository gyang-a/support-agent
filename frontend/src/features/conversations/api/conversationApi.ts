import { request } from "@/shared/api/httpClient";
import type { Conversation } from "../types";
export const conversationApi = {
  list: () => request<Conversation[]>("/conversations"),
  create: (title: string) =>
    request<Conversation>("/conversations", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  rename: (id: string, title: string) =>
    request<Conversation>("/conversations/" + encodeURIComponent(id), {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
};
