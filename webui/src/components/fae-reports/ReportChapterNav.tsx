const CHAPTERS = [
  ["report-usage", "01", "使用情况"],
  ["report-value", "02", "业务价值"],
  ["report-effectiveness", "03", "回答效果"],
  ["report-improvement", "04", "业务洞察与改进"],
] as const;


export function ReportChapterNav() {
  return <nav className="fae-outcome-chapters" aria-label="报告章节">
    {CHAPTERS.map(([id, number, label]) => <a href={`#${id}`} key={id}><span>{number}</span>{label}</a>)}
  </nav>;
}
