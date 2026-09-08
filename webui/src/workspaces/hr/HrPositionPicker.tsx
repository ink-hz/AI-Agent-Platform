import { useEffect, useId, useRef, useState } from "react";
import { BriefcaseBusiness, ChevronDown, Search, X } from "lucide-react";

import type { HrApi } from "../../hrApi";
import type { HrPosition } from "../../hrTypes";

export function HrPositionPicker({ api, selected, disabled = false, existingConversation = false, onSelect, onOpenDetails }: {
  api: HrApi;
  selected: HrPosition | null;
  disabled?: boolean;
  existingConversation?: boolean;
  onSelect(position: HrPosition | null): void;
  onOpenDetails?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<HrPosition[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [attempt, setAttempt] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open || disabled) return;
    const controller = new AbortController();
    setState("loading");
    const timer = window.setTimeout(() => {
      void (async () => {
        const positions = new Map<string, HrPosition>();
        const cursors = new Set<string>();
        let cursor: string | undefined;
        do {
          const page = await api.listPositions({ internalStatus: "active", limit: 100, cursor }, controller.signal);
          if (controller.signal.aborted) return;
          for (const item of page.items) positions.set(item.positionId, item);
          cursor = page.nextCursor ?? undefined;
          if (cursor && cursors.has(cursor)) break;
          if (cursor) cursors.add(cursor);
        } while (cursor);
        setItems([...positions.values()]);
        setState("ready");
      })().catch(() => { if (!controller.signal.aborted) setState("error"); });
    }, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [api, attempt, disabled, open]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  function select(position: HrPosition | null) {
    if (disabled) return;
    setOpen(false);
    onSelect(position);
    trigger.current?.focus();
  }

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visible = items.filter((position) => [position.title, position.officialJobId, position.department, ...position.locations]
    .some((value) => value?.toLocaleLowerCase().includes(normalizedQuery)));

  return <div className="hr-position-picker" ref={root} onKeyDown={(event) => {
    if (event.key === "Enter" && event.target instanceof HTMLInputElement) { event.preventDefault(); event.stopPropagation(); }
    if (event.key === "Escape") { event.stopPropagation(); setOpen(false); trigger.current?.focus(); }
  }}>
    <button ref={trigger} className="hr-position-picker-trigger" type="button" disabled={disabled}
      aria-expanded={open && !disabled} aria-controls={panelId} aria-haspopup="dialog" onClick={() => setOpen((value) => !value)}>
      <BriefcaseBusiness size={16} aria-hidden="true" /><span>{selected?.title ?? (existingConversation ? "选择本轮岗位" : "选择岗位")}</span><ChevronDown size={14} aria-hidden="true" />
    </button>
    {selected && onOpenDetails && <button className="hr-position-picker-details" type="button" onClick={onOpenDetails}>岗位资料</button>}
    {open && !disabled && <section id={panelId} className="hr-position-picker-popover" role="dialog" aria-label="选择招聘岗位">
      <header><strong>{selected ? "切换岗位" : "选择岗位"}</strong><button type="button" aria-label="关闭岗位选择" onClick={() => { setOpen(false); trigger.current?.focus(); }}><X size={16} /></button></header>
      <label className="hr-position-picker-search"><Search size={16} aria-hidden="true" /><input autoFocus type="search" aria-label="搜索岗位" placeholder="搜索名称、编号、部门或地点" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      {existingConversation && <p className="hr-position-picker-hint">切换岗位只影响下一轮，当前执行和已有消息保持原来的岗位。</p>}
      <div className="hr-position-picker-results">
        <button type="button" className="hr-position-picker-option" aria-pressed={!selected} onClick={() => select(null)}><strong>通用对话</strong><small>不指定岗位</small></button>
        {state === "loading" ? <p role="status">正在搜索岗位…</p> : state === "error" ? <p role="alert">岗位暂时无法读取。<button type="button" onClick={() => setAttempt((value) => value + 1)}>重试</button></p>
          : visible.length === 0 ? <p>没有找到匹配的岗位，试试其他关键词。</p>
          : visible.map((position) => <button key={position.positionId} type="button" className="hr-position-picker-option" aria-pressed={selected?.positionId === position.positionId} onClick={() => select(position)}>
            <strong>{position.title}</strong><small>{[position.officialJobId, position.department, ...position.locations, position.officialStatus === "inactive" ? "官网已下线" : null].filter(Boolean).join(" · ") || "内部岗位"}</small>
          </button>)}
      </div>
    </section>}
  </div>;
}
