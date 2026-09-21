import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
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
import { navigate, currentLocationPath, type Route } from "../router";
import { PanoramaView } from "../panorama/PanoramaView";
import { OrganizationLayout } from "../organization/OrganizationLayout";
import { fetchPanorama } from "../panorama/panoramaApi";
import type { PanoramaData, PanoramaActionId } from "../panoramaTypes";
import { actionPath, workspaceGroup, registerPanoramaLeaveGuard } from "../panoramaNavigation";
import { PanoramaWorkArea } from "../PanoramaWorkArea";
import "../panoramaWorkspace.css";


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
  view?: "business" | "organization";
  client?: AiEngineeringClient;
  direct?: boolean;
  fallback: ReactNode;
  selectedDocument?: AiEngineeringDocumentSlug;
  onNavigate?: (path: string) => void;
  onAccessDenied?: () => void;
  workspaceRoute?: Route;
  renderWorkspace?: (route: Route) => ReactNode;
}

interface SessionProps extends LandingProps {
  client: AiEngineeringClient;
  onNavigate: (path: string) => void;
}

function AiEngineeringSession({ account, client, direct = false, fallback, selectedDocument, onNavigate, onAccessDenied }: SessionProps) {
  const [access, setAccess] = useState<"checking" | "allowed" | "fallback" | "denied">("checking");
  useEffect(() => { if (access === "denied") onAccessDenied?.(); }, [access, onAccessDenied]);
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
      ? `AI 助手 · ${PLATFORM_TITLE}`
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
  if (access === "denied") return pageState("无权限", "请联系苍渊。", "alert");
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
        <a href={platformPath("/brain")} onClick={(event) => follow(event, "/brain")}>AI 助手</a>
        <a href={platformPath("/agents")} onClick={(event) => follow(event, "/agents")}>Agent 目录</a>
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

function documentActiveElement(): HTMLElement | null { return window.document.activeElement instanceof HTMLElement ? window.document.activeElement : null; }

function RetainedScrollView({ active, view, children }: { active: boolean; view: "business" | "organization"; children: ReactNode }) {
  const viewport = useRef<HTMLDivElement>(null);
  const savedTop = useRef(0);
  useLayoutEffect(() => {
    const element = viewport.current;
    if (!active || !element) return;
    element.scrollTop = savedTop.current;
    const frame = window.requestAnimationFrame(() => { element.scrollTop = savedTop.current; });
    return () => window.cancelAnimationFrame(frame);
  }, [active]);
  return <div className="panorama-view-scroll" data-panorama-view={view} hidden={!active} onScroll={event => { if (active) savedTop.current = event.currentTarget.scrollTop; }} ref={viewport}>{children}</div>;
}

function PanoramaSession({ account, client, direct = false, fallback, onNavigate, onAccessDenied, view = "business", workspaceRoute, renderWorkspace }: SessionProps) {
  const [access, setAccess] = useState<"checking" | "allowed" | "denied">("checking");
  useEffect(() => { if (access === "denied") onAccessDenied?.(); }, [access, onAccessDenied]);
  const [data, setData] = useState<PanoramaData | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [evidence, setEvidence] = useState<AiEngineeringDocumentSlug | null>(null);
  const [document, setDocument] = useState<AiEngineeringDocument | null>(null);
  const [documentError, setDocumentError] = useState(false);
  const lastWorkspace = useRef<{group: PanoramaActionId; path: string} | null>(null);
  const dirty = useRef(false);
  const layoutDirty = useRef(false);
  const onLayoutDirty = useCallback((value: boolean) => { layoutDirty.current = value; }, []);
  const workspaceHeading = useRef<HTMLDivElement>(null);
  const graph = useRef<HTMLDivElement>(null);
  const evidencePanel = useRef<HTMLElement>(null);
  useDocumentTitle(view === "organization" ? `组织架构 · ${PLATFORM_TITLE}` : data ? `${data.title} · ${PLATFORM_TITLE}` : PLATFORM_TITLE);
  const deny = useCallback(() => { setData(null); setDocument(null); setEvidence(null); setAccess("denied"); dirty.current = false; layoutDirty.current = false; }, []);
  const authorizationFailure = useCallback((failure: unknown) => {
    deny();
    if (failure instanceof AiEngineeringApiError && failure.status === 401) {
      onNavigate(`/login?return_path=${encodeURIComponent(currentLocationPath())}`);
    }
  }, [deny, onNavigate]);
  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => { controller.abort(); deny(); }, ACCESS_PROBE_TIMEOUT_MS);
    void client.fetchAccess(controller.signal).then(({allowed}) => {
      if (controller.signal.aborted) return;
      window.clearTimeout(timeout);
      if (allowed) setAccess("allowed"); else deny();
    }).catch(failure => { if (!controller.signal.aborted) { window.clearTimeout(timeout); authorizationFailure(failure); } });
    return () => { window.clearTimeout(timeout); controller.abort(); };
  }, [client, deny, authorizationFailure]);
  useEffect(() => {
    if (access !== "allowed" || view !== "business" || data) return;
    const controller = new AbortController();
    setError(false);
    void fetchPanorama(controller.signal).then(value => { if (!controller.signal.aborted) setData(value); }).catch(failure => {
      if (controller.signal.aborted) return;
      if (isAuthorizationFailure(failure)) authorizationFailure(failure); else setError(true);
    });
    return () => controller.abort();
  }, [access, attempt, data, view, authorizationFailure]);
  useEffect(() => {
    if (access !== "allowed") return;
    let pending: {controller: AbortController; timeout: number} | null = null;
    const check = () => {
      if (pending) { pending.controller.abort(); window.clearTimeout(pending.timeout); }
      const controller = new AbortController();
      const timeout = window.setTimeout(() => { controller.abort(); deny(); }, ACCESS_PROBE_TIMEOUT_MS);
      pending = {controller, timeout};
      void client.fetchAccess(controller.signal).then(result => {
        if (!controller.signal.aborted && !result.allowed) deny();
      }).catch(failure => { if (!controller.signal.aborted) authorizationFailure(failure); }).finally(() => window.clearTimeout(timeout));
    };
    const timer = window.setInterval(check, 60_000);
    window.addEventListener("focus", check);
    return () => { if (pending) { pending.controller.abort(); window.clearTimeout(pending.timeout); } window.clearInterval(timer); window.removeEventListener("focus", check); };
  }, [access, client, deny, authorizationFailure]);
  useEffect(() => {
    if (access !== "allowed" || !evidence) return;
    const controller = new AbortController(); setDocument(null); setDocumentError(false);
    void client.fetchDocument(evidence, controller.signal).then(value => { if (!controller.signal.aborted) setDocument(value); }).catch(failure => {
      if (controller.signal.aborted) return;
      if (isAuthorizationFailure(failure)) authorizationFailure(failure); else setDocumentError(true);
    });
    return () => controller.abort();
  }, [access, client, evidence, deny, authorizationFailure]);
  useEffect(() => {
    if (!workspaceRoute) return;
    const group = workspaceGroup(workspaceRoute);
    if (group) lastWorkspace.current = {group, path: currentLocationPath()};
    workspaceHeading.current?.focus();
  }, [workspaceRoute]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirty.current || layoutDirty.current) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, []);
  useEffect(() => {
    if (!evidence) return;
    const previous = documentActiveElement();
    evidencePanel.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => previous?.focus();
  }, [evidence]);
  useEffect(() => registerPanoramaLeaveGuard(path => {
    // Returning to the graph keeps both workspaces mounted.
    if (layoutDirty.current && path !== "/" && path !== "/organization" && path !== "/ai-engineering"
      && !window.confirm("全景布局有未保存修改。确定离开？")) return false;
    if (!dirty.current || path === "/" || path === "/organization" || path === "/ai-engineering" || path === lastWorkspace.current?.path) return true;
    if (!window.confirm("当前工作区有未保存输入或未确认的提交结果。确定离开？取消可保留原输入和重试请求。")) return false;
    dirty.current = false;
    return true;
  }), []);
  const closeWorkspace = useCallback(() => { onNavigate("/"); window.requestAnimationFrame(() => graph.current?.focus()); }, [onNavigate]);
  const openAction = (action: PanoramaActionId) => {
    if (action === "access" && account.role !== "platform_owner") return;
    const target = actionPath(action);
    const previous = lastWorkspace.current;
    if (dirty.current && (target.external || (previous && previous.group !== action))) {
      if (!window.confirm("当前工作区可能有未保存的输入。确定放弃并切换？选择取消可保留当前工作。")) return;
      dirty.current = false;
    }
    if (target.external) {
      if (layoutDirty.current && !window.confirm("全景布局有未保存修改。确定离开？")) return;
      window.location.assign(platformPath(target.path)); return;
    }
    onNavigate(previous?.group === action ? previous.path : target.path);
  };
  if (access === "denied") return direct ? pageState("无权限", "请联系苍渊。", "alert") : <>{fallback}</>;
  if (access === "checking") return pageState("正在确认访问权限", "正在确认企业账号。");
  const businessVisible = !workspaceRoute && view === "business";
  const organizationVisible = !workspaceRoute && view === "organization";
  const businessActive = businessVisible && !evidence;
  const organizationActive = organizationVisible && !evidence;
  return <article className="panorama-home">
    <div className="panorama-view-stack" ref={graph} tabIndex={-1} hidden={!!workspaceRoute} inert={!!evidence}>
      <RetainedScrollView active={businessVisible} view="business">
        {data ? <PanoramaView data={data} active={businessActive} isOwner={account.role === "platform_owner"} onAction={openAction} onEvidence={setEvidence} onDataChange={setData} onAuthorizationFailure={authorizationFailure} onDirtyChange={onLayoutDirty} /> : error ? <section className="panorama" role="alert"><h1>AI 工程全景暂时不可用</h1><button onClick={() => setAttempt(value => value + 1)}>重试全景</button></section> : pageState("正在打开 AI 工程全景", "正在读取受保护内容。")}
      </RetainedScrollView>
      <RetainedScrollView active={organizationVisible} view="organization">
        <OrganizationLayout active={organizationActive} onAuthorizationFailure={authorizationFailure} />
      </RetainedScrollView>
    </div>
    {renderWorkspace && <div className="panorama-workspace-scroll" hidden={!workspaceRoute} ref={workspaceHeading} tabIndex={-1} inert={!!evidence}><PanoramaWorkArea route={workspaceRoute} renderWorkspace={renderWorkspace} onClose={closeWorkspace} onDirty={value => { dirty.current = value; }} /></div>}
    {evidence && <section ref={evidencePanel} className="panorama-evidence" role="dialog" aria-modal="true" aria-label="事实依据" onKeyDown={event => {
      if (event.key === "Escape") { event.stopPropagation(); setEvidence(null); }
      if (event.key === "Tab") {
        const controls = [...event.currentTarget.querySelectorAll<HTMLElement>('button, a[href], input, [tabindex="0"]')];
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && documentActiveElement() === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && documentActiveElement() === last) { event.preventDefault(); first?.focus(); }
      }
    }}>
      <header><h2>{document?.title ?? "事实依据"}</h2><button type="button" autoFocus onClick={() => setEvidence(null)}>关闭依据</button></header>
      {documentError ? <p role="alert">此项依据暂时不可用，其他区域仍可使用。</p> : document ? <div onClickCapture={event => {
        const anchor = (event.target as Element).closest("a");
        if (!anchor || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        const url = new URL(anchor.href, window.location.origin);
        if (url.origin === window.location.origin && url.pathname === platformPath("/ai-engineering")) {
          const slug = url.searchParams.get("document");
          if (slug && ["overview", "reading", "domains", "finance", "products", "assets"].includes(slug)) { event.preventDefault(); setEvidence(slug as AiEngineeringDocumentSlug); }
        }
      }}><AiEngineeringMarkdown markdown={document.markdown} onAssetError={() => setDocumentError(true)} /></div> : <p role="status">正在读取依据…</p>}
    </section>}
  </article>;
}

const navigateFromPanorama = (path: string) => navigate(path, {state: {panorama: true}});

export function AiEngineeringLanding({
  account,
  client = aiEngineeringClient,
  onNavigate = navigateFromPanorama,
  ...props
}: LandingProps) {
  if (account.role !== "platform_admin" && account.role !== "platform_owner") {
    return pageState("无权限", "请联系苍渊。", "alert");
  }
  const Session = props.selectedDocument ? AiEngineeringSession : PanoramaSession;
  return <Session {...props} account={account} client={client}
    key={`${account.internal_user_id}:${account.role}`} onNavigate={onNavigate} />;
}
