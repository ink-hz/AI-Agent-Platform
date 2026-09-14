import { useEffect, useMemo, useState } from "react";
import { ArrowUpRight, BriefcaseBusiness, Search } from "lucide-react";

import type { Account } from "../../auth";
import { PlatformLink } from "../../components/PlatformLink";
import { createHrApi, type HrApi } from "../../hrApi";
import type { HrPosition } from "../../hrTypes";
import "./hrPositionWorkflow.css";


function sourceLabel(position: HrPosition): string {
  return position.sourceKind === "official_site" ? "官网" : "内部";
}


function officialStatus(status: HrPosition["officialStatus"]): string {
  return ({
    active: "在招", stale: "信息较旧", suspected_inactive: "疑似下线", inactive: "已下线",
  } as const)[status ?? "active"];
}


async function loadEveryPosition(api: HrApi, signal: AbortSignal): Promise<HrPosition[]> {
  const found = new Map<string, HrPosition>();
  const seenCursors = new Set<string>();
  let cursor: string | undefined;
  do {
    const page = await api.listPositions(
      cursor ? { limit: 100, cursor } : { limit: 100 }, signal,
    );
    for (const item of page.items) found.set(item.positionId, item);
    if (!page.nextCursor) break;
    if (seenCursors.has(page.nextCursor)) throw new Error("repeated position cursor");
    seenCursors.add(page.nextCursor);
    cursor = page.nextCursor;
  } while (!signal.aborted);
  return [...found.values()];
}


export function HrPositionIndex({
  account,
  api: injectedApi,
  onSelect,
}: {
  account: Account;
  api?: HrApi;
  onSelect?: (position: HrPosition) => void;
}) {
  const api = useMemo(
    () => injectedApi ?? createHrApi(account.csrf_token),
    [account.csrf_token, injectedApi],
  );
  const [positions, setPositions] = useState<HrPosition[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"all" | HrPosition["internalStatus"]>("all");
  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    void loadEveryPosition(api, controller.signal).then((loadedPositions) => {
      if (controller.signal.aborted) return;
      setPositions(loadedPositions); setState("ready");
    }).catch(() => {
      if (!controller.signal.aborted) setState("error");
    });
    return () => controller.abort();
  }, [api, attempt]);

  const visible = useMemo(() => {
    const selected = query.trim().toLocaleLowerCase();
    return positions.filter((position) =>
      (status === "all" || position.internalStatus === status)
      && (!selected || [
        position.title, position.officialJobId, position.department, ...position.locations,
      ].some((value) => value?.toLocaleLowerCase().includes(selected))),
    );
  }, [positions, query, status]);
  if (state === "loading") return <main className="hr-position-directory hr-position-state"><p>正在读取岗位…</p></main>;
  if (state === "error") return <main className="hr-position-directory hr-position-state" role="alert">
    <h1>岗位数据暂时不可用</h1><p>已有数据不会丢失，请稍后重试。</p>
    <button type="button" onClick={() => setAttempt((value) => value + 1)}>重新加载</button>
  </main>;

  return <main className="hr-position-directory"><div className="hr-position-page-inner">
    <header className="hr-pw-heading">
      <div><span>RECRUITMENT WORKSPACE</span>
        <h1>岗位</h1>
        <p>围绕一个岗位，查看要求、候选人、面试与主对话中保存的成果。</p>
      </div>
      <BriefcaseBusiness size={34} aria-hidden="true" />
    </header>

    <div className="hr-pw-directory-tools">
      <label><Search size={18} aria-hidden="true" /><input aria-label="搜索岗位" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索岗位、编号、部门或地点" /></label>
      <select aria-label="岗位状态" value={status} onChange={(event) => setStatus(event.target.value as typeof status)}>
        <option value="all">全部岗位</option><option value="active">进行中</option><option value="draft">草案</option><option value="archived">已归档</option>
      </select>
      <span>{visible.length} 个岗位</span>
    </div>
    {account.hard_stale_read_only && <p className="hr-position-notice" role="status">账号目录信息已过期，岗位数据暂时只读。</p>}
    <PositionSection positions={visible} onSelect={onSelect} />
    </div>
  </main>;
}


function PositionSection({ positions, onSelect }: { positions: HrPosition[]; onSelect?: (position: HrPosition) => void }) {
  return <section className="hr-position-section">
    {positions.length === 0 ? <div className="hr-position-empty">没有匹配的岗位。</div>
      : <div className="hr-pw-position-list">{positions.map((position) => <article className="hr-pw-position-card" key={position.positionId}>
        <div><span>{position.officialJobId ?? '自建岗位'} · {sourceLabel(position)}</span><em>{position.internalStatus === "archived" ? "已归档" : position.internalStatus === "draft" ? "草案" : "进行中"}</em></div>
        <h2><PlatformLink href={`/hr/positions/${encodeURIComponent(position.positionId)}`}>{position.title}</PlatformLink></h2>
        <p>{[position.department, ...position.locations].filter(Boolean).join(" · ") || "部门与地点待补充"}</p>
        <div className="hr-pw-card-source">{position.officialStatus && <span>{officialStatus(position.officialStatus)}</span>}<span>{position.sourceVersion ? `官网版本 ${position.sourceVersion}` : "内部上下文"}</span></div>
        <footer><PlatformLink href={`/hr/positions/${encodeURIComponent(position.positionId)}`}>查看岗位工作流 <ArrowUpRight size={16} aria-hidden="true" /></PlatformLink>
          {onSelect ? <button type="button" disabled={position.internalStatus !== "active"} onClick={() => onSelect(position)}>在主对话中继续</button> : <PlatformLink href={`/hr/?position=${encodeURIComponent(position.positionId)}`}>与 Hannah 讨论岗位</PlatformLink>}
        </footer>
      </article>)}</div>}
  </section>;
}
