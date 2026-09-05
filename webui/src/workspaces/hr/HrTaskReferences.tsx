import type { HrTaskReference } from "../../hrR12Types";

export function HrTaskReferences({ references }: { references: HrTaskReference[] }) {
  if (references.length === 0) return null;
  return <details className="hr-task-references">
    <summary>本次参考 {references.length}</summary>
    <ul>{references.map((reference) => <li key={`${reference.sourceType}:${reference.sourceId}`}>
      <strong>{reference.displayLabel}</strong>
      <span>{reference.selectedReason}</span>
    </li>)}</ul>
  </details>;
}
