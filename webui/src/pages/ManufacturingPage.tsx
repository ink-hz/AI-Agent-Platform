import { useEffect, useRef, useState } from 'react';
import { AuthenticationRequired, PermissionDenied, listManagedUsers, logoutAccount, platformPath, type Account } from '../auth';
import { manufacturingAccess, ManufacturingAccessError, type ManufacturingAccess } from '../manufacturingApi';
import { createManufacturingMarkdownRenderer, type ManufacturingMarkdownRenderer } from '../manufacturingMarkdown';
import { navigate } from '../router';
import './manufacturingHost.css';
import { registerPanoramaLeaveGuard } from '../panoramaNavigation';

type WorkbenchUser = {displayName: string; roleLabel: string};
type MountOptions = {findSubjects?: (query:string,signal:AbortSignal)=>Promise<{id:string;displayName:string}[]>; bootstrap?: ManufacturingAccess; user?: WorkbenchUser; onNavigatePlatform?: () => void; onLogout?: () => Promise<void>; apiBase: string; csrfToken: string; readOnly: boolean; renderMarkdown?: ManufacturingMarkdownRenderer; onAuthError?: () => void; onDirtyChange?: (dirty: boolean) => void};
type MountedWorkbench = {unmount(): void; isDirty(): boolean; updateUser?(user: WorkbenchUser): void};
declare global {
  interface Window {OrbbecManufacturing?: {mount(element: HTMLElement, options: MountOptions): MountedWorkbench}}
}
type WorkbenchLibrary = NonNullable<Window['OrbbecManufacturing']>;
type LoadedAsset = {library?: WorkbenchLibrary; stylesheet?: HTMLLinkElement};
// Cache the captured implementation, not a mutable window-global lookup. This
// also makes v1 -> v2 -> v1 rollback reuse the correct code for its API release.
const assets = new Map<string, Promise<LoadedAsset>>();
let activeStylesheet: HTMLLinkElement | undefined;
function loadAsset(url: string, script: boolean): Promise<LoadedAsset> {
  const existing = assets.get(url);
  if (existing) return existing;
  const promise = new Promise<LoadedAsset>((resolve, reject) => {
    const node = script ? document.createElement('script') : document.createElement('link');
    node.dataset.manufacturingAsset = url;
    if (node instanceof HTMLScriptElement) { node.src = url; node.async = true; }
    else { node.rel = 'stylesheet'; node.href = url; node.media = 'not all'; }
    const fail = () => {
      node.onload = null; node.onerror = null; node.remove(); assets.delete(url);
      reject(new Error('Manufacturing asset unavailable'));
    };
    node.onload = () => {
      node.onload = null; node.onerror = null;
      if (node instanceof HTMLLinkElement) resolve({stylesheet: node});
      else if (typeof window.OrbbecManufacturing?.mount === 'function') resolve({library: window.OrbbecManufacturing});
      else fail();
    };
    node.onerror = fail;
    document.head.append(node);
  });
  assets.set(url, promise);
  return promise;
}

