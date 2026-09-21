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
 });
});
