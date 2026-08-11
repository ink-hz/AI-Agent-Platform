import { useMemo, useState } from "react";

import type { IssueLink, ReplayRun } from "../../types";
import { MessageMarkdown } from "../MessageMarkdown";


function runtimeLabel(replay: ReplayRun): string {
  if (replay.execution_status === "blocked" && replay.runtime_failure_reason === "missing_replay_input") return "缺少可复跑图片/附件";
  if (replay.runtime_gate === "passed" && replay.semantic_verdict === "pending") return "运行通过，待语义复审";
  if (replay.runtime_gate === "passed") return "运行门通过";
  if (replay.execution_status === "running") return "复跑中";
  return `运行门未通过：${replay.runtime_failure_reason || replay.execution_status}`;
}


export function selectLatestValidReplay(attempts: ReplayRun[]): ReplayRun | null {
  return attempts
    .filter((row) => row.execution_status === "succeeded" && row.runtime_gate === "passed" && row.completed_at)
    .sort((a, b) => Date.parse(b.completed_at!) - Date.parse(a.completed_at!))[0] || null;
}


function SourceList({ sources }: { sources: Record<string, unknown>[] }) {
  if (!sources.length) return <p className="review-muted">未返回结构化来源。</p>;
  return <ul className="replay-sources">{sources.map((source, index) => <li key={index}>{String(source.title || source.name || source.reference || `来源 ${index + 1}`)}</li>)}</ul>;
}


function Attempt({ replay }: { replay: ReplayRun }) {
  return <div className="replay-attempt-content">
    <div className="replay-badges"><span className={`review-state state-${replay.runtime_gate}`}>{runtimeLabel(replay)}</span><span>语义：{replay.semantic_verdict}</span></div>
    {replay.answer ? <MessageMarkdown content={replay.answer} /> : <p className="review-muted">该次执行没有可展示答案。</p>}
    <SourceList sources={replay.sources || []} />
    <dl className="replay-metadata"><div><dt>Trace</dt><dd>{replay.trace_id || "缺失"}</dd></div><div><dt>版本</dt><dd>{replay.actual_version || "缺失"}</dd></div><div><dt>Git SHA</dt><dd>{replay.actual_git_sha || "缺失"}</dd></div><div><dt>模型</dt><dd>{replay.actual_model || "无 provider 回显"}</dd></div></dl>
    {replay.review_reason && <p className="review-reason">复审说明：{replay.review_reason}</p>}
  </div>;
}


export function ReplayMatrix({ links, replays, onReview }: {
  links: IssueLink[];
  replays: ReplayRun[];
  onReview?: (replay: ReplayRun, verdict: "passed" | "failed", reason: string) => void;
}) {
  const rows = useMemo(() => links.filter((link) => link.active).map((link) => ({
    link,
    attempts: replays.filter((replay) => replay.issue_link_id === link.id).sort((a, b) => b.attempt_no - a.attempt_no),
  })), [links, replays]);
  const [reasons, setReasons] = useState<Record<string, string>>({});

  return <div className="replay-matrix">{rows.map(({ link, attempts }) => {
    const latest = selectLatestValidReplay(attempts);
    const history = attempts.filter((attempt) => attempt.id !== latest?.id);
    return <article className="replay-row" key={link.id}>
      <section className="replay-original"><p>原始问题</p><MessageMarkdown content={link.source_question || "未记录问题"} /><p>原始答案</p><MessageMarkdown content={link.source_answer || "未记录原答案"} /><small>{link.source_turn_key} · {link.link_role}</small></section>
      <section className="replay-latest"><header><div><p>最新真实复测</p><h4>{latest ? `第 ${latest.attempt_no} 次` : "尚未复跑"}</h4></div>{latest && <span>{runtimeLabel(latest)}</span>}</header>{latest ? <><Attempt replay={latest} />{latest.runtime_gate === "passed" && latest.semantic_verdict === "pending" && onReview && <div className="semantic-actions"><input aria-label="语义复审理由" placeholder="填写 Codex/FAE 复审理由" value={reasons[latest.id] || ""} onChange={(event) => setReasons((current) => ({ ...current, [latest.id]: event.target.value }))} /><button onClick={() => onReview(latest, "passed", reasons[latest.id] || "")}>语义通过</button><button className="secondary-action" onClick={() => onReview(latest, "failed", reasons[latest.id] || "")}>语义不通过</button></div>}</> : <p className="review-muted">等待 dev 环境逐题真实复跑。</p>}
        {history.length > 0 && <details className="replay-history"><summary>查看其余 {history.length} 次完整历史</summary>{history.map((attempt) => <article key={attempt.id}><h5>第 {attempt.attempt_no} 次</h5><Attempt replay={attempt} /></article>)}</details>}
      </section>
    </article>;
  })}{rows.length === 0 && <p className="review-muted">事项尚未关联回答。</p>}</div>;
}
