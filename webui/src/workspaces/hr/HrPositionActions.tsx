export function HrPositionActions({ title, readOnly, onDraft, onCandidates }: {
  title: string;
  readOnly: boolean;
  onDraft(text: string): void;
  onCandidates(): void;
}) {
  return <div className="hr-position-actions" role="group" aria-label="推进当前岗位">
    <button type="button" disabled={readOnly} onClick={() => onDraft(`请梳理《${title}》的 JD / JR：先读取现有岗位职责、任职要求和已确认标准，明确工作目标、人才画像与待澄清问题；需要调整的内容列为建议，供我确认。`)}>梳理 JD / JR</button>
    <button type="button" disabled={readOnly} onClick={() => onDraft(`请为《${title}》制定人才搜寻策略，结合岗位要求说明目标人才背景、可迁移经验、优先渠道和搜索条件，并列出需要我补充的信息。`)}>制定搜寻策略</button>
    <button type="button" disabled={readOnly} onClick={onCandidates}>候选人 / 面试</button>
  </div>;
}
