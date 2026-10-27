import { useEffect, useRef, useState } from 'react';
import { platformPath, type Account } from '../auth';
import { manufacturingAccess, ManufacturingAccessError } from '../manufacturingApi';
import { registerPanoramaLeaveGuard } from '../panoramaNavigation';

type MountOptions = {apiBase: string; csrfToken: string; readOnly: boolean; onAuthError?: () => void; onDirtyChange?: (dirty: boolean) => void};
type MountedWorkbench = {unmount(): void; isDirty(): boolean};
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

type Status = 'loading' | 'ready' | 'denied' | 'unavailable' | 'error';
export function ManufacturingPage({account}: {account: Account}) {
  const element = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<Status>('loading');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true, dirty = false;
    let mounted: MountedWorkbench | undefined;
    let stylesheet: HTMLLinkElement | undefined;
    let removeGuard: (() => void) | undefined;
    const controller = new AbortController();
    const retire = () => {
      if (!active) return;
      active = false; controller.abort(); removeGuard?.(); removeGuard = undefined;
      if (stylesheet && activeStylesheet === stylesheet) {
        stylesheet.media = 'not all'; activeStylesheet = undefined;
      }
      const handle = mounted; mounted = undefined; handle?.unmount();
    };
    setStatus('loading');
    void (async () => {
      try {
        const access = await manufacturingAccess(controller.signal);
        if (!active) return;
        if (!access.allowed) { setStatus('denied'); return; }
        if (!access.available) { setStatus('unavailable'); return; }
        const apiBase = platformPath('/api/v1/manufacturing');
        const version = encodeURIComponent(access.release ?? 'development');
        const [script, style] = await Promise.all([
          loadAsset(`${apiBase}/assets/manufacturing.js?release=${version}`, true),
          loadAsset(`${apiBase}/assets/manufacturing.css?release=${version}`, false),
        ]);
        if (!active || !element.current) return;
        if (!script.library || !style.stylesheet) throw new Error('Manufacturing mount unavailable');
        stylesheet = style.stylesheet;
        if (activeStylesheet) activeStylesheet.media = 'not all';
        activeStylesheet = stylesheet; stylesheet.media = 'all';
        removeGuard = registerPanoramaLeaveGuard(() => !active || !(dirty || mounted?.isDirty()) || window.confirm('有未保存的修改，确认离开？'));
        const handle = script.library.mount(element.current, {
          apiBase, csrfToken: account.csrf_token,
          readOnly: account.hard_stale_read_only || access.can_edit !== true,
          onDirtyChange(value) { if (active) dirty = value; },
          onAuthError() { if (active) { retire(); setStatus('denied'); } },
        });
        // A synchronous auth callback may retire the owner during mount.
        if (!active) { handle.unmount(); return; }
        mounted = handle; setStatus('ready');
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
  return <section aria-label="大制造工作台">
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
