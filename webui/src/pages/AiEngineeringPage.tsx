import { useCallback, useEffect, useMemo, useState, type MouseEvent, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  aiEngineeringClient,
  AiEngineeringApiError,
  type AiEngineeringClient,
  type AiEngineeringDocument,
  type AiEngineeringDocumentSlug,
  type AiEngineeringIndex,
} from "../aiEngineeringApi";
import { platformPath, type Account } from "../auth";
import { PLATFORM_TITLE, useDocumentTitle } from "../documentTitle";
import { navigate } from "../router";


const DOCUMENT_BY_FILENAME: Record<string, AiEngineeringDocumentSlug> = {
  "2026-09-20-panorama-v1.md": "overview",
  "2026-09-20-panorama-reading-guide.md": "reading",
  "2026-09-20-domain-cards.md": "domains",
  "2026-09-20-public-disclosures.md": "finance",
  "2026-09-20-product-family-inventory.md": "products",
  "2026-09-20-existing-ai-assets.md": "assets",
};
const ACCESS_PROBE_TIMEOUT_MS = 5_000;

type ResolvedUrl = { kind: "external" | "internal" | "asset" | "anchor" | "unsafe"; href?: string };

function basename(value: string): string {
  return value.split(/[?#]/, 1)[0].replace(/\\/g, "/").split("/").pop() ?? "";
}

function resolveMarkdownUrl(raw: string, image = false): ResolvedUrl {
  const value = raw.trim();
  if (!value) return { kind: "unsafe" };
  if (value.startsWith("#")) return { kind: "anchor", href: value };
  if (/^https:\/\//i.test(value)) return { kind: "external", href: value };
  const file = basename(value);
  if (["panorama.png", "2026-09-20-panorama-v1.png"].includes(file)) {
    return {
      kind: "asset",
      href: platformPath("/api/v1/ai-engineering/assets/panorama.png"),
    };
  }
  if (["panorama.svg", "2026-09-20-panorama-v1.svg"].includes(file)) {
    return {
      kind: "asset",
      href: platformPath(`/api/v1/ai-engineering/assets/${image ? "panorama.png" : "panorama.svg"}`),
    };
  }
  const slug = DOCUMENT_BY_FILENAME[file];
  if (slug) return { kind: "internal", href: platformPath(`/ai-engineering?document=${slug}`) };
  return { kind: "unsafe" };
}

function markdownComponents(onAssetError?: () => void): Components {
  return {
    a({ href = "", children, node, ...props }) {
      void node;
      const resolved = resolveMarkdownUrl(href);
      if (!resolved.href) return <span className="ai-engineering-unsafe-link">{children}</span>;
      const external = resolved.kind === "external";
      return <a
        {...props}
        href={resolved.href}
        rel={external ? "noopener noreferrer" : undefined}
        target={external ? "_blank" : undefined}
      >{children}</a>;
    },
    img({ src = "", alt = "", node, ...props }) {
      void node;
      const resolved = resolveMarkdownUrl(src, true);
      if (!resolved.href || resolved.kind !== "asset") return null;
      return <img {...props} alt={alt} className="ai-engineering-diagram" loading="lazy" onError={onAssetError} src={resolved.href} />;
    },
    table({ node, ...props }) {
      void node;
      return <div className="ai-engineering-table-scroll"><table {...props} /></div>;
    },
  };
}

export function AiEngineeringMarkdown({ markdown, onAssetError }: { markdown: string; onAssetError?: () => void }) {
  const components = useMemo(() => markdownComponents(onAssetError), [onAssetError]);
  return <div className="ai-engineering-markdown">
    <ReactMarkdown components={components} remarkPlugins={[remarkGfm]} skipHtml>
      {markdown}
    </ReactMarkdown>
  </div>;
}

function isAuthorizationFailure(error: unknown): boolean {
  return error instanceof AiEngineeringApiError && (error.status === 401 || error.status === 403);
}

function pageState(title: string, description: string, role: "status" | "alert" = "status") {
  return <section className="ai-engineering-state" role={role}><h1>{title}</h1><p>{description}</p></section>;
}

interface LandingProps {
  account: Account;
  client?: AiEngineeringClient;
  direct?: boolean;
  fallback: ReactNode;
  selectedDocument?: AiEngineeringDocumentSlug;
  onNavigate?: (path: string) => void;
}

interface SessionProps extends LandingProps {
  client: AiEngineeringClient;
  onNavigate: (path: string) => void;
}

function AiEngineeringSession({ account, client, direct = false, fallback, selectedDocument, onNavigate }: SessionProps) {
  const [access, setAccess] = useState<"checking" | "allowed" | "fallback" | "denied">("checking");
  const [index, setIndex] = useState<AiEngineeringIndex | null>(null);
  const [document, setDocument] = useState<AiEngineeringDocument | null>(null);
  const [contentError, setContentError] = useState(false);
  const selected = selectedDocument ?? "overview";
  const clearFailedAsset = useCallback(() => {
    setIndex(null);
    setDocument(null);
    setContentError(true);
  }, []);
  const title = index && access === "allowed"
    ? `${index.title} · ${PLATFORM_TITLE}`
    : access === "fallback"
      ? `Agent 大脑 · ${PLATFORM_TITLE}`
      : direct
        ? `AI 工程全景 · ${PLATFORM_TITLE}`
        : PLATFORM_TITLE;
  useDocumentTitle(title);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      controller.abort();
      setAccess(direct ? "denied" : "fallback");
    }, ACCESS_PROBE_TIMEOUT_MS);
    setAccess("checking");
    setIndex(null);
    setDocument(null);
    setContentError(false);
    void client.fetchAccess(controller.signal).then(({ allowed }) => {
      if (controller.signal.aborted) return;
      window.clearTimeout(timeout);
      setAccess(allowed ? "allowed" : direct ? "denied" : "fallback");
    }).catch(() => {
      if (!controller.signal.aborted) {
        window.clearTimeout(timeout);
        setAccess(direct ? "denied" : "fallback");
      }
    });
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [account.internal_user_id, client, direct]);

  useEffect(() => {
    if (access !== "allowed") return;
    const controller = new AbortController();
    setIndex(null);
    setDocument(null);
    setContentError(false);
    void client.fetchIndex(controller.signal).then((loaded) => {
      if (!controller.signal.aborted) setIndex(loaded);
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      setIndex(null);
      setDocument(null);
      if (isAuthorizationFailure(error)) setAccess(direct ? "denied" : "fallback");
      else setContentError(true);
    });
    return () => controller.abort();
  }, [access, client, direct]);

  useEffect(() => {
    if (access !== "allowed" || !index) return;
    const available = new Set(index.documents.map(({ slug }) => slug));
    const slug = available.has(selected) ? selected : "overview";
    const controller = new AbortController();
    setDocument(null);
    setContentError(false);
    void client.fetchDocument(slug, controller.signal).then((loaded) => {
      if (!controller.signal.aborted) setDocument(loaded);
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      setDocument(null);
      if (isAuthorizationFailure(error)) {
        setIndex(null);
        setAccess(direct ? "denied" : "fallback");
      } else {
        setContentError(true);
      }
    });
    return () => controller.abort();
  }, [access, client, direct, index, selected]);

  if (access === "fallback") return <>{fallback}</>;
  if (access === "denied") return pageState("无权访问 AI 工程全景", "当前账号不在该内部资料的授权范围内。", "alert");
  if (access === "checking") return pageState("正在确认访问权限", "正在安全地检查该账号的阅读权限。");
  if (contentError) return pageState("AI 工程全景暂时不可用", "受保护内容暂时无法读取，请稍后再试。", "alert");
  if (!index || !document) return pageState("正在打开 AI 工程全景", "正在读取受保护的最新版本。");

  const follow = (event: MouseEvent<HTMLAnchorElement>, path: string) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onNavigate(path);
  };
  return <article className="ai-engineering-page">
    <header className="ai-engineering-hero">
      <div>
        <p>AI ENGINEERING PANORAMA</p>
        <h1>{index.title}</h1>
        <span>版本 {index.version} · 更新于 <time dateTime={index.updated_at}>{new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium" }).format(new Date(index.updated_at))}</time></span>
      </div>
      <nav aria-label="AI 工程快捷入口">
        <a href={platformPath("/brain")} onClick={(event) => follow(event, "/brain")}>Agent 大脑</a>
        <a href={platformPath("/agents")} onClick={(event) => follow(event, "/agents")}>专业 Agent</a>
      </nav>
    </header>
    <div className="ai-engineering-layout">
      <aside>
        <h2>全景资料</h2>
        <nav aria-label="AI 工程文档">
          {index.documents.map((item) => {
            const path = `/ai-engineering?document=${item.slug}`;
            return <a
              aria-current={document.slug === item.slug ? "page" : undefined}
              className={document.slug === item.slug ? "is-current" : undefined}
              href={platformPath(path)}
              key={item.slug}
              onClick={(event) => follow(event, path)}
            >{item.title}</a>;
          })}
        </nav>
      </aside>
      <section className="ai-engineering-reader" aria-live="polite">
        <AiEngineeringMarkdown markdown={document.markdown} onAssetError={clearFailedAsset} />
      </section>
    </div>
  </article>;
}

export function AiEngineeringLanding({
  account,
  client = aiEngineeringClient,
  onNavigate = navigate,
  ...props
}: LandingProps) {
  return <AiEngineeringSession
    {...props}
    account={account}
    client={client}
    key={account.internal_user_id}
    onNavigate={onNavigate}
  />;
}
