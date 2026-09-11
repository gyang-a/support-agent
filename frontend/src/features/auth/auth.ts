import { create } from "zustand";
import { apiFetch } from "@/shared/api/apiFetch";

export interface User {
  id: string;
  username: string;
}
export const useAuth = create<{ user: User | null }>(() => ({ user: null }));

export function notifyAccountChange() {
  try {
    localStorage.setItem("support-auth-change", String(Date.now()));
  } catch {
    /* Storage can be disabled. */
  }
}

export async function authRequest(path: string, body?: object): Promise<User> {
  const response = await apiFetch(
    "/api/auth/" + path,
    body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  const data = await response.json().catch(() => ({}));
  if (response.status === 429) throw new Error("操作太频繁，请稍后再试");
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string" ? data.detail : "请检查用户名和密码格式",
    );
  return data;
}

export async function logout() {
  const response = await apiFetch("/api/auth/logout", { method: "POST" });
  if (!response.ok) throw new Error("退出失败，请重试");
  notifyAccountChange();
  // A fresh document also closes SSE and clears every account's in-memory store.
  window.location.replace("/login");
}
