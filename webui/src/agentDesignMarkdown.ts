export function prepareDesignMarkdown(source: string) {
  const headings: Array<{ id: string; title: string; level: number }> = [];
  const headingIds: Record<number, string> = {};
  const used = new Set<string>();
  let pending: string | null = null;
  let fence: { char: string; length: number } | null = null;
  const lines = source.split("\n").map((line, index) => {
    const marker = /^\s{0,3}(`{3,}|~{3,})/.exec(line)?.[1];
    if (marker) {
      if (!fence) fence = { char: marker[0], length: marker.length };
      else if (marker[0] === fence.char && marker.length >= fence.length) fence = null;
      return line;
    }
    if (fence) return line;
    const anchor = /^\s*<a id="([A-Za-z][A-Za-z0-9_-]*)"><\/a>\s*$/.exec(line);
    if (anchor) { pending = anchor[1]; return ""; }
    const heading = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (heading) {
      const title = heading[2];
      const base = pending ?? (title.normalize("NFKC").trim().toLocaleLowerCase("zh-CN")
        .replace(/\s+/g, "-").replace(/[^\p{Letter}\p{Number}_-]/gu, "") || "section");
      let id = base; let suffix = 2;
      while (used.has(id)) id = `${base}-${suffix++}`;
      used.add(id); pending = null; headingIds[index + 1] = id;
      if (heading[1].length <= 2) headings.push({ id, title, level: heading[1].length });
    }
    return line;
  });
  return { markdown: lines.join("\n"), headings, headingIds };
}
