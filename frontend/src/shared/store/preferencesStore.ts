import { create } from "zustand";
import { persist } from "zustand/middleware";
interface Preferences {
  inspectorOpen: boolean;
  toggleInspector: () => void;
}
export const usePreferences = create<Preferences>()(
  persist(
    (set) => ({
      inspectorOpen: true,
      toggleInspector: () => set((s) => ({ inspectorOpen: !s.inspectorOpen })),
    }),
    {
      name: "support-agent-ui",
      partialize: (s) => ({ inspectorOpen: s.inspectorOpen }),
    },
  ),
);
