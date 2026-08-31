import { useEffect, useState } from "react";

import type {
  FeedbackIssueDetail,
  FeedbackIssueSummary,
  IssueLink,
  ReplayRun,
} from "../../types";
import { GATE_LABELS, STATUS_LABELS } from "./IssueList";
import { ReplayMatrix } from "./ReplayMatrix";

type EvidencePayload = {
  evidence_type: "merge" | "deployment";
  reference: string;
  commit_sha?: string;
  release_manifest_ref?: string;
  environment?: string;
  reason: string;
};

export function IssueDetail({
  detail,
  issues,
  busy,
  readOnly = false,
  onSave,
  onFixReady,
  onEvidence,
  onVerify,
  onReplay,
  onMove,
  onReview,
  onDisposition,
}: {
  detail: FeedbackIssueDetail;
  issues: FeedbackIssueSummary[];
  busy: boolean;
  readOnly?: boolean;
  onSave: (
    owner: string,
    failureLayer: string,
    priority: string,
    rootCause: string,
    impactScope: string,
  ) => void;
  onFixReady: () => void;
  onEvidence: (payload: EvidencePayload) => void;
  onVerify: (id: string) => void;
  onReplay: (link: IssueLink) => void;
  onMove: (link: IssueLink, targetIssueId: string) => void;
  onReview: (
    replay: ReplayRun,
    verdict: "passed" | "failed",
    reason: string,
  ) => void;
  onDisposition: (
    value: "not_actionable" | "wont_fix" | "duplicate",
    target: string,
    reason: string,
  ) => void;
}) {
  const [owner, setOwner] = useState(detail.issue.owner || "");
  const [failureLayer, setFailureLayer] = useState(
    detail.issue.failure_layer || "",
  );
  const [priority, setPriority] = useState(detail.issue.priority);
  const [rootCause, setRootCause] = useState(detail.issue.root_cause || "");
  const [impactScope, setImpactScope] = useState(
    detail.issue.impact_scope || "",
  );
  const [evidenceType, setEvidenceType] = useState<"merge" | "deployment">(
    "merge",
  );
  const [evidenceValue, setEvidenceValue] = useState("");
  const [dispositionReason, setDispositionReason] = useState("");
  const [duplicateTarget, setDuplicateTarget] = useState("");
  const [moveTargets, setMoveTargets] = useState<Record<string, string>>({});
  useEffect(() => {
    setOwner(detail.issue.owner || "");
    setFailureLayer(detail.issue.failure_layer || "");
    setPriority(detail.issue.priority);
    setRootCause(detail.issue.root_cause || "");
    setImpactScope(detail.issue.impact_scope || "");
  }, [detail.issue.id]);

  return (
    <article className="review-detail">
      <header className="review-detail-head">
        <div>
          <p>
            {detail.issue.priority} · {detail.issue.agent_id}
          </p>
          <h1>{detail.issue.title}</h1>
        </div>
        <span className={`issue-status status-${detail.progress.status}`}>
          {STATUS_LABELS[detail.progress.status]}
        </span>
      </header>
      <div className="gate-summary">
        {detail.progress.replay_passed_turns === null ||
        detail.progress.replay_required_turns === null ? (
          <strong>闭环门：暂不可用</strong>
        ) : (
          <strong>
            闭环门：{detail.progress.replay_passed_turns}/
            {detail.progress.replay_required_turns} 条回答通过真实复跑
          </strong>
        )}
        {detail.progress.missing_gates === null ? (
          <p>闭环门状态暂不可用。</p>
        ) : detail.progress.missing_gates.length ? (
          <p>
            仍缺：
            {detail.progress.missing_gates
              .map((gate) => GATE_LABELS[gate] || gate)
              .join("、")}
          </p>
        ) : (
          <p>所有硬门均已满足。</p>
        )}
      </div>

      <section className="review-section">
        <header>
          <p>01</p>
          <h2>原始事实</h2>
        </header>
        {detail.section_availability?.links === "unavailable" && (
          <p className="review-muted">原始事实明细暂不可用。</p>
        )}
        {detail.links.map((link) => (
          <article className="source-fact" key={link.id}>
            <div>
              <b>{link.link_role}</b>
              <code>{link.source_turn_key}</code>
            </div>
            {link.source_context && link.source_context.length > 0 && (
              <details>
                <summary>
                  查看前序上下文（{link.source_context.length} 轮）
                </summary>
                {link.source_context.map((turn) => (
                  <div className="source-context-turn" key={turn.turn_index}>
                    <b>第 {turn.turn_index} 轮问题</b>
                    <p>{turn.question}</p>
                    <b>回答</b>
                    <p>{turn.answer}</p>
                  </div>
                ))}
              </details>
            )}
            <strong>{link.source_question || "未记录问题"}</strong>
            <p>{link.source_answer || "未记录原答案"}</p>
            <dl>
              <div>
                <dt>Trace</dt>
                <dd>{link.source_trace_key || "缺失"}</dd>
              </div>
              <div>
                <dt>Outcome</dt>
                <dd>{link.source_outcome || "缺失"}</dd>
              </div>
              <div>
                <dt>Sources</dt>
                <dd>{link.source_sources?.length || 0}</dd>
              </div>
            </dl>
            <small>
              {link.source_feedback_keys.length} 条源反馈 ·{" "}
              {link.active ? "纳入闭环" : "已移出"}
              {link.source_fallback_used ? " · 使用过 fallback" : ""}
            </small>
            {link.active && !readOnly && (
              <div className="link-move">
                <label>
                  移动回答归属
                  <select
                    value={moveTargets[link.id] || ""}
                    onChange={(event) =>
                      setMoveTargets((current) => ({
                        ...current,
                        [link.id]: event.target.value,
                      }))
                    }
                  >
                    <option value="">选择目标事项</option>
                    {issues
                      .filter(
                        (item) =>
                          item.id !== detail.issue.id &&
                          item.disposition === "actionable",
                      )
                      .map((item) => (
                        <option value={item.id} key={item.id}>
                          {item.title}
                        </option>
                      ))}
                  </select>
                </label>
                <button
                  className="secondary-action"
                  disabled={busy || !moveTargets[link.id]}
                  onClick={() => onMove(link, moveTargets[link.id])}
                >
                  移动
                </button>
              </div>
            )}
          </article>
        ))}
      </section>

      <section className="review-section">
        <header>
          <p>02</p>
          <h2>归因与责任</h2>
        </header>
        {readOnly ? (
          <dl>
            <div>
              <dt>负责人</dt>
              <dd>{owner || "未分配"}</dd>
            </div>
            <div>
              <dt>优先级</dt>
              <dd>{priority}</dd>
            </div>
            <div>
              <dt>失败层</dt>
              <dd>{failureLayer || "待归因"}</dd>
            </div>
            <div>
              <dt>根因</dt>
              <dd>
                {detail.issue.root_cause === null
                  ? "暂不可用"
                  : rootCause || "尚未填写"}
              </dd>
            </div>
            <div>
              <dt>影响范围</dt>
              <dd>
                {detail.issue.impact_scope === null
                  ? "暂不可用"
                  : impactScope || "尚未填写"}
              </dd>
            </div>
          </dl>
        ) : (
          <>
            <div className="review-form-grid">
              <label>
                负责人
                <input
                  value={owner}
                  onChange={(event) => setOwner(event.target.value)}
                  placeholder="fae:zhangsan"
                />
              </label>
              <label>
                优先级
                <select
                  value={priority}
                  onChange={(event) =>
                    setPriority(event.target.value as typeof priority)
                  }
                >
                  {["P0", "P1", "P2", "P3"].map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </select>
              </label>
              <label className="wide">
                失败层
                <select
                  value={failureLayer}
                  onChange={(event) => setFailureLayer(event.target.value)}
                >
                  <option value="">请选择失败层</option>
                  {[
                    "channel",
                    "context",
                    "guardrail",
                    "schema",
                    "planner",
                    "capability_evidence",
                    "coverage",
                    "synthesis",
                    "outcome",
                    "trace_eval",
                  ].map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </select>
              </label>
              <label className="wide">
                根因
                <textarea
                  value={rootCause}
                  onChange={(event) => setRootCause(event.target.value)}
                />
              </label>
              <label className="wide">
                影响范围
                <textarea
                  value={impactScope}
                  onChange={(event) => setImpactScope(event.target.value)}
                />
              </label>
            </div>
            <button
              disabled={busy}
              onClick={() =>
                onSave(owner, failureLayer, priority, rootCause, impactScope)
              }
            >
              保存归因
            </button>
            <button
              className="secondary-action"
              disabled={busy}
              onClick={onFixReady}
            >
              标记修复代码已准备
            </button>
          </>
        )}
      </section>

      <section className="review-section">
        <header>
          <p>03</p>
          <h2>工程证据</h2>
        </header>
        {detail.section_availability?.evidence === "unavailable" && (
          <p className="review-muted">工程证据明细暂不可用。</p>
        )}
        <div className="evidence-list">
          {detail.evidence.map((item) => (
            <article key={item.id}>
              <span>
                {item.evidence_type} · {item.verification_status}
              </span>
              <strong>{item.reference}</strong>
              <code>{item.commit_sha || item.release_manifest_ref}</code>
              {item.verification_status === "pending" && !readOnly && (
                <button disabled={busy} onClick={() => onVerify(item.id)}>
                  机器验证
                </button>
              )}
            </article>
          ))}
        </div>
        {!readOnly && (
          <div className="evidence-add">
            <select
              value={evidenceType}
              onChange={(event) =>
                setEvidenceType(event.target.value as "merge" | "deployment")
              }
            >
              <option value="merge">Merge SHA</option>
              <option value="deployment">Deployment manifest</option>
            </select>
            <input
              aria-label="证据值"
              value={evidenceValue}
              onChange={(event) => setEvidenceValue(event.target.value)}
              placeholder={
                evidenceType === "merge"
                  ? "40 位 merge SHA"
                  : "release manifest 文件名"
              }
            />
            <button
              disabled={busy || !evidenceValue.trim()}
              onClick={() => {
                onEvidence({
                  evidence_type: evidenceType,
                  reference:
                    evidenceType === "merge" ? "修复合并提交" : "生产部署产物",
                  ...(evidenceType === "merge"
                    ? { commit_sha: evidenceValue.trim() }
                    : {
                        release_manifest_ref: evidenceValue.trim(),
                        environment: "production",
                      }),
                  reason: "add engineering evidence",
                });
                setEvidenceValue("");
              }}
            >
              添加证据
            </button>
          </div>
        )}
      </section>

      <section className="review-section">
        <header>
          <p>04</p>
          <h2>逐题复跑矩阵</h2>
        </header>
        {detail.section_availability?.replays === "unavailable" && (
          <p className="review-muted">复跑明细暂不可用。</p>
        )}
        {!readOnly && (
          <div className="replay-launch">
            {detail.links
              .filter((link) => link.active)
              .map((link) => (
                <button
                  disabled={busy}
                  key={link.id}
                  onClick={() => onReplay(link)}
                >
                  复跑 {link.source_turn_key}
                </button>
              ))}
          </div>
        )}
        <ReplayMatrix
          links={detail.links}
          replays={detail.replays}
          onReview={busy || readOnly ? undefined : onReview}
        />
      </section>

      <section className="review-section">
        <header>
          <p>05</p>
          <h2>审计时间线</h2>
        </header>
        <ol className="review-timeline">
          {detail.events.map((event, index) => (
            <li key={event.id || index}>
              <span>{event.event_type}</span>
              <strong>{event.actor}</strong>
              <p>{event.reason || "系统依据证据自动计算"}</p>
              <time>{event.created_at}</time>
            </li>
          ))}
        </ol>
        {detail.section_availability?.events === "unavailable" ? (
          <p className="review-muted">审计事件明细暂不可用。</p>
        ) : detail.events.length === 0 && (
          <p className="review-muted">尚无审计事件。</p>
        )}
      </section>

      {!readOnly && (
        <section className="review-section disposition-section">
          <header>
            <p>—</p>
            <h2>非修复处置</h2>
          </header>
          <p>重复、无需处理、暂不修复会单列统计，不计入“已闭环”。</p>
          <input
            value={dispositionReason}
            onChange={(event) => setDispositionReason(event.target.value)}
            placeholder="必填：处置理由"
          />
          <div>
            <button
              className="secondary-action"
              disabled={busy || !dispositionReason.trim()}
              onClick={() =>
                onDisposition("not_actionable", "", dispositionReason)
              }
            >
              无需处理
            </button>
            <button
              className="secondary-action"
              disabled={busy || !dispositionReason.trim()}
              onClick={() => onDisposition("wont_fix", "", dispositionReason)}
            >
              暂不修复
            </button>
          </div>
          <label>
            重复事项的 canonical issue ID
            <input
              value={duplicateTarget}
              onChange={(event) => setDuplicateTarget(event.target.value)}
            />
          </label>
          <button
            className="secondary-action"
            disabled={
              busy || !dispositionReason.trim() || !duplicateTarget.trim()
            }
            onClick={() =>
              onDisposition("duplicate", duplicateTarget, dispositionReason)
            }
          >
            标记为重复事项
          </button>
        </section>
      )}
    </article>
  );
}
