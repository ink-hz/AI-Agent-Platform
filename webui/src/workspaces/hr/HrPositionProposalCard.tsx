import { useEffect, useRef, useState } from "react";
import { Check, CircleAlert, Copy, Download } from "lucide-react";

import { copyVisibleText } from "../../clipboard";
import { MessageMarkdown } from "../../components/MessageMarkdown";
import type { HrPositionPackage } from "../../hrTypes";


type PackageTab = "mission" | "jd" | "jr";
const TABS: ReadonlyArray<readonly [PackageTab, string]> = [
  ["mission", "岗位需求"], ["jd", "JD"], ["jr", "JR"],
];


function safeFilename(value: string): string {
  const normalized = value.normalize("NFKC")
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
    .replace(/\s+/g, " ")
    .replace(/[-. ]+$/g, "")
    .trim()
    .slice(0, 80);
  return normalized || "岗位方案";
}


function downloadText(filename: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.hidden = true;
  document.body.append(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}


export function HrPositionProposalCard({
  confirmationDisabled = false,
  confirmed = false,
  notice,
  onConfirm,
  onCopy = copyVisibleText,
  positionPackage,
}: {
  confirmationDisabled?: boolean;
  confirmed?: boolean;
  notice?: string | null;
  onConfirm(): Promise<void> | void;
  onCopy?: (text: string) => Promise<boolean>;
  positionPackage: HrPositionPackage;
}) {
  const [activeTab, setActiveTab] = useState<PackageTab>("mission");
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const [confirming, setConfirming] = useState(false);
  const confirmationInFlight = useRef(false);
  const activeCopyScope = useRef("");
  const copyRequest = useRef(0);
  const activeLabel = TABS.find(([tab]) => tab === activeTab)?.[1] ?? "岗位需求";
  const activeText = positionPackage.modules[activeTab].text;
  activeCopyScope.current = `${positionPackage.draftVersionId}:${activeTab}`;

  useEffect(() => {
    setCopyState("idle");
  }, [activeTab, positionPackage.draftVersionId]);

  useEffect(() => {
    if (copyState === "idle") return;
    const timer = window.setTimeout(() => setCopyState("idle"), 1_800);
    return () => window.clearTimeout(timer);
  }, [copyState]);

  const copy = async () => {
    const scope = activeCopyScope.current;
    const request = ++copyRequest.current;
    try {
      const copied = await onCopy(activeText);
      if (activeCopyScope.current === scope && copyRequest.current === request) setCopyState(copied ? "copied" : "error");
    } catch {
      if (activeCopyScope.current === scope && copyRequest.current === request) setCopyState("error");
    }
  };
  const confirm = async () => {
    if (confirmationDisabled || confirmed || confirmationInFlight.current) return;
    confirmationInFlight.current = true;
    setConfirming(true);
    try {
      await onConfirm();
    } finally {
      confirmationInFlight.current = false;
      setConfirming(false);
    }
  };
  const copyLabel = copyState === "copied"
    ? `已复制 ${activeLabel}`
    : copyState === "error" ? `复制失败 ${activeLabel}` : `复制 ${activeLabel}`;
  const tabKey = (event: React.KeyboardEvent<HTMLButtonElement>, tab: PackageTab) => {
    const index = TABS.findIndex(([candidate]) => candidate === tab);
    let next = index;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % TABS.length;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + TABS.length) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    else return;
    event.preventDefault();
    const selected = TABS[next][0];
    setActiveTab(selected);
    document.getElementById(`hr-position-proposal-tab-${selected}`)?.focus();
  };

  return <article aria-label="岗位方案" className="hr-position-proposal-card">
    <header>
      <div><span>POSITION PACKAGE · V{positionPackage.versionNumber}</span><h2>岗位方案</h2></div>
      <strong>{positionPackage.title}</strong>
    </header>
    <div aria-label="岗位方案内容" className="hr-position-proposal-tabs" role="tablist">
      {TABS.map(([tab, label]) => <button
        aria-controls={`hr-position-proposal-${tab}`}
        aria-selected={activeTab === tab}
        id={`hr-position-proposal-tab-${tab}`}
        key={tab}
        onClick={() => setActiveTab(tab)}
        onKeyDown={(event) => tabKey(event, tab)}
        role="tab"
        tabIndex={activeTab === tab ? 0 : -1}
        type="button"
      >{label}</button>)}
    </div>
    <section
      aria-labelledby={`hr-position-proposal-tab-${activeTab}`}
      className="hr-position-proposal-content"
      id={`hr-position-proposal-${activeTab}`}
      role="tabpanel"
    ><MessageMarkdown content={activeText} /></section>
    <footer>
      <div className="hr-position-proposal-icon-actions">
        <button aria-label={copyLabel} className={copyState} onClick={() => void copy()} title={copyLabel} type="button">
          {copyState === "copied" ? <Check size={16} /> : copyState === "error" ? <CircleAlert size={16} /> : <Copy size={16} />}
        </button>
        <button aria-label={`下载${activeLabel}`} onClick={() => downloadText(
          `${safeFilename(positionPackage.title)}-${activeLabel}.md`, activeText,
        )} title={`下载${activeLabel}`} type="button"><Download size={16} /></button>
      </div>
      <button className="hr-position-proposal-confirm" disabled={confirmationDisabled || confirmed || confirming} onClick={() => void confirm()} type="button">
        {confirmed ? "已加入岗位库" : confirming ? "正在加入岗位库…" : "确认并加入岗位库"}
      </button>
    </footer>
    {notice && <p className="hr-position-proposal-notice" role="status">{notice}</p>}
  </article>;
}
