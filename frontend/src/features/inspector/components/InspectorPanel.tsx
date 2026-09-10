import {
  Network,
  X,
  ScanSearch,
  Layers,
  ArrowDown,
  Database,
} from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/shared/ui/tabs";
import { Button } from "@/shared/ui/button";
import type { Run } from "@/features/execution/types";
import { TaskTree } from "./trace/TaskTree";
import { EvidenceCard } from "./retrieval/EvidenceCard";
export function InspectorPanel({
  run,
  onClose,
}: {
  run?: Run;
  onClose: () => void;
}) {
  const retrievals =
    run?.events.filter((e) => e.type === "retrieval.completed") ?? [];
  return (
    <aside aria-label="执行详情" className="flex h-full flex-col bg-[#f8f9f4]">
      <div className="flex h-[73px] shrink-0 items-center justify-between border-b px-5">
        <div className="flex items-center gap-2 text-[13px] font-medium">
          <Network size={15} className="text-[#718368]" />
          执行详情
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          aria-label="收起执行详情"
          onClick={onClose}
        >
          <X size={14} />
        </Button>
      </div>
      <Tabs defaultValue="trace" className="flex min-h-0 flex-1 flex-col gap-0">
        <div className="px-5 pt-5">
          <TabsList className="grid h-9 w-full grid-cols-2 bg-[#ebede5]">
            <TabsTrigger value="trace" className="text-xs">
              任务轨迹
            </TabsTrigger>
            <TabsTrigger value="evidence" className="text-xs">
              检索证据
              {retrievals.length > 0 && (
                <span className="ml-1 text-[10px]">
                  {retrievals.reduce(
                    (n, e) => n + (e.data.results?.length ?? 0),
                    0,
                  )}
                </span>
              )}
            </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent
          value="trace"
          className="min-h-0 flex-1 overflow-y-auto p-5"
        >
          {run ? (
            <TaskTree run={run} />
          ) : (
            <div className="pt-8">
              <p className="text-[10px] font-medium tracking-[.15em] text-[#909b84]">
                每个答案，都有迹可循
              </p>
              <h2 className="mt-3 text-lg font-medium">看看助手如何协作</h2>
              <p className="mt-3 text-xs leading-6 text-muted-foreground">
                发送问题后，在这里查看任务的分配、执行状态与结果。
              </p>
              <div className="mt-9 space-y-2">
                {[
                  {
                    icon: Network,
                    title: "理解与规划",
                    text: "识别需求，拆解为专业任务",
                  },
                  {
                    icon: Layers,
                    title: "多助手协作",
                    text: "独立任务并行，有序处理依赖",
                  },
                  {
                    icon: ScanSearch,
                    title: "有据可查的回答",
                    text: "查看检索证据与来源",
                  },
                ].map(({ icon: Icon, title, text }, i) => (
                  <div key={title}>
                    {i > 0 && (
                      <ArrowDown
                        size={12}
                        className="my-3 ml-4 text-[#bac3b1]"
                      />
                    )}
                    <div className="flex gap-3 rounded-xl border border-[#e3e7dc] bg-white/60 p-3">
                      <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[#edf0e6]">
                        <Icon size={15} className="text-[#849475]" />
                      </span>
                      <div>
                        <p className="text-xs font-medium">{title}</p>
                        <p className="mt-1 text-[10px] leading-5 text-muted-foreground">
                          {text}
                        </p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </TabsContent>
        <TabsContent
          value="evidence"
          className="min-h-0 flex-1 overflow-y-auto p-5"
        >
          {retrievals.length ? (
            retrievals.map((event) => (
              <section key={event.sequence} className="mb-6 space-y-3">
                <p className="text-[10px] text-muted-foreground">
                  {event.data.label}
                </p>
                <p className="text-xs leading-6">{event.data.query}</p>
                {event.data.results?.map((evidence, i) => (
                  <EvidenceCard key={i} evidence={evidence} index={i} />
                ))}
              </section>
            ))
          ) : (
            <div className="py-14 text-center">
              <Database size={26} className="mx-auto mb-4 text-[#a1af95]" />
              <p className="text-sm">暂无检索证据</p>
              <p className="mt-3 text-xs leading-6 text-muted-foreground">
                只有本轮实际产生的知识片段
                <br />
                才会出现在这里。
              </p>
            </div>
          )}
          <p className="mt-6 text-[10px] leading-5 text-muted-foreground">
            当前展示预检索选入上下文的片段；不代表全部召回候选，也不等于回答最终引用。
          </p>
        </TabsContent>
      </Tabs>
      <div className="flex items-center gap-2 border-t px-5 py-4 text-[10px] text-[#929c87]">
        <span className="size-1.5 rounded-full bg-[#a9b89a]" />
        {run ? "执行记录与当前回答关联" : "等待你的第一个问题"}
      </div>
    </aside>
  );
}
