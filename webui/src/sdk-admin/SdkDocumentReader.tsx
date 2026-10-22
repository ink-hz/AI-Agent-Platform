import { isValidElement, type MouseEvent, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { remarkEvidenceAnchors } from './remarkEvidenceAnchors';
import { platformPath } from '../auth';
import { navigate } from '../router';
import { isDocumentId, type DocumentDetail } from './sdkAdminApi';
export function portalClick(event: MouseEvent<HTMLAnchorElement>, path: string) {
    const target = new URL(platformPath(path), window.location.origin);
    // A fragment inside the currently open article uses native anchor scrolling.
    if (target.pathname === window.location.pathname && target.search === window.location.search && target.hash) return;
    if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
    event.preventDefault();
    navigate(path);
} }
export function DocumentLink({ id, children }: {
    id: string;
    children: React.ReactNode;
}) { const path = `/sdk-admin/knowledge/${id}`; return isDocumentId(id) ? <a href={platformPath(path)} onClick={e => portalClick(e, path)}>{children}</a> : <span>{children}</span>; }
export function safeExternal(href: string) { try {
    const url = new URL(href);
    return url.protocol === 'https:' && !url.username && !url.password;
}
catch {
    return false;
} }
export function statusLabel(code: string) {
    const labels = new Map<string, string>(Object.entries({ accepted_static: '静态接收', navigation: '阅读导航', phase_report: '阶段报告', design_proposal: '设计草案', execution_record: '执行记录', independently_reviewed: '已独立复核', acceptance_report: '接收报告', not_applicable: '不适用', source_record: '来源记录', not_run: '未执行运行验证', instance_not_verified: '实例待核验', limited_release_record: '有限发布记录', not_registered: '待登记' }));
    return labels.get(code) ?? code;
}
function headingText(value: ReactNode): string { if (typeof value === 'string' || typeof value === 'number')
    return String(value); if (Array.isArray(value))
    return value.map(headingText).join(''); if (isValidElement<{
    children?: ReactNode;
}>(value))
    return headingText(value.props.children); return ''; }
function markdownHref(value: string) { try {
    return encodeURI(value).replace(/%25([a-f0-9]{2})/gi, '%$1');
}
catch {
    return value;
} }
export function SdkDocumentReader({ document: doc }: {
    document: DocumentDetail;
}) {
    const normalizedLinks = new Map(Object.entries(doc.links).map(([href, link]) => [markdownHref(href), link]));
    const headings = new Set<string>();
    function headingId(value: ReactNode) {
        const base = headingText(value).toLowerCase().replace(/[^\p{L}\p{N}_\s-]/gu, '').trim().replace(/\s+/g, '-');
        let id = base, suffix = 0;
        while (headings.has(id)) id = `${base}-${++suffix}`;
        headings.add(id);
        return id;
    }
    return <article className="sdk-reader" aria-label={doc.title}>
  <div className="sdk-reader-heading"><span className="sdk-eyebrow">{doc.id} · {doc.kind}</span><h1>{doc.title}</h1><p>基于锁定来源阅读；静态结论与设备运行验证分别记录。</p></div>
  <dl className="sdk-evidence"><div><dt>源码基线</dt><dd>{doc.baseline_id ?? '来源未声明'}</dd></div><div><dt>阅读层级</dt><dd>{doc.level ?? '未分级'} · {doc.group}</dd></div><div><dt>内容状态</dt><dd>{statusLabel(doc.content_state)}</dd></div><div><dt>静态审阅</dt><dd>{statusLabel(doc.review_status)}</dd></div><div><dt>运行验证</dt><dd>{statusLabel(doc.runtime_validation)}</dd></div></dl>
  <details className="sdk-provenance"><summary>适用条件与来源摘要</summary><dl><dt>原始状态 code（内容 / 审阅 / 运行）</dt><dd><code>{doc.content_state} / {doc.review_status} / {doc.runtime_validation}</code></dd><dt>适用条件</dt><dd>{Object.keys(doc.conditions).length ? <pre>{JSON.stringify(doc.conditions, null, 2)}</pre> : '来源未声明，请结合正文确认设备、固件、平台、配置与状态。'}</dd><dt>原始来源</dt><dd><code>{doc.source_path}</code></dd><dt>原件 SHA-256</dt><dd><code>{doc.source_sha256}</code></dd><dt>阅读正文 SHA-256</dt><dd><code>{doc.sha256}</code></dd></dl></details>
  <div className="sdk-markdown"><ReactMarkdown remarkPlugins={[remarkGfm, remarkEvidenceAnchors]} skipHtml urlTransform={value => value} components={{
            a: ({ href = '', children }) => {
                const link = doc.links[href] ?? normalizedLinks.get(markdownHref(href));
                if (link && isDocumentId(link.document_id)) {
                    const path = `/sdk-admin/knowledge/${link.document_id}${link.fragment ? `#${encodeURIComponent(link.fragment.replace(/^#/, ''))}` : ''}`;
                    return <a href={platformPath(path)} onClick={e => portalClick(e, path)}>{children}</a>;
                }
                if (href.startsWith('#'))
                    return <a href={href}>{children}</a>;
                if (safeExternal(href))
                    return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
                return <span className="sdk-source-ref">{children} <code>{href}</code><span className="sdk-visually-hidden">（源引用，未收录）</span></span>;
            },
            img: ({ src, alt }) => <span className="sdk-source-ref">{alt ?? '图片引用'} <code>{src}</code></span>,
            table: ({ children }) => <div className="sdk-table-scroll"><table>{children}</table></div>,
            h1: ({ children }) => <h2 id={headingId(children)}>{children}</h2>, h2: ({ children }) => <h2 id={headingId(children)}>{children}</h2>, h3: ({ children }) => <h3 id={headingId(children)}>{children}</h3>, h4: ({ children }) => <h4 id={headingId(children)}>{children}</h4>, h5: ({ children }) => <h5 id={headingId(children)}>{children}</h5>, h6: ({ children }) => <h6 id={headingId(children)}>{children}</h6>
        }}>{doc.markdown}</ReactMarkdown></div>
 </article>;
}
