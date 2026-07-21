import { agentIdentity } from "./identity";
import {
  errorLabel,
  formatCheckedAt,
  formatLatency,
  formatUptime,
  isStale,
  statusMeta,
} from "./status";
import type { InstanceStatus } from "./types";


interface AgentCardProps {
  instance: InstanceStatus;
  now: Date;
}


export function AgentCard({ instance, now }: AgentCardProps) {
  const identity = agentIdentity(instance.id, instance.name);
  const meta = statusMeta(instance.status);
  const stale = isStale(instance.checked_at, now);
  const probeError = errorLabel(instance.error);

  return (
    <article
      className={`instance-card agent-${identity.accent} ${meta.tone}${stale ? " stale" : ""}`}
    >
      <div className="agent-card-head">
        <span className="agent-avatar" aria-hidden="true">
          {identity.glyph}
        </span>
        <div className="agent-heading">
          <p>{identity.domain}</p>
          <h3>{identity.name}</h3>
        </div>
        <span className={`status-badge ${meta.tone}`}>
          <i aria-hidden="true" />
          {meta.label}
        </span>
      </div>

      <p className="agent-description">{identity.description}</p>

      <dl className="agent-metrics" aria-label={`${identity.name} 运行指标`}>
        <div className="metric-chip">
          <dt>运行时长</dt>
          <dd>{formatUptime(instance.uptime_seconds)}</dd>
        </div>
        <div className="metric-chip">
          <dt>响应延迟</dt>
          <dd>{formatLatency(instance.latency_ms)}</dd>
        </div>
        <div className="metric-chip">
          <dt>API 端口</dt>
          <dd>{instance.port}</dd>
        </div>
      </dl>

      <footer className="agent-card-foot">
        <div className="runtime-name">
          <span>运行实例</span>
          <strong>{instance.pm2_name}</strong>
        </div>
        <div className="probe-meta">
          <span className="readonly-label">只读监控</span>
          <span>{formatCheckedAt(instance.checked_at)}</span>
        </div>
      </footer>

      {(stale || probeError) && (
        <div className="agent-alert" role="status">
          {probeError ?? "监控数据已过期，等待下一次探测。"}
        </div>
      )}
    </article>
  );
}
