import DOMPurify from "dompurify";
import { useEffect, useState } from "react";


let diagramSequence = 0;
let initialized = false;


export function MermaidDiagram({ source }: { source: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let current = true;
    const identifier = `ai-note-mermaid-${++diagramSequence}`;
    setSvg(null);
    setFailed(false);
    void import("mermaid").then(async ({ default: mermaid }) => {
      if (!initialized) {
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "neutral",
        });
        initialized = true;
      }
      const rendered = await mermaid.render(identifier, source);
      if (!current) return;
      const sanitized = DOMPurify.sanitize(rendered.svg, {
        USE_PROFILES: { svg: true, svgFilters: true },
        FORBID_TAGS: ["script", "foreignObject"],
      });
      setSvg(sanitized);
    }).catch(() => {
      if (current) setFailed(true);
    });
    return () => { current = false; };
  }, [source]);

  if (failed) return <div className="mermaid-fallback" role="status">
    <p>图表暂时无法渲染</p>
    <pre><code>{source}</code></pre>
  </div>;
  if (!svg) return <div className="mermaid-loading" role="status">正在渲染图表</div>;
  return <div
    aria-label="Mermaid 图表"
    className="mermaid-diagram"
    dangerouslySetInnerHTML={{ __html: svg }}
    role="img"
  />;
}
