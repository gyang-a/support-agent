import { create } from "zustand";
import type { Conversation } from "../types";
import { conversationApi } from "../api/conversationApi";
interface State {
  items: Conversation[];
  error: string;
  loading: boolean;
  load: () => Promise<void>;
  create: (title: string) => Promise<Conversation>;
  rename: (id: string, title: string) => Promise<void>;
}
export const useConversationStore = create<State>((set) => ({
  items: [],
  error: "",
  loading: false,
  load: async () => {
    set({ loading: true, error: "" });
    try {
      set({ items: await conversationApi.list() });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : "加载失败" });
    } finally {
      set({ loading: false });
    }
  },
  create: async (title) => {
    const item = await conversationApi.create(title);
    set((s) => ({ items: [item, ...s.items] }));
    return item;
  },
  rename: async (id, title) => {
    const item = await conversationApi.rename(id, title);
    set((s) => ({ items: s.items.map((c) => (c.id === id ? item : c)) }));
  },
}));
