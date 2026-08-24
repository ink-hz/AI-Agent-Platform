import { platformPath } from "../auth";


export function BrainPreparingPage() {
  return (
    <section className="brain-preparing" aria-labelledby="brain-preparing-heading">
      <p className="eyebrow">Agent Platform</p>
      <h1 id="brain-preparing-heading">Agent 大脑正在准备</h1>
      <p>顶层调度能力尚未正式启用。您可以先直接使用已经开放的专业 Agent。</p>
      <a className="primary-action" href={platformPath("/agents")}>打开专业 Agent</a>
    </section>
  );
}
