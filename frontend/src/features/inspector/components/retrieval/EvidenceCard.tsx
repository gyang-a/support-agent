import { FileText } from "lucide-react";
import type { Evidence } from "@/features/execution/types";
export function EvidenceCard({
  evidence,
  index,
  stage,
}: {
  evidence: Evidence;
  index: number;
  stage?: string;
}) {
  return (
    <details className="group rounded-xl border bg-white p-3.5">
      <summary className="cursor-pointer list-none">
        <div className="flex items-start gap-2">
          <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-[#edf1e8] text-[10px] text-primary">
            {index + 1}
          </span>
          <div className="min-w-0">
            <p className="text-xs font-medium leading-5">
              {evidence.title || evidence.document_id || "知识文档"}
            </p>
            <p className="mt-1 text-[10px] text-muted-foreground">
              {evidence.product_model || "通用文档"}
            </p>
          </div>
        </div>
        <p className="mt-3 line-clamp-3 text-xs leading-6 text-muted-foreground group-open:hidden">
          {evidence.excerpt}
        </p>
        <div className="mt-3 flex items-center justify-between text-[9px] text-[#839575]">
          <span className="flex items-center gap-1">
            <FileText size={10} />
            {stage === "tool_result" ? "工具返回片段" : "模型上下文片段"}
          </span>
          <span className="group-open:hidden">展开原文 ↓</span>
          <span className="hidden group-open:inline">收起原文 ↑</span>
        </div>
      </summary>
      <div className="mt-3 border-t pt-3">
        <p className="whitespace-pre-wrap text-xs leading-6">
          {evidence.excerpt}
        </p>
        <p className="mt-3 break-all text-[10px] text-muted-foreground">
          {evidence.citation || evidence.section}
        </p>
        {typeof evidence.rerank_score === "number" && (
          <p className="mt-2 font-mono text-[10px] text-muted-foreground">
            重排分数 {evidence.rerank_score.toFixed(4)}
          </p>
        )}
      </div>
    </details>
  );
}
