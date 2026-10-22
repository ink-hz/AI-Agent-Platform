/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { navigate, parseRoute } from './router';
import { actionPath, isPanoramaLocation, workspaceGroup } from './panoramaNavigation';
describe('panorama navigation boundary', () => {
 beforeEach(() => { window.history.replaceState({}, '', '/'); vi.stubGlobal('requestAnimationFrame', vi.fn()); });
 it('keeps provenance through real native detail routes and query filters', () => {
   navigate('/admin/sessions', { state: { panorama: true } });
   navigate('/admin/sessions/s1?q=detail', { state: { origin: 'sessions' } });
   expect(window.history.state).toMatchObject({ panorama: true, origin: 'sessions' });
   expect(isPanoramaLocation(parseRoute(window.location.pathname, window.location.search))).toBe(true);
 });
 it('does not add panorama provenance to independent native visits or login', () => {
   navigate('/brain'); expect(isPanoramaLocation(parseRoute('/brain'))).toBe(false);
   navigate('/admin', { state: { panorama: true } });
   navigate('/login'); expect(window.history.state?.panorama).not.toBe(true);
 });
 it('treats both company canvases as one panorama session boundary', () => {
   expect(isPanoramaLocation(parseRoute('/organization'))).toBe(true);
   navigate('/organization', { state: { panorama: true } });
   expect(window.history.state).toMatchObject({ panorama: true });
   navigate('/');
   expect(window.history.state).toMatchObject({ panorama: true });
 });
 it('groups conversation with brain and excludes independent applications', () => {
   expect(workspaceGroup({ name: 'conversation', conversationId: 'c1' })).toBe('brain');
   expect(workspaceGroup({ name: 'admin-session', sessionKey: 's1' })).toBe('sessions');
   expect(workspaceGroup({ name: 'legacy-redirect', to: '/office/', navigation: 'document' })).toBeNull();
   expect(actionPath('office')).toEqual({ path: '/office/', external: true });
   expect(actionPath('notes')).toEqual({ path: '/ai-notes', external: true, newTab: true });
   expect(workspaceGroup({ name: 'ai-notes' })).toBeNull();
   navigate('/ai-notes', { state: { panorama: true } });
   expect(isPanoramaLocation(parseRoute('/ai-notes'))).toBe(false);
   expect(window.history.state?.panorama).not.toBe(true);
 });
});

it('retains SDK workspace provenance through sections, article links and return navigation',()=>{
 window.history.replaceState({panorama:true},'', '/');
 for(const path of ['/sdk-admin','/sdk-admin/products','/sdk-admin/delivery','/sdk-admin/diagnostics','/sdk-admin/knowledge','/sdk-admin/knowledge/KB-API-001#section','/sdk-admin/knowledge']) {
  navigate(path);const route=parseRoute(window.location.pathname,window.location.search);
  expect(workspaceGroup(route)).toBe('sdk-admin');
  expect(window.history.state?.panorama).toBe(true);expect(isPanoramaLocation(route)).toBe(true);
 }
 navigate('/sdk/');expect(window.history.state?.panorama).not.toBe(true);
 expect(workspaceGroup(parseRoute('/sdk/'))).toBeNull();
 expect(actionPath('sdk-admin')).toEqual({path:'/sdk-admin',external:false});
});
it('keeps direct SDK article visits independent of panorama provenance',()=>{
 window.history.replaceState({},'', '/sdk-admin/knowledge/KB-API-001');
 expect(isPanoramaLocation(parseRoute(window.location.pathname))).toBe(false);
 navigate('/sdk-admin/knowledge');expect(window.history.state?.panorama).not.toBe(true);
});
