import { create } from "zustand";
interface State {
  drafts: Record<string, string>;
  setDraft: (id: string, text: string) => void;
}
export const useChatStore = create<State>((set) => ({
  drafts: {},
  setDraft: (id, text) => set((s) => ({ drafts: { ...s.drafts, [id]: text } })),
}));
