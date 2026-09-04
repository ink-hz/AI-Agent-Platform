import { useEffect, useRef, useState } from "react";

import type { HrPositionMaterialItem, HrPositionTaskKind } from "../../hrR12Types";

export const POSITION_TASKS = [
  ["jd", "生成岗位说明（JD）", "形成可修改、可下载的岗位说明"],
  ["jr", "梳理岗位要求（JR）", "整理职责、能力和任职要求"],
  ["talent_profile", "生成人才画像", "形成目标候选人的能力组合"],
  ["sourcing_strategy", "生成搜寻策略", "形成渠道、关键词和目标公司建议"],
  ["position_interview_plan", "生成面试方案", "形成结构化问题与评价重点"],
] as const satisfies ReadonlyArray<readonly [HrPositionTaskKind, string, string]>;

export function HrPositionTaskMenu({ disabled, materials, selectedMaterialIds, onSelectedMaterialIdsChange, onStart }: {
  disabled: boolean;
  materials: HrPositionMaterialItem[];
  selectedMaterialIds: string[];
  onSelectedMaterialIdsChange(ids: string[]): void;
  onStart(kind: HrPositionTaskKind): void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const firstItem = useRef<HTMLButtonElement>(null);

  const close = (restoreFocus = true) => {
    if (restoreFocus) opener.current?.focus();
    setOpen(false);
  };

  useEffect(() => {
    if (!open) return;
    firstItem.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      close();
    };
    const pointerdown = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) close(false);
    };
    document.addEventListener("keydown", keydown);
    document.addEventListener("pointerdown", pointerdown);
    return () => {
      document.removeEventListener("keydown", keydown);
      document.removeEventListener("pointerdown", pointerdown);
    };
  }, [open]);

  return <div className="hr-position-task-menu" ref={root}>
    <button aria-expanded={open} aria-haspopup="menu" disabled={disabled} ref={opener} type="button" onClick={() => setOpen((value) => !value)}>岗位任务</button>
    {open && <div aria-label="选择岗位任务" className="hr-position-task-popover" role="menu">
      <div className="hr-position-task-items">{POSITION_TASKS.map(([kind, label, description], index) => <button
        key={kind} ref={index === 0 ? firstItem : undefined} role="menuitem" type="button"
        onClick={() => { onStart(kind); close(); }}
      ><strong>{label}</strong><span>{description}</span></button>)}</div>
      <section aria-label="本次任务使用的材料" className="hr-position-task-materials">
        <h3>本次任务使用</h3>
        {materials.length === 0 ? <p>暂无岗位材料</p> : materials.map((material) => <label key={material.attachmentId}>
          <input type="checkbox" checked={selectedMaterialIds.includes(material.attachmentId)} onChange={(event) => onSelectedMaterialIdsChange(event.target.checked
            ? selectedMaterialIds.includes(material.attachmentId) ? selectedMaterialIds : [...selectedMaterialIds, material.attachmentId]
            : selectedMaterialIds.filter((id) => id !== material.attachmentId))} />
          <span>{material.filename}</span>
        </label>)}
        <small>默认不选；只把你明确勾选的材料交给本次任务。</small>
      </section>
    </div>}
  </div>;
}
