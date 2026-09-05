import { useEffect, useState } from "react";

import type { HrR12Api } from "../../hrR12Api";
import type { HrOfficialPositionVersion } from "../../hrR12Types";
import type { HrPositionDetail } from "../../hrTypes";

function display(value: string | null | undefined): string {
  return value?.trim() || "官网未公开";
}

function displayDate(value: string): string {
  const parsed = new Date(value);
  return Number.isFinite(parsed.valueOf()) ? parsed.toLocaleString("zh-CN", { hour12: false }) : value;
}

export function HrOfficialPositionPanel({ api, positionId, currentSourceVersion, fallback }: {
  api: Pick<HrR12Api, "officialVersions" | "officialVersion" | "downloadOfficialVersion">;
  positionId: string;
  currentSourceVersion: string | null;
  fallback?: Pick<HrPositionDetail, "title" | "officialJobId" | "department" | "locations">;
}) {
  const [versions, setVersions] = useState<HrOfficialPositionVersion[] | null>(null);
  const [selected, setSelected] = useState<HrOfficialPositionVersion | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setVersions(null); setSelected(null); setNotice(null);
    void api.officialVersions(positionId, controller.signal).then((loaded) => {
      if (controller.signal.aborted) return;
      setVersions(loaded);
      setSelected(loaded.find((item) => item.sourceVersion === currentSourceVersion) ?? loaded[0] ?? null);
    }).catch(() => { if (!controller.signal.aborted) setNotice("官网岗位原文暂时无法读取，请稍后重试。"); });
    return () => controller.abort();
  }, [api, currentSourceVersion, positionId]);

  const chooseVersion = (officialVersionId: string) => {
    const controller = new AbortController();
    setNotice(null);
    void api.officialVersion(positionId, officialVersionId, controller.signal).then(setSelected)
      .catch(() => setNotice("所选历史版本暂时无法读取，请重试。"));
  };

  const download = async () => {
    if (!selected || downloading) return;
    setDownloading(true); setNotice(null);
    try {
      const result = await api.downloadOfficialVersion(positionId, selected.officialVersionId);
      const href = URL.createObjectURL(result.blob);
      const link = document.createElement("a");
      link.href = href; link.download = result.filename; link.click();
      URL.revokeObjectURL(href);
    } catch { setNotice("官网岗位原文下载失败，请重试。"); }
    finally { setDownloading(false); }
  };

  return <section aria-label="官网岗位原文" className="hr-official-position">
    <header><div><span>OFFICIAL SOURCE</span><h3>官网岗位原文</h3></div>
      {versions && versions.length > 0 && <div className="hr-official-position-actions">
        <label>历史版本（{versions.length}）<select aria-label="官网岗位历史版本" value={selected?.officialVersionId ?? ""}
          onChange={(event) => chooseVersion(event.target.value)}>
          {versions.map((item) => <option key={item.officialVersionId} value={item.officialVersionId}>
            {item.sourceVersion} · {displayDate(item.lastObservedAt)}
          </option>)}
        </select></label>
        <button disabled={!selected || downloading} onClick={() => void download()} type="button">{downloading ? "正在下载…" : "下载原文"}</button>
      </div>}
    </header>
    {!versions && !notice && <p aria-live="polite">正在读取官网岗位原文…</p>}
    {notice && <p role="alert">{notice}</p>}
    {versions?.length === 0 && <><p>该岗位暂时没有可用的官网历史版本，先显示岗位库已同步的概要。</p>
      {fallback && <article className="hr-official-position-fallback"><div className="hr-official-position-title"><div><strong>{fallback.title}</strong><span>{fallback.officialJobId ?? "官网编号未同步"}</span></div></div><dl>
        <div><dt>部门</dt><dd>{display(fallback.department)}</dd></div>
        <div><dt>地点</dt><dd>{fallback.locations.length > 0 ? fallback.locations.join("、") : "官网未公开"}</dd></div>
      </dl></article>}
    </>}
    {selected && <article>
      <div className="hr-official-position-title"><div><strong>{selected.title}</strong><span>{selected.officialJobId}</span></div><em>{selected.officialStatus === "active" ? "官网在招" : "历史状态"}</em></div>
      <dl>
        <div><dt>部门</dt><dd>{display(selected.department)}</dd></div>
        <div><dt>地点</dt><dd>{selected.locations.length > 0 ? selected.locations.join("、") : "官网未公开"}</dd></div>
        <div><dt>职位类别</dt><dd>{display(selected.category)}</dd></div>
        <div><dt>职位子类</dt><dd>{display(selected.subcategory)}</dd></div>
        <div><dt>招聘人数</dt><dd>{selected.headcount > 0 ? selected.headcount : "官网未公开"}</dd></div>
        <div><dt>学历</dt><dd>{display(selected.degree)}</dd></div>
        <div><dt>用工类型</dt><dd>{display(selected.employmentType)}</dd></div>
        <div><dt>薪资</dt><dd>{display(selected.salary)}</dd></div>
      </dl>
      <section><h4>岗位职责</h4><p>{display(selected.duty)}</p></section>
      <section><h4>任职要求</h4><p>{display(selected.requirement)}</p></section>
      <footer>官网更新时间：{displayDate(selected.sourceChangedAt)} · 平台采集：{displayDate(selected.lastObservedAt)} · 来源版本：{selected.sourceVersion}</footer>
    </article>}
  </section>;
}
