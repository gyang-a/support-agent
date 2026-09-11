export async function apiFetch(path: string, init?: RequestInit) {
  const headers = new Headers(init?.headers);
  headers.set("X-Requested-With", "SupportAgent");
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (response.status === 401 && !path.startsWith("/api/auth/")) {
    window.location.replace("/login");
  }
  return response;
}
