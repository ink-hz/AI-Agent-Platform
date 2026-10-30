/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import fixture from '../../../backend/tests/fixtures/runtime_dashboard_v1.json';
import type { DashboardOverview } from '../runtimeDashboardTypes';
import { OverviewPage } from './OverviewPage';
let box:HTMLDivElement;let root:ReturnType<typeof createRoot>;let data:DashboardOverview;
beforeEach(()=>{(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;box=document.createElement('div');document.body.append(box);root=createRoot(box);data=structuredClone(fixture) as unknown as DashboardOverview;vi.stubGlobal('fetch',vi.fn().mockImplementation(()=>Promise.resolve(new Response(JSON.stringify(data)))));});
afterEach(async()=>{await act(async()=>root.unmount());box.remove();vi.restoreAllMocks();vi.unstubAllGlobals();});
it('renders seven current applications, coverage and separate channel/history inventories from the new HTTP contract',async()=>{
 await act(async()=>root.render(<OverviewPage/>));
 expect(box.querySelector('h1')?.textContent).toBe('运行中心');expect(box.textContent).toContain('数据覆盖');
 expect(box.querySelectorAll('[data-application-id]')).toHaveLength(7);expect(box.querySelectorAll('[data-application-kind="agent"]')).toHaveLength(5);expect(box.querySelectorAll('[data-application-kind="workspace"]')).toHaveLength(2);
 expect(box.textContent).toContain('MetaBot / 飞书 Agent');expect(box.querySelector('.runtime-history')?.textContent).toContain('历史来源');expect(box.textContent).not.toContain('当前关键运行检查均正常。');
 expect(fetch).toHaveBeenCalledTimes(1);expect(fetch).toHaveBeenCalledWith('/api/dashboard/overview?days=7',expect.objectContaining({credentials:'include',cache:'no-store'}));expect(box.querySelector('a[href="/hr/"]')).not.toBeNull();expect(box.querySelector('a[href="/admin/agents/hr-bot"]')).toBeNull();
});
it('preserves nullable data, input proxy, unknown health and distinct VOC business evidence',async()=>{
 data.applications[1].usage.window.deliveries={...data.applications[1].usage.window.deliveries,value:null as any,status:'unavailable',reason:'delivery_evidence_unavailable'};
 await act(async()=>root.render(<OverviewPage/>));const voc=box.querySelector('[data-application-id="voc"]')!;expect(voc.textContent).toContain('用户输入');expect(voc.textContent).toContain('2 轮');expect(voc.textContent).toContain('已确认送达');expect(voc.textContent).toContain('未知');expect(voc.textContent).toContain('已入库 VOC');expect(voc.textContent).toContain('voc-authorized-records');expect(box.querySelector('[data-application-id="ai-admin-agent"]')?.textContent).toContain('运行未知');expect(box.querySelector('[data-application-id="ai-daq-fae-agent"]')?.textContent).toContain('记录创建时间代理');expect(box.textContent).toContain('同一输入群组');expect(box.textContent).toContain('近 15 分钟');
});
it('keeps native HR counts separate from 369 historical turns and never links an unauthorized application',async()=>{
 data.applications[0].usage.window.turns={...data.applications[1].usage.window.turns,value:39};data.legacy_streams[0].usage.retained.turns={...data.applications[1].usage.window.turns,value:369};data.applications[0].can_use=false;
 await act(async()=>root.render(<OverviewPage/>));expect(box.querySelector('[data-application-id="hr-bot"]')?.textContent).toContain('39 轮');expect(box.querySelector('.runtime-history')?.textContent).toContain('369 轮');expect(box.querySelector('[aria-label="当前独立服务使用"]')?.textContent).not.toContain('369');expect(box.querySelector('a[href="/hr/"]')).toBeNull();
});
it('reports first request failure with no invented healthy or zero cards',async()=>{vi.mocked(fetch).mockRejectedValue(new Error('offline'));await act(async()=>root.render(<OverviewPage/>));expect(box.querySelector('[role="alert"]')?.textContent).toContain('读取失败');expect(box.querySelectorAll('[data-application-id]')).toHaveLength(0);});
it('shows stale source evidence without suppressing known metrics or inferring fresh health',async()=>{data.applications[1].source.status='stale';data.applications[1].source.error='source_read_failed';data.applications[1].runtime.stale=true;await act(async()=>root.render(<OverviewPage/>));const voc=box.querySelector('[data-application-id="voc"]')!;expect(voc.textContent).toContain('已过期');expect(voc.textContent).toContain('运行未知');expect(voc.textContent).toContain('2 轮');});

it('preserves authorized workspace destinations, dataset and actor visibility without conversation totals',async()=>{
 const app=data.applications.find(app=>app.id==='manufacturing')!;app.can_use=true;app.entry_path='/manufacturing/';app.dataset={mode:'demo',label:'授权演示项目'} as any;app.source.permission_scope='current_actor_projects';
 await act(async()=>root.render(<OverviewPage/>));const workspace=box.querySelector('[data-application-id="manufacturing"]')!;
 expect(workspace.querySelector('a')?.getAttribute('href')).toBe('/manufacturing/');expect(workspace.textContent).toContain('授权演示项目');expect(workspace.textContent).toContain('演示');expect(workspace.textContent).toContain('当前账号授权项目');expect(workspace.textContent).toContain('不加入会话');expect(workspace.textContent).toContain('运行观测不适用');
});
