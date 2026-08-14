import { useEffect, useState } from "react";

import { listGovernanceAudit, type GovernanceEvent } from "../auth";


export function GovernancePage() {
  const [events, setEvents] = useState<GovernanceEvent[]>([]);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    void listGovernanceAudit().then(setEvents).catch(() => setFailed(true));
  }, []);
  return <section className="governance-page">
    <header><p>IMMUTABLE AUDIT</p><h1>治理审计</h1><span>仅展示脱敏的身份、授权和特权访问事件。</span></header>
    {failed && <p className="auth-message is-error" role="alert">审计服务暂不可用。</p>}
    <div className="governance-list">
      {events.map((event) => <article key={event.audit_event_id}>
        <div><strong>{event.event_type}</strong><span>{event.result}</span></div>
        <p>{event.reason_code}</p><time dateTime={event.occurred_at}>{event.occurred_at}</time>
      </article>)}
      {!failed && events.length === 0 && <p>暂无治理审计记录。</p>}
    </div>
  </section>;
}
