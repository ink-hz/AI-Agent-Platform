import type { Account } from "../auth";

export type PermissionSection = "administrators" | "fae" | "voc" | "observers" | "partners";

export function PermissionNavigation({ account, section, onSelect }: {
  account: Account;
  section: PermissionSection;
  onSelect: (section: PermissionSection) => void;
}) {
  const owner = account.role === "platform_owner";
  const button = (key: PermissionSection, label: string) => <button type="button" data-section={key}
    aria-pressed={section === key} onClick={event => {
      onSelect(key);
      const menu = event.currentTarget.closest("details");
      if (menu) { menu.open = false; menu.querySelector("summary")?.focus(); }
    }}>{label}</button>;
  return <nav className="permission-navigation" aria-label="权限分类">
    {button("administrators", "平台管理员")}
    {owner && button("fae", "FAE 权限")}
    {owner && button("voc", "VOC 权限")}
    <details className={section === "observers" || section === "partners" ? "is-current" : undefined}>
      <summary>其他授权</summary>
      <div className="permission-navigation-menu">
        {button("observers", "观察者权限")}
        {owner && button("partners", "合作方权限")}
      </div>
    </details>
  </nav>;
}
