import { ArrowUp, Square, CornerDownLeft } from "lucide-react";
import { Button } from "@/shared/ui/button";
import { Textarea } from "@/shared/ui/textarea";
export function MessageComposer({
  value,
  onChange,
  onSend,
  onStop,
  busy,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop: () => void;
  busy: boolean;
  disabled?: boolean;
}) {
  return (
    <div className="mx-auto w-full max-w-[800px] px-4 pb-4 md:px-8">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy && value.trim()) onSend();
        }}
        className="rounded-2xl border border-[#dce1d5] bg-white p-3 shadow-[0_4px_24px_#20352006] focus-within:border-[#9fb294]"
      >
        <Textarea
          aria-label="输入问题"
          placeholder="问问商品、订单，或任何使用中的问题…"
          maxLength={4000}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault();
              if (!busy && !disabled && value.trim()) onSend();
            }
          }}
          className="max-h-40 min-h-14 resize-none border-0 p-1 text-[13px] shadow-none focus-visible:ring-0"
        />
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-1.5 pl-1 text-[10px] text-muted-foreground">
            <span className="size-1.5 rounded-full bg-[#8eaa7c]" />
            多智能体协作<span className="mx-2 text-[#d0d4c9]">|</span>
            <span className="hidden sm:inline">Shift + Enter 换行</span>
            <CornerDownLeft size={11} />
          </span>
          {busy ? (
            <Button
              type="button"
              size="icon"
              aria-label="停止执行"
              onClick={onStop}
            >
              <Square size={14} fill="currentColor" />
            </Button>
          ) : (
            <Button
              size="icon"
              type="submit"
              aria-label="发送消息"
              disabled={!value.trim() || disabled}
            >
              <ArrowUp size={18} />
            </Button>
          )}
        </div>
      </form>
      <p className="mt-3 text-center text-[10px] text-[#a0a697]">
        AI 回答仅供参考，商品信息与售后政策请以实际查询结果为准。
      </p>
    </div>
  );
}
