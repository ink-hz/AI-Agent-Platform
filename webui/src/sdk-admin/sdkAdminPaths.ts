export type SdkAdminSection = "overview" | "products" | "delivery" | "diagnostics" | "knowledge";

export function parseSdkAdminPath(path: string): { section: SdkAdminSection; documentId?: string } | null {
  if (/^\/sdk-admin\/?$/.test(path)) return { section: "overview" };
  const section = /^\/sdk-admin\/(products|delivery|diagnostics|knowledge)\/?$/.exec(path);
  if (section) return { section: section[1] as SdkAdminSection };
  const document = /^\/sdk-admin\/knowledge\/([A-Za-z0-9][A-Za-z0-9._-]{0,159})$/.exec(path);
  return document ? { section: "knowledge", documentId: document[1] } : null;
}
