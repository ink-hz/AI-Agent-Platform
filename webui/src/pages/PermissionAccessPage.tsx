import { platformPath, type Account } from "../auth";
import { FaeAccessPanel } from "../components/FaeAccessPanel";
import { VocAccessPanel } from "../components/VocAccessPanel";
import { PartnerAccessPanel } from "./PartnerAccessPanel";
import { ObserverAccessPage } from "./ObserverAccessPage";

export type PermissionSection = "observers" | "partners" | "fae" | "voc";

export function PermissionAccessPage({ account, section }: { account: Account; section: PermissionSection }) {
  const allowed = account.role === "platform_owner" || (section === "observers" && account.role === "platform_admin");
  if (!allowed) return <section className="permission-state" role="alert"><h1>无权访问</h1><p>请联系苍渊。</p></section>;
  const backPath = section === "fae" ? "/fae/manage/" : section === "voc" ? "/voc/manage/" : "/admin/identity";
  const backLabel = section === "fae" ? "返回技术支持" : section === "voc" ? "返回客户洞察" : "返回账号与权限";
  return <div className="permission-access-page">
    <a className="permission-back" href={platformPath(backPath)}>{backLabel}</a>
    {section === "fae" && <FaeAccessPanel account={account} />}
    {section === "voc" && <VocAccessPanel account={account} />}
    {section === "partners" && <PartnerAccessPanel account={account} />}
    {section === "observers" && <ObserverAccessPage account={account} />}
  </div>;
}
