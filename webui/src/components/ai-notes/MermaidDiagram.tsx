import DOMPurify from "dompurify";
import { useEffect, useRef, useState } from "react";

import { MermaidLightbox } from "./MermaidLightbox";
import { mermaidMetadata } from "./mermaidMetadata";


let diagramSequence = 0;
let initialized = false;


export const AI_NOTES_MERMAID_CONFIG = {
  startOnLoad: false,
  securityLevel: "strict",
  theme: "neutral",
  themeVariables: {
    background: "#FFFFFF",
    clusterBkg: "#FFFFFF",
    clusterBorder: "#CBD5E1",
  },
  htmlLabels: false,
  flowchart: { htmlLabels: false },
} as const;


function supportsModalDialog(): boolean {
  return typeof HTMLDialogElement !== "undefined"
    && typeof HTMLDialogElement.prototype.showModal === "function";
}


export function mermaidImageSource(renderedSvg: string): string {
  const sanitized = DOMPurify.sanitize(renderedSvg, {
    USE_PROFILES: { svg: true, svgFilters: true },
    FORBID_TAGS: ["script", "foreignObject"],
  });
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(sanitized)}`;
}


export function MermaidDiagram({ source }: { source: string }) {
  const metadata = mermaidMetadata(source);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef(false);
  const [imageSource, setImageSource] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let current = true;
    const identifier = `ai-note-mermaid-${++diagramSequence}`;
    setImageSource(null);
    setFailed(false);
    setExpanded(false);
    void import("mermaid").then(async ({ default: mermaid }) => {
      if (!initialized) {
        mermaid.initialize(AI_NOTES_MERMAID_CONFIG);
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

  useEffect(() => {
    if (expanded || !restoreFocusRef.current) return;
    restoreFocusRef.current = false;
    triggerRef.current?.focus();
  }, [expanded]);

  function closeExpanded() {
    restoreFocusRef.current = true;
    setExpanded(false);
  }

  if (failed) return <div className="mermaid-fallback" role="status">
    <p>图表暂时无法渲染</p>
    <pre><code>{source}</code></pre>
  </div>;
  if (!imageSource) return <div className="mermaid-loading" role="status">正在渲染图表</div>;
  return <figure className="mermaid-diagram">
    <button
      aria-label={`查看大图：${metadata.title}`}
      className="mermaid-diagram-trigger"
      onClick={() => { if (supportsModalDialog()) setExpanded(true); }}
      ref={triggerRef}
      type="button"
    >
      <img alt={metadata.title} onError={() => setFailed(true)} src={imageSource} />
    </button>
    {metadata.description && <figcaption className="mermaid-visually-hidden">{metadata.description}</figcaption>}
    {expanded && <MermaidLightbox
      description={metadata.description}
      imageSource={imageSource}
      onClose={closeExpanded}
      title={metadata.title}
    />}
  </figure>;
}
