import { Orbit } from "lucide-react";
export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-primary text-white">
        <Orbit size={21} strokeWidth={1.5} />
      </span>
      {!compact && (
        <span className="text-[15px] font-semibold tracking-tight">
          Support
          <span className="font-normal text-muted-foreground"> Agent</span>
        </span>
      )}
    </div>
  );
}
