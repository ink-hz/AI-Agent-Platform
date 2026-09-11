import { useEffect, useState } from "react";
import { platformPath } from "../../auth";
import { HrLegacyPanoramaWorkspace } from "./HrLegacyPanoramaWorkspace";
import { HrResearchWorkspace } from "./HrResearchWorkspace";
export function HrPanoramaWorkspace(props: Parameters<typeof HrLegacyPanoramaWorkspace>[0]) {
  const [search, setSearch] = useState(() => window.location.search);
  useEffect(() => {
    const sync = () => setSearch(window.location.search);
    window.addEventListener("popstate", sync); window.addEventListener("platform:navigate", sync);
    return () => { window.removeEventListener("popstate", sync); window.removeEventListener("platform:navigate", sync); };
  }, []);
  const query = new URLSearchParams(search);
  const archive = ["archive", "topics"].includes(query.get("view") ?? "") || ["company", "topic", "bundle_id"].some(key => query.has(key));
  return archive ? <><div className="hr-research-archive-banner">历史情报归档 · 保留原发布内容与已有引用 <a href={platformPath("/hr/panorama")}>阅读新版研究 →</a></div><HrLegacyPanoramaWorkspace {...props} /></> : <HrResearchWorkspace account={props.account} />;
}
