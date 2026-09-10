import {
  Check,
  Loader2,
  Circle,
  CircleAlert,
  Wrench,
  GitBranch,
} from "lucide-react";
import type { Run, TaskData } from "@/features/execution/types";
const names: Record<string, string> = {
  product_agent: "商品咨询助手",
  recommendation_agent: "选购推荐助手",
  order_agent: "订单物流助手",
  after_sales_agent: "售后服务助手",
};
const statuses: Record<string, string> = {
  pending: "等待中",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  needs_input: "等待补充",
  blocked: "依赖阻塞",
  cancelled: "已停止",
};
export function TaskTree({ run }: { run: Run }) {
  const tasks: Record<string, TaskData> = {};
  for (const event of run.events)
    if (event.type.startsWith("task.") && event.data.task_id)
      tasks[event.data.task_id] = {
        ...tasks[event.data.task_id],
        ...event.data,
      } as TaskData;
  for (const task of Object.values(tasks)) {
    if (
      (task.status === "running" || task.status === "pending") &&
      run.status !== "running"
    ) {
      task.status = run.status === "cancelled" ? "cancelled" : "failed";
    }
  }
  return (
    <div>
      <div className="mb-6 rounded-xl border bg-white p-3.5">
        <div className="mb-2 flex items-center gap-2 text-xs font-medium">
          <GitBranch size={14} className="text-primary" />
          本轮执行
        </div>
        <p className="line-clamp-3 text-xs leading-6 text-muted-foreground">
          {run.query}
        </p>
      </div>
      {!Object.keys(tasks).length && (
        <p className="py-4 text-xs leading-6 text-muted-foreground">
          {run.status === "running"
            ? "正在识别需求，等待任务计划…"
            : run.events.some((e) => e.data.cached)
              ? "本轮命中语义缓存，无需执行专业任务。"
              : "本轮没有可展示的专业任务。"}
        </p>
      )}
      {Object.values(tasks).map((task, index) => (
        <div
          key={task.task_id}
          className="relative mb-6 border-l border-[#d8e0d0] pl-5"
        >
          <span className="absolute -left-2.5 top-0 flex size-5 items-center justify-center rounded-full border bg-background text-primary">
            {task.status === "completed" ? (
              <Check size={11} />
            ) : task.status === "running" ? (
              <Loader2 size={11} className="animate-spin" />
            ) : task.status === "pending" ? (
              <Circle size={9} />
            ) : (
              <CircleAlert size={11} />
            )}
          </span>
          <div className="mb-1 flex items-center justify-between gap-2">
            <h3 className="text-xs font-medium">
              {names[task.agent_name] ?? task.agent_name}
            </h3>
            <span className="text-[10px] text-muted-foreground">
              {statuses[task.status] ?? task.status}
            </span>
          </div>
          <p className="mb-3 font-mono text-[9px] text-[#a0a694]">
            TASK {String(index + 1).padStart(2, "0")}
            {task.latency_ms != null &&
              " · " + (task.latency_ms / 1000).toFixed(1) + "s"}
          </p>
          <p className="text-xs leading-6 text-muted-foreground">
            {task.instruction}
          </p>
          {!!task.depends_on?.length && (
            <p className="mt-2 text-[10px] text-muted-foreground">
              依赖：{task.depends_on.join(" → ")}
            </p>
          )}
          {run.events
            .filter(
              (e) =>
                e.type === "tool.completed" && e.data.task_id === task.task_id,
            )
            .map((e) => (
              <div
                key={e.sequence}
                className="mt-2 flex items-start gap-2 rounded-md bg-[#eeefe9] p-2 text-[10px] text-muted-foreground"
              >
                <Wrench size={11} className="mt-0.5 shrink-0" />
                <span className="break-all">
                  {e.data.label}
                  <span className="block text-[9px] opacity-70">
                    执行后摘要
                  </span>
                </span>
              </div>
            ))}
          {task.response && (
            <details className="mt-3 text-[11px] text-muted-foreground">
              <summary>任务答复</summary>
              <p className="mt-2 whitespace-pre-wrap leading-6">
                {task.response}
              </p>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}
