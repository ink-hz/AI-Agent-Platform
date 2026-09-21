import { type Account } from "../auth";
import { PermissionNavigation } from "../components/PermissionNavigation";
import { FaeAccessPanel } from "../components/FaeAccessPanel";
import { VocAccessPanel } from "../components/VocAccessPanel";
import { PartnerAccessPanel } from "./PartnerAccessPanel";
import { ObserverAccessPage } from "./ObserverAccessPage";

export type PermissionSection = "observers" | "partners" | "fae" | "voc";

export function PermissionAccessPage({ account, section }: { account: Account; section: PermissionSection }) {
  const allowed = account.role === "platform_owner" || (section === "observers" && account.role === "platform_admin");
  if (!allowed) return <section className="permission-state" role="alert"><h1>无权访问</h1><p>请联系苍渊。</p></section>;
  return <div className="permission-access-page">
    <h1>账号与权限</h1>
    <PermissionNavigation account={account} section={section} />
    {section === "fae" && <FaeAccessPanel account={account} />}
    {section === "voc" && <VocAccessPanel account={account} />}
    {section === "partners" && <PartnerAccessPanel account={account} />}
    {section === "observers" && <ObserverAccessPage account={account} />}
  </div>;
}
