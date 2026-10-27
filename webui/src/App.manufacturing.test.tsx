/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from './App';
import { PermissionDenied, type Account } from './auth';
const loadAccount = vi.hoisted(() => vi.fn());
vi.mock('./auth', async original => ({...await original<typeof import('./auth')>(), identityShellEnabled: () => true, loadAccount}));
vi.mock('./pages/ManufacturingPage', () => ({ManufacturingPage: ({account}: {account: Account}) => <h1>制造页面 {account.display_name}</h1>}));
vi.mock('./accessEventReporter', () => ({AccessEventReporter: () => null}));
vi.mock('./api', async original => ({...await original<typeof import('./api')>(), fetchDeployment: async () => ({mode:'local',read_only:false,freshness:'current'})}));
const account: Account = {internal_user_id:'manager',display_name:'测试制造经理',role:'platform_admin',departments:[],gender:null,observation_agent_ids:[],workspace_scopes:[],directory_freshness:'fresh',hard_stale_read_only:false,csrf_token:'csrf'};
let box: HTMLDivElement, root: ReturnType<typeof createRoot>;
beforeEach(() => {
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
 window.history.replaceState({}, '', '/manufacturing');
 box = document.createElement('div');document.body.append(box);root = createRoot(box);
 loadAccount.mockReset().mockResolvedValue(account);
});
afterEach(async () => {await act(async () => root.unmount());box.remove();vi.restoreAllMocks();});
it('renders the authorized manufacturing route outside all Agent chrome', async () => {
 await act(async () => root.render(<App/>));
 expect(box.textContent).toContain('制造页面 测试制造经理');
 expect(box.querySelector('.platform-shell')).toBeNull();expect(box.querySelector('.topbar')).toBeNull();
 expect(box.querySelector('nav')).toBeNull();
});
it('keeps denied manufacturing routes outside the shell and hides account data', async () => {
 loadAccount.mockResolvedValue({...account,role:'member'});
 await act(async () => root.render(<App/>));
 expect(box.textContent).toContain('无权访问');expect(box.textContent).not.toContain('测试制造经理');
 expect(box.querySelector('.platform-shell')).toBeNull();
});
it('keeps account failures outside manufacturing chrome', async () => {
 loadAccount.mockRejectedValue(new PermissionDenied());
 await act(async () => root.render(<App/>));
 expect(box.textContent).toContain('无权访问');expect(box.querySelector('.platform-shell')).toBeNull();
});
