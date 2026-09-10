import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Plus,
  Search,
  ArrowUpRight,
  MessageSquare,
  RefreshCw,
} from "lucide-react";
import { Brand } from "@/shared/components/Brand";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { useConversationStore } from "../store/conversationStore";
import { ConversationItem } from "./ConversationItem";
export function ConversationSidebar({
  id,
  onChoose = () => {},
}: {
  id?: string;
  onChoose?: () => void;
}) {
  const { items, error, loading, load } = useConversationStore();
  const [search, setSearch] = useState("");
  const filtered = items.filter((item) =>
    item.title.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <nav
      aria-label="会话导航"
      className="flex h-full flex-col bg-[#f2f3ed] p-5"
    >
      <Link to="/" onClick={onChoose} className="mb-8 mt-1">
        <Brand />
      </Link>
      <Button
        asChild
        className="h-10 w-full justify-start gap-2.5 rounded-lg shadow-none"
      >
        <Link to="/" onClick={onChoose}>
          <Plus size={17} />
          开启新对话
        </Link>
      </Button>
      <div className="relative mt-4">
        <Search
          size={14}
          className="absolute left-3 top-3 text-muted-foreground"
        />
        <Input
          aria-label="搜索对话"
          placeholder="搜索对话…"
          className="h-9 border-transparent bg-white/50 pl-9 text-xs shadow-none"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      <div className="mb-3 mt-7 flex items-center justify-between text-[10px] font-medium tracking-[.16em] text-muted-foreground">
        <span>你的对话</span>
        <span>{items.length.toString().padStart(2, "0")}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto space-y-1">
        {loading && (
          <p className="p-2 text-xs text-muted-foreground">正在加载会话…</p>
        )}
        {error && (
          <div className="rounded-lg border bg-white/50 p-3 text-xs leading-6 text-muted-foreground">
            <p>{error}</p>
            <Button variant="ghost" size="sm" onClick={() => void load()}>
              <RefreshCw size={12} />
              重试连接
            </Button>
          </div>
        )}
        {!loading && !error && !filtered.length && (
          <div className="py-8 text-center text-xs leading-7 text-muted-foreground">
            <MessageSquare className="mx-auto mb-2 opacity-35" size={22} />
            {search ? "没有找到匹配的对话" : "从一个问题开始"}
            <br />
            {!search && "你的对话会保存在这里"}
          </div>
        )}
        {filtered.map((item) => (
          <ConversationItem
            key={item.id}
            item={item}
            active={id === item.id}
            onChoose={onChoose}
          />
        ))}
      </div>
      <a
        href="https://github.com/gyang-a/support-agent"
        target="_blank"
        rel="noreferrer"
        className="mb-5 mt-3 flex items-center justify-between rounded-lg border border-[#dfe3d8] p-3 text-xs text-muted-foreground"
      >
        <span>了解这个项目</span>
        <ArrowUpRight size={14} />
      </a>
      <div className="flex items-center gap-3 border-t pt-4">
        <span className="flex size-8 items-center justify-center rounded-full border border-[#d5ddcb] bg-[#e6ebde] text-xs font-medium text-primary">
          YG
        </span>
        <div>
          <p className="text-xs font-medium">演示工作空间</p>
          <p className="mt-1 text-[10px] text-muted-foreground">
            user_1001 · 演示身份
          </p>
        </div>
        <span className="ml-auto size-1.5 rounded-full bg-[#7c9273]" />
      </div>
    </nav>
  );
}
