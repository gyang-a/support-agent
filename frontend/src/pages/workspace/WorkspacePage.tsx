import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useShallow } from "zustand/react/shallow";
import { PanelRight, Menu, ChevronDown } from "lucide-react";
import { Button } from "@/shared/ui/button";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetDescription,
} from "@/shared/ui/sheet";
import { ConversationSidebar } from "@/features/conversations/components/ConversationSidebar";
import { useConversationStore } from "@/features/conversations/store/conversationStore";
import { useExecutionStore } from "@/features/execution/store/executionStore";
import { useChatStore } from "@/features/chat/store/chatStore";
import { startRun } from "@/features/execution/services/runController";
import { runApi } from "@/features/execution/api/runApi";
import { WelcomeScreen } from "@/features/chat/components/WelcomeScreen";
import { MessageList } from "@/features/chat/components/MessageList";
import { MessageComposer } from "@/features/chat/components/MessageComposer";
import { InspectorPanel } from "@/features/inspector/components/InspectorPanel";
import { usePreferences } from "@/shared/store/preferencesStore";
import { useRunHistory } from "@/features/execution/hooks/useRunHistory";

export function WorkspacePage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const [sidebar, setSidebar] = useState(false);
  const [mobileInspector, setMobileInspector] = useState(false);
  const [pending, setPending] = useState(false);
  const { loading, historyError } = useRunHistory(conversationId);
  const [error, setError] = useState("");
  const { inspectorOpen, toggleInspector } = usePreferences();
  const load = useConversationStore((s) => s.load);
  const title = useConversationStore(
    (s) => s.items.find((c) => c.id === conversationId)?.title,
  );
  const runs = useExecutionStore(
    useShallow((s) =>
      Object.values(s.runs)
        .filter((r) => r.conversation_id === conversationId)
        .sort((a, b) => a.created_at.localeCompare(b.created_at)),
    ),
  );
  const selectedId = useExecutionStore((s) => s.selected[conversationId ?? ""]);
  const selected = runs.find((r) => r.id === selectedId) ?? runs.at(-1);
  const busy = runs.some((r) => r.status === "running");
  const draftKey = conversationId ?? "new";
  const draft = useChatStore((s) => s.drafts[draftKey] ?? "");
  const setDraft = (text: string) =>
    useChatStore.getState().setDraft(draftKey, text);
  useEffect(() => {
    void load();
  }, [load]);
  async function send() {
    if (!draft.trim() || busy || pending) return;
    setPending(true);
    setError("");
    const text = draft.trim();
    try {
      let id = conversationId;
      if (!id) {
        const item = await useConversationStore
          .getState()
          .create(text.slice(0, 30));
        id = item.id;
        navigate("/chat/" + id);
      }
      setDraft("");
      void startRun(id, text).then(() => load());
    } catch (e) {
      setError(e instanceof Error ? e.message : "发送失败");
    } finally {
      setPending(false);
    }
  }
  function inspect(id: string) {
    if (conversationId) useExecutionStore.getState().select(conversationId, id);
    if (window.innerWidth < 1280) setMobileInspector(true);
    else if (!inspectorOpen) toggleInspector();
  }
  async function stop() {
    const running = runs.find((r) => r.status === "running");
    if (running)
      try {
        const result = await runApi.cancel(running.id);
        if (!result.ok) setError("服务端已无活动任务，请刷新查看最终状态。");
      } catch {
        setError("停止请求失败，请重试");
      }
  }
  return (
    <div className="flex h-dvh overflow-hidden">
      <div className="hidden w-[240px] shrink-0 border-r lg:block">
        <ConversationSidebar id={conversationId} />
      </div>
      <Sheet open={sidebar} onOpenChange={setSidebar}>
        <SheetContent side="left" className="w-[280px] p-0">
          <SheetTitle className="sr-only">会话导航</SheetTitle>
          <SheetDescription className="sr-only">
            选择或新建一个对话
          </SheetDescription>
          <ConversationSidebar
            id={conversationId}
            onChoose={() => setSidebar(false)}
          />
        </SheetContent>
      </Sheet>
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-[73px] shrink-0 items-center justify-between border-b px-5 md:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <Button
              variant="ghost"
              size="icon"
              className="lg:hidden"
              aria-label="打开会话导航"
              onClick={() => setSidebar(true)}
            >
              <Menu size={18} />
            </Button>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="max-w-[220px] truncate text-[13px] font-medium">
                  {title || "数码智能客服"}
                </h2>
                <ChevronDown size={12} className="text-muted-foreground" />
              </div>
              <p className="mt-1 text-[10px] text-muted-foreground">
                专业协作，让每个问题都有回应
              </p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <span className="hidden items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] text-muted-foreground sm:flex">
              <span className="size-1 rounded-full bg-[#829970]" />
              多智能体模式
            </span>
            <Button
              variant="ghost"
              size="icon"
              aria-label="切换执行详情"
              onClick={() =>
                window.innerWidth < 1280
                  ? setMobileInspector(true)
                  : toggleInspector()
              }
            >
              <PanelRight size={17} strokeWidth={1.5} />
            </Button>
          </div>
        </header>
        {(error || historyError) && (
          <div
            role="alert"
            className="mx-5 mt-3 rounded-lg border border-red-100 bg-red-50 p-3 text-xs text-destructive"
          >
            {error || historyError}
          </div>
        )}
        {loading && !runs.length && conversationId ? (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            加载对话中…
          </div>
        ) : runs.length ? (
          <MessageList
            runs={runs}
            selected={selected?.id}
            onInspect={inspect}
          />
        ) : (
          <div className="flex min-h-0 flex-1 overflow-auto">
            <WelcomeScreen onPrompt={setDraft} />
          </div>
        )}
        <MessageComposer
          value={draft}
          onChange={setDraft}
          onSend={() => void send()}
          onStop={() => void stop()}
          busy={busy}
          disabled={pending || (loading && !!conversationId)}
        />
      </main>
      {inspectorOpen && (
        <div className="hidden w-[330px] shrink-0 border-l xl:block">
          <InspectorPanel run={selected} onClose={toggleInspector} />
        </div>
      )}
      <Sheet open={mobileInspector} onOpenChange={setMobileInspector}>
        <SheetContent
          side="right"
          showCloseButton={false}
          className="w-[360px] max-w-full p-0 sm:max-w-[360px]"
        >
          <SheetTitle className="sr-only">执行详情</SheetTitle>
          <SheetDescription className="sr-only">
            查看本轮执行任务和检索证据
          </SheetDescription>
          <InspectorPanel
            run={selected}
            onClose={() => setMobileInspector(false)}
          />
        </SheetContent>
      </Sheet>
    </div>
  );
}
