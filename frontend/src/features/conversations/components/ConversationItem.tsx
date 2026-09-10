import { useState } from "react";
import { MessageSquare, Pencil, Check } from "lucide-react";
import { Link } from "react-router-dom";
import { Input } from "@/shared/ui/input";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/utils";
import type { Conversation } from "../types";
import { useConversationStore } from "../store/conversationStore";
export function ConversationItem({
  item,
  active,
  onChoose,
}: {
  item: Conversation;
  active: boolean;
  onChoose: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(item.title);
  const [error, setError] = useState("");
  async function save() {
    try {
      await useConversationStore.getState().rename(item.id, title);
      setEditing(false);
      setError("");
    } catch {
      setError("重命名失败，请重试");
    }
  }
  return (
    <div className="group">
      <div
        className={cn(
          "flex items-center gap-2 rounded-lg px-2 py-1 text-[13px]",
          active
            ? "bg-[#e5e9e0] text-primary"
            : "text-[#6a7168] hover:bg-[#ecede7]",
        )}
      >
        {editing ? (
          <>
            <Input
              aria-label="对话标题"
              maxLength={160}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void save();
                if (e.key === "Escape") setEditing(false);
              }}
              autoFocus
            />
            <Button
              size="icon"
              variant="ghost"
              aria-label="保存标题"
              onClick={() => void save()}
            >
              <Check size={14} />
            </Button>
          </>
        ) : (
          <>
            <Link
              className="flex min-w-0 flex-1 items-center gap-2.5 py-2.5"
              to={"/chat/" + item.id}
              onClick={onChoose}
              aria-current={active ? "page" : undefined}
            >
              <MessageSquare size={14} className="shrink-0 opacity-65" />
              <span className="truncate">{item.title}</span>
            </Link>
            <button
              aria-label={"重命名 " + item.title}
              className="p-1 opacity-0 group-hover:opacity-100 focus:opacity-100"
              onClick={() => {
                setTitle(item.title);
                setEditing(true);
              }}
            >
              <Pencil size={12} />
            </button>
          </>
        )}
      </div>
      {error && <p className="p-2 text-xs text-destructive">{error}</p>}
    </div>
  );
}
