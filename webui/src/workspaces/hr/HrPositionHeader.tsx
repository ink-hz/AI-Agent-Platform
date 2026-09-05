import type { HrPositionDetail } from "../../hrTypes";
import { PlatformLink } from "../../components/PlatformLink";

function statusLabel(detail: HrPositionDetail): string {
  if (detail.internalStatus === "archived") return "已归档";
  if (detail.officialStatus === "inactive") return "官网已下线";
  if (detail.officialStatus === "stale" || detail.officialStatus === "suspected_inactive") return "官网状态待核验";
  return "进行中";
}

export function HrPositionHeader({ detail, readOnly, onOpenDetails, onOpenMaterials, onNewConversation }: {
  detail: HrPositionDetail;
  readOnly: boolean;
  onOpenDetails(): void;
  onOpenMaterials?(): void;
  onNewConversation(): void;
}) {
  const subtitle = [detail.department, ...detail.locations].filter(Boolean).join(" · ") || "岗位信息待完善";
  return <header className="hr-position-bar">
    <PlatformLink href="/hr/positions">← 岗位</PlatformLink>
    <div className="hr-position-bar-copy"><h1>{detail.title}</h1><p>{subtitle}</p></div>
    <span className="hr-position-status-pill">{statusLabel(detail)}</span>
    <div className="hr-position-bar-actions">
      {onOpenMaterials && <button type="button" onClick={onOpenMaterials}>会话材料</button>}
      <button type="button" onClick={onOpenDetails}>岗位资料</button>
      <button className="is-primary" disabled={readOnly} type="button" onClick={onNewConversation}>＋ 新对话</button>
    </div>
  </header>;
}
