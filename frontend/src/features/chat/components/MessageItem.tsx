import { lazy, Suspense, useState } from "react";
import { Copy, Check, Workflow, Loader2 } from "lucide-react";
import { Brand } from "@/shared/components/Brand";
const MarkdownRenderer = lazy(
  () => import("@/shared/components/MarkdownRenderer"),
);
import { Button } from "@/shared/ui/button";
import type { Run } from "@/features/execution/types";
export function MessageItem({
  run,
  selected,
  onInspect,
}: {
  run: Run;
  selected: boolean;
  onInspect: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(run.response);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopyError(true);
    }
  }
  return (
    <article className="mb-10">
      <div className="mb-8 flex justify-end">
        <div className="max-w-[88%] whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-[#edf0e7] px-5 py-3 text-[13px] leading-7">
          {run.query}
        </div>
      </div>
      <div className="mb-3 flex items-center gap-2">
        <Brand compact />
        <span className="text-xs font-medium">Support Agent</span>
        <span className="ml-1 rounded border px-1.5 py-0.5 text-[9px] text-muted-foreground">
          协作回答
        </span>
      </div>
      <div className="pl-1">
        {run.response ? (
          <Suspense
            fallback={
              <p className="whitespace-pre-wrap text-sm">{run.response}</p>
            }
          >
            <MarkdownRenderer content={run.response} />
          </Suspense>
        ) : run.status === "running" ? (
          <p
            role="status"
            className="flex items-center gap-2 py-3 text-xs text-muted-foreground"
          >
            <Loader2 className="animate-spin" size={14} />
            正在协作处理你的问题…
          </p>
        ) : (
          <p className="py-3 text-xs text-muted-foreground">
            本轮尚未生成回答。
          </p>
        )}
      </div>
      {run.error && (
        <p
          role="alert"
          className="my-3 rounded-lg bg-red-50 p-3 text-xs text-destructive"
        >
          {run.error}
        </p>
      )}
      {run.status === "cancelled" && (
        <p className="my-3 text-xs text-muted-foreground">已停止本轮执行</p>
      )}
      <div className="mt-3 flex items-center gap-1">
        <Button
          variant={selected ? "secondary" : "ghost"}
          size="sm"
          className="h-7 text-[10px]"
          onClick={onInspect}
        >
          <Workflow size={12} />
          查看执行详情
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          aria-label="复制回答"
          disabled={!run.response}
          onClick={() => void copy()}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
        </Button>
        {copyError && (
          <span className="text-xs text-destructive">无法访问剪贴板</span>
        )}
      </div>
    </article>
  );
}
