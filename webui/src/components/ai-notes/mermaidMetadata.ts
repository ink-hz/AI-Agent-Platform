export type MermaidMetadata = {
  title: string;
  description: string | null;
};


function directive(source: string, name: "accTitle" | "accDescr"): string | null {
  const value = new RegExp(`^[ \\t]*${name}:[ \\t]*(.*?)[ \\t]*$`, "m").exec(source)?.[1]?.trim();
  return value || null;
}


export function mermaidMetadata(source: string): MermaidMetadata {
  return {
    title: directive(source, "accTitle") ?? "Mermaid 图表",
    description: directive(source, "accDescr"),
  };
}
