import type { Account } from "../auth";
import type { PermissionSection as WorkspaceSection } from "../components/PermissionNavigation";
import { IdentityManagementPage } from "./IdentityManagementPage";

export type PermissionSection = Exclude<WorkspaceSection, "administrators">;

// Existing deep links open the corresponding section of the same workspace.
export function PermissionAccessPage({ account, section }: { account: Account; section: PermissionSection }) {
  return <IdentityManagementPage account={account} initialSection={section} />;
}
