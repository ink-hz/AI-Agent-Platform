import type { HrTaskReference } from "../../hrR12Types";

export function HrTaskReferences({ references }: { references: HrTaskReference[] }) {
  if (references.length === 0) return null;
  return <details className="hr-task-references">
    <summary>本次参考 {references.length}</summary>
    <ul>{references.map((reference) => <li key={`${reference.sourceType}:${reference.sourceId}`}>
      <strong>{reference.displayLabel}</strong>
      <span>{reference.selectedReason}</span>
      {reference.freshness && <small>数据截至 {reference.freshness}</small>}
      {reference.sourceUrl && <a href={reference.sourceUrl} rel="noreferrer" target="_blank">查看公开来源 ↗</a>}
      {reference.evidenceSha256 && <code title={reference.evidenceSha256}>证据 {reference.evidenceSha256.slice(0, 12)}</code>}
    </li>)}</ul>
  </details>;
}
