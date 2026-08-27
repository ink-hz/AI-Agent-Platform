import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/common";
import { isValidElement, type ComponentPropsWithoutRef, type ReactNode } from "react";
import ReactMarkdown, { type Components, defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";

import { MermaidDiagram } from "./MermaidDiagram";


function childText(value: ReactNode): string {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(childText).join("");
  if (isValidElement<{ children?: ReactNode }>(value)) return childText(value.props.children);
  return "";
}

function baseHeadingSlug(children: ReactNode): string {
  return childText(children)
    .normalize("NFKC")
    .trim()
    .toLocaleLowerCase("zh-CN")
    .replace(/\s+/g, "-")
    .replace(/[^\p{Letter}\p{Number}_-]/gu, "") || "section";
}

function safeUrl(url: string): string {
  const selected = url.trim();
  if (!selected) return "";
  if (selected.startsWith("#") || selected.startsWith("/")
    || selected.startsWith("./") || selected.startsWith("../")) return selected;
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(selected)?.[1]?.toLowerCase();
  if (!scheme || scheme === "http" || scheme === "https" || scheme === "mailto") {
    return defaultUrlTransform(selected);
  }
  return "";
}

type HeadingProps = ComponentPropsWithoutRef<"h1"> & { node?: unknown };


export function ArticleMarkdown({ markdown }: { markdown: string }) {
  const headingCounts = new Map<string, number>();
  const heading = (Tag: "h1" | "h2" | "h3" | "h4" | "h5" | "h6") => {
    return function ArticleHeading({ children, node, ...props }: HeadingProps) {
      void node;
      const base = baseHeadingSlug(children);
      const count = (headingCounts.get(base) ?? 0) + 1;
      headingCounts.set(base, count);
      const id = count === 1 ? base : `${base}-${count}`;
      return <Tag id={id} {...props}>{children}</Tag>;
    };
  };
  const components: Components = {
    h1: heading("h1"), h2: heading("h2"), h3: heading("h3"),
    h4: heading("h4"), h5: heading("h5"), h6: heading("h6"),
    a({ href = "", children, node, ...props }) {
      void node;
      const external = /^(?:https?:)?\/\//i.test(href);
      return <a
        {...props}
        href={href}
        rel={external ? "noopener noreferrer" : undefined}
        target={external ? "_blank" : undefined}
      >{children}</a>;
    },
    table({ node, ...props }) {
      void node;
      return <div className="article-table-scroll"><table {...props} /></div>;
    },
    pre({ children, node, ...props }) {
      void node;
      const code = Array.isArray(children) ? children[0] : children;
      if (!isValidElement<{ className?: string; children?: ReactNode }>(code)) {
        return <pre {...props}>{children}</pre>;
      }
      const language = /language-([^\s]+)/.exec(code.props.className ?? "")?.[1];
      const source = childText(code.props.children).replace(/\n$/, "");
      if (language === "mermaid") return <MermaidDiagram source={source} />;
      if (!language || !hljs.getLanguage(language)) {
        return <pre {...props}><code className={code.props.className}>{source}</code></pre>;
      }
      const highlighted = DOMPurify.sanitize(
        hljs.highlight(source, { language, ignoreIllegals: true }).value,
        { ALLOWED_TAGS: ["span"], ALLOWED_ATTR: ["class"] },
      );
      return <pre {...props}><code
        className={`hljs language-${language}`}
        dangerouslySetInnerHTML={{ __html: highlighted }}
      /></pre>;
    },
  };

  return <div className="article-markdown">
    <ReactMarkdown components={components} remarkPlugins={[remarkGfm]} urlTransform={safeUrl}>
      {markdown}
    </ReactMarkdown>
  </div>;
}
