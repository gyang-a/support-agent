import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { WorkspacePage } from "@/pages/workspace/WorkspacePage";
import { LoginPage } from "@/pages/login/LoginPage";
import { useEffect, useState } from "react";
import { useAuth } from "@/features/auth/auth";
import { apiFetch } from "@/shared/api/apiFetch";

function AccountGate() {
  const user = useAuth((s) => s.user);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    void (async () => {
      try {
        const response = await apiFetch("/api/auth/me");
        if (response.status !== 401 && !response.ok)
          throw new Error("暂时无法连接账号服务");
        const user = response.ok ? await response.json() : null;
        if (!disposed) {
          useAuth.setState({ user });
          setReady(true);
        }
      } catch {
        if (!disposed) setError("暂时无法连接账号服务，请稍后重试。");
      }
    })();
    return () => {
      disposed = true;
    };
  }, []);
  if (error)
    return (
      <main className="flex min-h-dvh flex-col items-center justify-center gap-4">
        <p role="alert">{error}</p>
        <button onClick={() => window.location.reload()} className="underline">
          重试连接
        </button>
      </main>
    );
  if (!ready)
    return (
      <main className="flex min-h-dvh items-center justify-center text-sm text-muted-foreground">
        正在加载工作台…
      </main>
    );
  return user ? <WorkspacePage /> : <Navigate to="/login" replace />;
}
export default function App() {
  useEffect(() => {
    const changed = (event: StorageEvent) => {
      if (event.key === "support-auth-change") window.location.reload();
    };
    const restored = (event: PageTransitionEvent) => {
      if (event.persisted) window.location.reload();
    };
    window.addEventListener("storage", changed);
    window.addEventListener("pageshow", restored);
    return () => {
      window.removeEventListener("storage", changed);
      window.removeEventListener("pageshow", restored);
    };
  }, []);
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<AccountGate />} />
        <Route path="/chat/:conversationId" element={<AccountGate />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
