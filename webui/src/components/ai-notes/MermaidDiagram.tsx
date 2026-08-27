import DOMPurify from "dompurify";
import { useEffect, useState } from "react";


let diagramSequence = 0;
let initialized = false;


export function mermaidImageSource(renderedSvg: string): string {
  const sanitized = DOMPurify.sanitize(renderedSvg, {
    USE_PROFILES: { svg: true, svgFilters: true },
    FORBID_TAGS: ["script", "foreignObject"],
  });
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(sanitized)}`;
}


export function MermaidDiagram({ source }: { source: string }) {
  const [imageSource, setImageSource] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let current = true;
    const identifier = `ai-note-mermaid-${++diagramSequence}`;
    setImageSource(null);
    setFailed(false);
    void import("mermaid").then(async ({ default: mermaid }) => {
      if (!initialized) {
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "neutral",
          htmlLabels: false,
          flowchart: { htmlLabels: false },
        });
        initialized = true;
      }
      const rendered = await mermaid.render(identifier, source);
      if (!current) return;
      setImageSource(mermaidImageSource(rendered.svg));
    }).catch(() => {
      if (current) setFailed(true);
    });
    return () => { current = false; };
  }, [source]);

  if (failed) return <div className="mermaid-fallback" role="status">
    <p>图表暂时无法渲染</p>
    <pre><code>{source}</code></pre>
  </div>;
  if (!imageSource) return <div className="mermaid-loading" role="status">正在渲染图表</div>;
  return <div className="mermaid-diagram">
    <img alt="Mermaid 图表" onError={() => setFailed(true)} src={imageSource} />
  </div>;
}
