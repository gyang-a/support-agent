import { useState } from "react";
import { ArrowRight, MessageSquare, ShieldCheck } from "lucide-react";
import { Brand } from "@/shared/components/Brand";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { authRequest, notifyAccountChange } from "@/features/auth/auth";

export function LoginPage() {
  const [register, setRegister] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError("");
    try {
      await authRequest(register ? "register" : "login", {
        username,
        password,
      });
      notifyAccountChange();
      window.location.replace("/");
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "暂时无法连接，请稍后重试",
      );
      setPending(false);
    }
  }
  return (
    <main className="flex min-h-dvh items-center justify-center bg-[#f2f3ed] p-5 sm:p-10">
      <div className="grid w-full max-w-4xl overflow-hidden rounded-3xl border bg-background shadow-sm md:grid-cols-2">
        <section className="flex flex-col justify-between bg-[#e6ebde] p-8 sm:p-12">
          <Brand />
          <div className="hidden py-10 md:block md:py-20">
            <span className="mb-6 inline-flex rounded-2xl border border-primary/15 p-3 text-primary">
              <MessageSquare size={28} strokeWidth={1.5} />
            </span>
            <h1 className="text-3xl font-medium leading-snug tracking-tight">
              每一个问题，
              <br />
              都有专属的对话。
            </h1>
            <p className="mt-5 text-sm leading-7 text-muted-foreground">
              从选购建议到使用答疑，
              <br />
              让数码智能客服陪你找到答案。
            </p>
          </div>
          <p className="hidden items-center gap-2 text-xs text-muted-foreground md:flex">
            <ShieldCheck size={15} />
            你的会话和聊天记录独立保存
          </p>
        </section>
        <section aria-labelledby="login-title" className="p-8 sm:p-12">
          <p className="mb-3 text-xs tracking-widest text-muted-foreground">
            你的客服工作台
          </p>
          <h2 id="login-title" className="text-2xl font-medium">
            {register ? "创建账号" : "欢迎回来"}
          </h2>
          <p className="mb-8 mt-3 text-sm text-muted-foreground">
            {register
              ? "只需用户名和密码，即可开始。"
              : "登录，继续上次的对话。"}
          </p>
          <form onSubmit={submit} className="space-y-5">
            <div className="space-y-2">
              <label htmlFor="username" className="text-sm">
                用户名
              </label>
              <Input
                id="username"
                name="username"
                autoComplete="username"
                required
                minLength={3}
                maxLength={32}
                pattern="[A-Za-z0-9_]+"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                aria-describedby="username-help"
                className="h-11"
              />
              <p id="username-help" className="text-xs text-muted-foreground">
                3–32 位字母、数字或下划线，不区分大小写
              </p>
            </div>
            <div className="space-y-2">
              <label htmlFor="password" className="text-sm">
                密码
              </label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete={register ? "new-password" : "current-password"}
                required
                minLength={8}
                maxLength={128}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-describedby="password-help"
                className="h-11"
              />
              <p id="password-help" className="text-xs text-muted-foreground">
                8–128 位字符
              </p>
            </div>
            {error && (
              <p
                role="alert"
                className="rounded-lg bg-red-50 p-3 text-sm text-destructive"
              >
                {error}
              </p>
            )}
            <Button type="submit" disabled={pending} className="h-11 w-full">
              {pending ? "正在处理…" : register ? "注册并进入" : "登录工作台"}
              <ArrowRight size={16} />
            </Button>
          </form>
          <p className="mt-6 text-center text-sm text-muted-foreground">
            {register ? "已有账号？" : "还没有账号？"}
            <button
              type="button"
              disabled={pending}
              onClick={() => {
                setRegister(!register);
                setError("");
              }}
              className="ml-2 rounded text-primary underline underline-offset-4 focus-visible:outline-2"
            >
              {register ? "去登录" : "创建账号"}
            </button>
          </p>
        </section>
      </div>
    </main>
  );
}
