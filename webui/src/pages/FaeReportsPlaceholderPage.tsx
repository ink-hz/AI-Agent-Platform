import { FaeWorkbenchShell } from "../components/fae-workbench/FaeWorkbenchShell";


export function FaeReportsPlaceholderPage() {
  return <FaeWorkbenchShell currentSection="reports">
    <section className="fae-workbench__empty" data-fae-reports-state="integration-pending" role="status">
      <h2>分析报告尚未接入</h2>
      <p>Sessions 与问题治理可以正常使用；这里不会用演示数据代替 FAE 的真实分析结果。</p>
    </section>
  </FaeWorkbenchShell>;
}
