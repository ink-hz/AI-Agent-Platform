import { isValidElement, useState, type MouseEvent, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { remarkEvidenceAnchors } from './remarkEvidenceAnchors';
import { platformPath } from '../auth';
import { navigate } from '../router';
import { isDocumentId, type DocumentDetail } from './sdkAdminApi';
import { conditionDescription, conditionLabel, documentDescription, kindNames, statusLabel } from './sdkDocumentPresentation';
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
    const [showDetails, setShowDetails] = useState(false);
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
  <div className="sdk-reader-heading"><h1>{doc.title}</h1><p>{documentDescription(doc)}</p></div>
  <details className="sdk-provenance" open={showDetails} onToggle={event => setShowDetails(event.currentTarget.open)}><summary>文档详情与适用条件</summary>{showDetails && <>
    <dl><dt>资料类型</dt><dd>{kindNames[doc.kind]}</dd>{doc.level && <><dt>阅读层级</dt><dd>{doc.level}</dd></>}</dl>
    <dl className="sdk-evidence"><div><dt>内容状态</dt><dd>{statusLabel(doc.content_state)}</dd></div><div><dt>静态审阅</dt><dd>{statusLabel(doc.review_status)}</dd></div><div><dt>运行验证</dt><dd>{statusLabel(doc.runtime_validation)}</dd></div></dl>
    <dl className="sdk-conditions">{Object.keys(doc.conditions).length ? Object.entries(doc.conditions).map(([key, value]) => <div key={key}><dt>{conditionLabel(key)}</dt><dd>{conditionDescription(value)}</dd></div>) : <><dt>适用条件</dt><dd>来源未单独声明，请结合正文确认设备、固件、平台、配置与状态。</dd></>}</dl>
    <dl><dt>源码基线</dt><dd>{doc.baseline_id ?? '来源未声明'}</dd><dt>原始来源</dt><dd><code>{doc.source_path}</code></dd><dt>原件 SHA-256</dt><dd><code>{doc.source_sha256}</code></dd><dt>阅读正文 SHA-256</dt><dd><code>{doc.sha256}</code></dd></dl>
  </>}</details>
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
