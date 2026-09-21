import { platformPath, type Account } from "../auth";

type Section = "administrators" | "fae" | "voc" | "observers" | "partners";

export function PermissionNavigation({ account, section }: { account: Account; section: Section }) {
  const owner = account.role === "platform_owner";
  const link = (key: Section, label: string, path: string) => <a href={platformPath(path)}
    aria-current={section === key ? "page" : undefined}>{label}</a>;
  return <nav className="permission-navigation" aria-label="权限分类">
    {link("administrators", "平台管理员", "/admin/identity")}
    {owner && link("fae", "FAE 权限", "/fae/manage/access")}
    {owner && link("voc", "VOC 权限", "/admin/voc/access")}
    <details className={section === "observers" || section === "partners" ? "is-current" : undefined}>
      <summary>其他授权</summary>
      <div className="permission-navigation-menu">
        {link("observers", "观察者权限", "/admin/identity/observers")}
        {owner && link("partners", "合作方权限", "/admin/identity/partners")}
      </div>
    </details>
  </nav>;
}
