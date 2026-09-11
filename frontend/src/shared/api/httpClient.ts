import { apiFetch } from "./apiFetch";
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch("/api/workspace" + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : "服务暂时不可用，请检查后端连接",
    );
  }
  return response.json() as Promise<T>;
}
