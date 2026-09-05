import { useEffect, useRef, useState } from "react";

import type { HrR12Api } from "../../hrR12Api";
import type { HrContextVersion } from "../../hrR12Types";
import type { HrPositionDetail } from "../../hrTypes";
import { HrCandidateWorkspace } from "./HrCandidateWorkspace";
import { HrPositionContextPanel } from "./HrPositionContextPanel";
import { HrOfficialPositionPanel } from "./HrOfficialPositionPanel";
import { HrPositionResourcesPanel } from "./HrPositionResourcesPanel";
import { trapDialogFocus } from "./modalFocus";

export type HrPositionDetailsTab = "position" | "candidates" | "resources";
const TABS: ReadonlyArray<readonly [HrPositionDetailsTab, string]> = [
  ["position", "岗位信息"],
  ["candidates", "候选人"],
  ["resources", "材料与成果"],
];

function sourceLabel(detail: HrPositionDetail): string {
  return detail.sourceKind === "official_site" ? "官网同步" : "手动创建";
}

function statusLabel(detail: HrPositionDetail): string {
  if (detail.internalStatus === "archived") return "已归档";
  if (detail.officialStatus === "inactive") return "官网已下线";
  if (detail.officialStatus === "stale" || detail.officialStatus === "suspected_inactive") return "官网状态待核验";
  return "进行中";
}

export function HrPositionDetailsDrawer({ activeTab: controlledActiveTab, api, csrfToken, currentContextVersionId = null,
  detail, initialTab = "position", open, readOnly, onActiveTabChange, onClose, onConfirmed,
  contextRefreshGeneration = 0, degraded = false, onRetryDetail, resourceRefreshGeneration = 0,
  taskConversationId }: {
  activeTab?: HrPositionDetailsTab;
  api: HrR12Api;
  csrfToken: string;
  currentContextVersionId?: string | null;
  detail: HrPositionDetail;
  initialTab?: HrPositionDetailsTab;
  open: boolean;
  readOnly: boolean;
  onActiveTabChange?(tab: HrPositionDetailsTab): void;
  onClose(): void;
  onConfirmed(context: HrContextVersion): void;
  contextRefreshGeneration?: number;
  degraded?: boolean;
  onRetryDetail?(): void;
  resourceRefreshGeneration?: number;
  taskConversationId?: string;
}) {
  const [uncontrolledActiveTab, setUncontrolledActiveTab] = useState<HrPositionDetailsTab>(initialTab);
  const activeTab = controlledActiveTab ?? uncontrolledActiveTab;
  const [visited, setVisited] = useState<Set<HrPositionDetailsTab>>(
    () => new Set([controlledActiveTab ?? initialTab]),
  );
  const closeButton = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const tabRefs = useRef<Partial<Record<HrPositionDetailsTab, HTMLButtonElement | null>>>({});

  useEffect(() => {
    if (controlledActiveTab === undefined) setUncontrolledActiveTab(initialTab);
  }, [controlledActiveTab, initialTab]);

  useEffect(() => {
    setVisited((current) => current.has(activeTab) ? current : new Set([...current, activeTab]));
  }, [activeTab]);

  useEffect(() => {
    if (!open) return;
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButton.current?.focus();
  }, [open]);

  if (!open) return null;

  const close = () => {
    returnFocus.current?.focus();
    onClose();
  };
  const activate = (tab: HrPositionDetailsTab) => {
    setVisited((current) => current.has(tab) ? current : new Set([...current, tab]));
    if (controlledActiveTab === undefined) setUncontrolledActiveTab(tab);
    onActiveTabChange?.(tab);
  };
  const tabKey = (event: React.KeyboardEvent<HTMLButtonElement>, tab: HrPositionDetailsTab) => {
    const index = TABS.findIndex(([candidate]) => candidate === tab);
    let next = index;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % TABS.length;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + TABS.length) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    else return;
    event.preventDefault();
    const selected = TABS[next][0];
    activate(selected);
    tabRefs.current[selected]?.focus();
  };

  return <><button aria-label="关闭岗位资料遮罩" className="hr-drawer-backdrop" type="button" onClick={close} />
    <aside aria-label="岗位资料" aria-modal="true" className="hr-position-details-drawer" role="dialog" onKeyDown={(event) => trapDialogFocus(event, close)}>
      <header><div><span>POSITION</span><h2>岗位资料</h2></div><button aria-label="关闭岗位资料" ref={closeButton} type="button" onClick={close}>关闭</button></header>
      <nav aria-label="岗位资料分区"><div role="tablist">{TABS.map(([tab, label]) => <button
        aria-controls={`hr-position-details-${tab}`} aria-selected={activeTab === tab}
        id={`hr-position-details-tab-${tab}`} key={tab} ref={(element) => { tabRefs.current[tab] = element; }}
        role="tab" tabIndex={activeTab === tab ? 0 : -1} type="button"
        onClick={() => activate(tab)} onKeyDown={(event) => tabKey(event, tab)}
      >{label}</button>)}</div></nav>
      {visited.has("position") && <section aria-labelledby="hr-position-details-tab-position" hidden={activeTab !== "position"} id="hr-position-details-position" role="tabpanel">
        {degraded && <div className="hr-position-details-degraded" role="alert">
          <p>岗位资料暂时无法完整读取，当前显示对话方案中的降级内容。</p>
          {onRetryDetail && <button onClick={onRetryDetail} type="button">重新读取岗位资料</button>}
        </div>}
        {detail.sourceKind === "official_site" ? <HrOfficialPositionPanel
          api={api} currentSourceVersion={detail.sourceVersion} fallback={detail} positionId={detail.positionId}
        /> : <article className="hr-position-facts"><h3>岗位概要</h3><dl>
          <div><dt>岗位</dt><dd>{detail.title}</dd></div>
          <div><dt>部门</dt><dd>{detail.department || "待完善"}</dd></div>
          <div><dt>地点</dt><dd>{detail.locations.join("、") || "待完善"}</dd></div>
          <div><dt>来源</dt><dd>{sourceLabel(detail)}</dd></div>
          <div><dt>状态</dt><dd>{statusLabel(detail)}</dd></div>
          {detail.officialJobId && <div><dt>官网岗位编号</dt><dd>{detail.officialJobId}</dd></div>}
        </dl></article>}
        <HrPositionContextPanel api={api} heading="内部岗位理解" onConfirmed={onConfirmed} positionId={detail.positionId} readOnly={readOnly} refreshGeneration={contextRefreshGeneration} />
      </section>}
      {visited.has("candidates") && <section aria-labelledby="hr-position-details-tab-candidates" hidden={activeTab !== "candidates"} id="hr-position-details-candidates" role="tabpanel"><HrCandidateWorkspace
        api={api} csrfToken={csrfToken} currentContextVersionId={currentContextVersionId}
        positionId={detail.positionId} readOnly={readOnly} taskConversationId={taskConversationId}
      /></section>}
      {visited.has("resources") && <section aria-labelledby="hr-position-details-tab-resources" hidden={activeTab !== "resources"} id="hr-position-details-resources" role="tabpanel"><HrPositionResourcesPanel
        api={api} positionId={detail.positionId} readOnly={readOnly}
        refreshGeneration={resourceRefreshGeneration}
      /></section>}
    </aside></>;
}
