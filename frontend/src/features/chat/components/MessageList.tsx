import { useEffect, useRef, useState } from "react";
import type { Run } from "@/features/execution/types";
import { MessageItem } from "./MessageItem";
export function MessageList({
  runs,
  selected,
  onInspect,
}: {
  runs: Run[];
  selected?: string;
  onInspect: (id: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);
  const last = runs.at(-1);
  useEffect(() => {
    if (follow && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [last?.response, last?.events.length, runs.length, follow]);
  return (
    <div
      ref={ref}
      className="min-h-0 flex-1 overflow-y-auto"
      onScroll={(e) => {
        const n = e.currentTarget;
        setFollow(n.scrollHeight - n.scrollTop - n.clientHeight < 100);
      }}
    >
      <div className="mx-auto max-w-[800px] px-5 py-8 md:px-10">
        {runs.map((run) => (
          <MessageItem
            key={run.id}
            run={run}
            selected={selected === run.id}
            onInspect={() => onInspect(run.id)}
          />
        ))}
      </div>
    </div>
  );
}
