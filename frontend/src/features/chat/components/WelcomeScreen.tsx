import {
  ArrowUpRight,
  ShoppingBag,
  PackageSearch,
  Cable,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
const starters = [
  {
    icon: ShoppingBag,
    title: "找到适合你的好物",
    label: "选购推荐",
    query: "预算 5500 元，推荐一台适合编程的笔记本。",
  },
  {
    icon: Cable,
    title: "让设备更好地协作",
    label: "配件兼容",
    query: "iPhone 16 Pro 能用 Lightning 数据线吗？",
  },
  {
    icon: PackageSearch,
    title: "看看订单到哪里了",
    label: "订单物流",
    query: "查一下订单 DG-1001-0002 的最新物流。",
  },
  {
    icon: ShieldCheck,
    title: "解决使用中的小问题",
    label: "售后支持",
    query: "激活后的手机还能七天无理由退货吗？",
  },
];
export function WelcomeScreen({
  onPrompt,
}: {
  onPrompt: (text: string) => void;
}) {
  return (
    <div className="mx-auto flex w-full max-w-[700px] shrink-0 flex-col px-6 pb-8 pt-8 md:px-10">
      <div className="mb-7 flex size-14 items-center justify-center rounded-2xl border border-[#dce4d6] bg-[#edf2e8] text-primary shadow-[0_4px_18px_#304c3510]">
        <Sparkles size={27} strokeWidth={1.25} />
      </div>
      <p className="mb-3 text-[10px] font-medium tracking-[.24em] text-[#8a9485]">
        YOUR EVERYDAY TECH COMPANION
      </p>
      <h1 className="text-[30px] font-medium tracking-tight leading-[1.5] md:text-[34px]">
        关于数码的事，
        <br />
        <span className="text-[#72876a]">我们一起搞定。</span>
      </h1>
      <p className="mt-4 max-w-md text-[13px] leading-7 text-muted-foreground">
        从挑选新设备，到追踪订单、解决使用问题。
        <br className="hidden sm:block" />
        告诉我你的需要，专业助手会协作帮你找到答案。
      </p>
      <div className="mt-9 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {starters.map(({ icon: Icon, title, label, query }) => (
          <button
            key={label}
            onClick={() => onPrompt(query)}
            className="group rounded-xl border bg-white/60 px-4 py-4 text-left transition-colors hover:border-[#adbea6] hover:bg-[#f4f7ef]"
          >
            <div className="mb-4 flex items-center justify-between">
              <Icon size={18} strokeWidth={1.5} className="text-[#708269]" />
              <ArrowUpRight
                size={14}
                className="text-[#b4bbac] group-hover:text-primary"
              />
            </div>
            <p className="text-[13px] font-medium">{title}</p>
            <p className="mt-1.5 text-[10px] text-muted-foreground">{label}</p>
          </button>
        ))}
      </div>
      <p className="mt-6 flex items-center gap-2 text-[10px] text-[#959c8d]">
        <span className="size-1 rounded-full bg-[#8d9e7e]" />
        也可以一次提出多个问题，交给不同助手协作处理
      </p>
    </div>
  );
}