const roleLabels: Record<Account['role'], string> = {
  member: '企业成员', management_viewer: '只读观察者', platform_admin: '平台管理员', platform_owner: '平台所有者',
};
function workbenchUser(account: Account): WorkbenchUser {
  return {displayName: account.display_name, roleLabel: roleLabels[account.role]};
}
type Status = 'loading' | 'ready' | 'denied' | 'unavailable' | 'error';
export function ManufacturingPage({account}: {account: Account}) {
  const element = useRef<HTMLDivElement>(null);
  const currentAccount = useRef(account); currentAccount.current = account;
  const currentHandle = useRef<MountedWorkbench | undefined>(undefined);
  const [status, setStatus] = useState<Status>('loading');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true, dirty = false, logoutPending = false;
    let mounted: MountedWorkbench | undefined;
    let stylesheet: HTMLLinkElement | undefined;
    let removeGuard: (() => void) | undefined;
    const controller = new AbortController();
    const directoryRequests = new Set<AbortController>();
    const markdown = createManufacturingMarkdownRenderer();
    const retire = () => {
      if (!active) return;
      active = false; controller.abort(); directoryRequests.forEach(c=>c.abort()); directoryRequests.clear(); removeGuard?.(); removeGuard = undefined;
      if (stylesheet && activeStylesheet === stylesheet) {
        stylesheet.media = 'not all'; activeStylesheet = undefined;
      }
      markdown.unmount();
      const handle = mounted; mounted = undefined; currentHandle.current = undefined; handle?.unmount();
    };
    const deny = () => { if (active) { retire(); setStatus('denied'); } };
    setStatus('loading');
    void (async () => {
      try {
        const access = await manufacturingAccess(controller.signal);
        if (!active) return;
        if (!access.allowed) { setStatus('denied'); return; }
        if (!access.available) { setStatus('unavailable'); return; }
        const apiBase = platformPath('/api/v1/manufacturing');
        const version = encodeURIComponent(access.release ?? 'development');
        const assetsBase = access.workflow_available ? `${apiBase}/workflow` : apiBase;
        const [script, style] = await Promise.all([
          loadAsset(`${assetsBase}/assets/manufacturing.js?release=${version}`, true),
          loadAsset(`${assetsBase}/assets/manufacturing.css?release=${version}`, false),
        ]);
        if (!active || !element.current) return;
        if (!script.library || !style.stylesheet) throw new Error('Manufacturing mount unavailable');
        stylesheet = style.stylesheet;
        if (activeStylesheet) activeStylesheet.media = 'not all';
        activeStylesheet = stylesheet; stylesheet.media = 'all';
        removeGuard = registerPanoramaLeaveGuard(() => !active || !(dirty || mounted?.isDirty()) || window.confirm('有未保存的修改，确认离开？'));
        const handle = script.library.mount(element.current, {
          apiBase, bootstrap: access.protocol === 1 ? access : undefined, csrfToken: account.csrf_token, renderMarkdown: markdown.renderMarkdown,
          findSubjects: access.protocol === 1 && access.configuration_admin ? async (query, signal) => {
            if (!active || signal.aborted) throw new DOMException('Manufacturing directory retired', 'AbortError');
            const request = new AbortController(); directoryRequests.add(request);
            const abort = () => request.abort(); signal.addEventListener('abort', abort, {once:true});
            try {
              const users = await listManagedUsers(request.signal);
              if (!active || signal.aborted || request.signal.aborted) throw new DOMException('Manufacturing directory retired', 'AbortError');
              return users.filter(user => user.status === 'active' && user.display_name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
                .map(user => ({id:user.internal_user_id, displayName:user.display_name}));
            } catch (error) {
              if (active && !signal.aborted && (error instanceof AuthenticationRequired || error instanceof PermissionDenied)) deny();
              throw error;
            } finally { signal.removeEventListener('abort', abort); directoryRequests.delete(request); }
          } : undefined,
          user: workbenchUser(currentAccount.current),
          onNavigatePlatform() { if (active && !logoutPending) navigate('/'); },
          async onLogout() {
            if (!active || logoutPending) return;
            logoutPending = true;
            try {
              await logoutAccount(account.csrf_token);
              if (active) window.location.replace(platformPath('/login'));
            } finally { logoutPending = false; }
          },
          readOnly: account.hard_stale_read_only || access.can_edit !== true,
          onDirtyChange(value) { if (active) dirty = value; },
          onAuthError: deny,
        });
        // A synchronous auth callback may retire the owner during mount.
        if (!active) { handle.unmount(); return; }
        mounted = handle; currentHandle.current = handle; setStatus('ready');
      } catch (error) {
        if (!active) return;
        retire();
        setStatus(error instanceof ManufacturingAccessError
          ? [401, 403].includes(error.status) ? 'denied' : 'unavailable'
          : 'error');
      }
    })();
    return retire;
  }, [account.internal_user_id, account.role, account.csrf_token, account.hard_stale_read_only, attempt]);
  useEffect(() => {
    currentHandle.current?.updateUser?.(workbenchUser(account));
  }, [account.display_name, account.role]);
  return <section className="manufacturing-standalone" aria-label="大制造工作台">
    {account.hard_stale_read_only && <aside className="manufacturing-readonly-banner" role="status"><strong>通讯录已超过安全时限</strong><span>当前仅保留已授权管理账号的只读访问，变更功能已暂停。</span></aside>}
    {status !== 'ready' && <h1>大制造</h1>}
    {status === 'loading' && <p role="status">正在加载制造工作台…</p>}
    {status === 'denied' && <p role="alert">无权访问大制造工作台，请确认登录账号和权限。</p>}
    {(status === 'unavailable' || status === 'error') && <div role="alert">
      <p>{status === 'unavailable' ? '大制造工作台暂不可用。' : '大制造工作台加载失败。'}</p>
      <button type="button" onClick={() => setAttempt(value => value + 1)}>重新尝试</button>
    </div>}
    <div ref={element} data-manufacturing-root hidden={status !== 'ready'}/>
  </section>;
}
