/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import fixture from '../../../backend/tests/fixtures/runtime_dashboard_v1.json';
import type { DashboardOverview } from '../runtimeDashboardTypes';
import { OverviewPage } from './OverviewPage';
import { RuntimeDashboard } from '../runtimeDashboardUi';
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
it('reports first request failure with no invented healthy or zero cards',async()=>{vi.mocked(fetch).mockRejectedValue(new Error('offline'));await act(async()=>root.render(<OverviewPage/>));expect(box.querySelector('.runtime-reconnecting')?.textContent).toContain('读取失败');expect(box.querySelectorAll('[data-application-id]')).toHaveLength(0);});
it('keeps the last source values and unknown health without an expired-state banner',async()=>{data.applications[1].source.status='stale';data.applications[1].source.error='source_read_failed';data.applications[1].runtime.stale=true;await act(async()=>root.render(<OverviewPage/>));const voc=box.querySelector('[data-application-id="voc"]')!;expect(voc.textContent).not.toContain('过期');expect(voc.querySelector('.runtime-source')?.getAttribute('data-source-status')).toBe('stale');expect(voc.textContent).toContain('更新待确认');expect(voc.textContent).toContain('运行未知');expect(voc.textContent).toContain('2 轮');});

it('preserves authorized workspace destinations, dataset and actor visibility without conversation totals',async()=>{
 const app=data.applications.find(app=>app.id==='manufacturing')!;app.can_use=true;app.entry_path='/manufacturing/';app.dataset={mode:'demo',label:'授权演示项目'} as any;app.source.permission_scope='current_actor_projects';
 await act(async()=>root.render(<OverviewPage/>));const workspace=box.querySelector('[data-application-id="manufacturing"]')!;
 expect(workspace.querySelector('a')?.getAttribute('href')).toBe('/manufacturing/');expect(workspace.textContent).toContain('授权演示项目');expect(workspace.textContent).toContain('演示');expect(workspace.textContent).toContain('当前账号授权项目');expect(workspace.textContent).toContain('不加入会话');expect(workspace.textContent).toContain('运行观测不适用');
});
it('keeps provenance and detailed trend data accessible behind closed disclosures',async()=>{
 await act(async()=>root.render(<OverviewPage/>));
 const voc=box.querySelector('[data-application-id="voc"]')!;
 const source=voc.querySelector('details.runtime-source') as HTMLDetailsElement;
 expect(source).not.toBeNull();expect(source.open).toBe(false);
 expect(source.getAttribute('data-source-status')).toBe('fresh');expect(source.querySelector('summary')?.textContent).toContain('统计口径');
 expect(source.textContent).toContain(data.applications.find(app=>app.id==='voc')!.source.reader_role!);
 expect(box.querySelector('svg[role="img"]')?.getAttribute('aria-label')).toContain('输入趋势');
 const table=box.querySelector('.runtime-trend-details') as HTMLDetailsElement;
 expect(table.open).toBe(false);expect(table.querySelectorAll('tbody tr')).toHaveLength(data.trend.length);
});
it.each([['not_configured','未配置健康观测'],['not_observed','尚未观测']] as const)('retains the %s coverage explanation without an error',async(coverage,label)=>{
 const app=data.applications[0];app.runtime.coverage=coverage;app.runtime.error=null;
 await act(async()=>root.render(<OverviewPage/>));
 expect(box.querySelector(`[data-application-id="${app.id}"] .runtime-observation`)?.textContent).toContain(label);
});
it('distinguishes unknown and zero days when a 30-day chart omits most date labels',async()=>{
 data.window.days=30;data.trend=Array.from({length:30},(_,i)=>({...structuredClone(data.trend[0]),date:`2026-10-${String(i+1).padStart(2,'0')}`}));
 data.trend[1].turns={...data.trend[1].turns,value:null,status:'unavailable'};
 data.trend[0].turns={...data.trend[0].turns,value:null,status:'unavailable'};
 data.trend[2].turns={...data.trend[2].turns,value:0,status:'observed'};
 await act(async()=>root.render(<RuntimeDashboard data={data}/>));
 const days=box.querySelectorAll('svg g');
 expect(days[0].querySelectorAll('text')).toHaveLength(2);
 expect(days[1].querySelector('text')?.textContent).toBe('?');
 expect(days[1].querySelector('rect')).toBeNull();
 expect(days[2].querySelector('circle')).not.toBeNull();
 expect(box.querySelector('.runtime-chart-legend')?.textContent).toContain('部分覆盖');
});
it('uses the chosen local homepage hierarchy and treats configured channels as history',async()=>{
 for(const channel of data.channel_agents){channel.source.status='stale';channel.source.error='source_stale';channel.source.observed_at=null;}
 await act(async()=>root.render(<OverviewPage/>));
 expect(box.querySelectorAll('.fleet-summary-grid > article')).toHaveLength(4);
 expect(box.querySelectorAll('.fleet-agent-grid [data-application-kind="agent"]')).toHaveLength(5);
 expect(box.querySelector('.runtime-channel-list')?.textContent).toContain('已配置');
 expect(box.querySelector('.runtime-channel-list')?.textContent).not.toContain('过期');
 expect(box.querySelector('.runtime-channel-list a[href="/marketing/"]')).toBeNull();
 expect(box.querySelector('.runtime-channel-list details')?.textContent).toContain('历史记录');
});
it('pauses background polling and refreshes immediately when the page becomes visible',async()=>{
 vi.useFakeTimers();const visibility=vi.spyOn(document,'visibilityState','get').mockReturnValue('visible');
 try {
  await act(async()=>root.render(<OverviewPage/>));expect(fetch).toHaveBeenCalledTimes(1);
  visibility.mockReturnValue('hidden');await act(async()=>document.dispatchEvent(new Event('visibilitychange')));
  await act(async()=>vi.advanceTimersByTimeAsync(40_000));expect(fetch).toHaveBeenCalledTimes(1);
  visibility.mockReturnValue('visible');await act(async()=>document.dispatchEvent(new Event('visibilitychange')));expect(fetch).toHaveBeenCalledTimes(2);
 } finally {visibility.mockRestore();vi.useRealTimers();}
});
it('keeps the latest visible-page response when an aborted background request settles late',async()=>{
 const visibility=vi.spyOn(document,'visibilityState','get').mockReturnValue('visible');
 const old=structuredClone(data);let finish!:(response:Response)=>void;
 vi.mocked(fetch).mockImplementationOnce(()=>new Promise<Response>(resolve=>{finish=resolve;}));
 await act(async()=>root.render(<OverviewPage/>));
 const signal=vi.mocked(fetch).mock.calls[0][1]?.signal;
 visibility.mockReturnValue('hidden');await act(async()=>document.dispatchEvent(new Event('visibilitychange')));expect(signal?.aborted).toBe(true);
 data.summary.usage.turns.value=9999;
 visibility.mockReturnValue('visible');await act(async()=>document.dispatchEvent(new Event('visibilitychange')));
 expect(box.querySelector('.summary-total')?.textContent).toContain('9,999');
 await act(async()=>finish(new Response(JSON.stringify(old))));
 expect(box.querySelector('.summary-total')?.textContent).toContain('9,999');
 expect(box.querySelector('.runtime-reconnecting')).toBeNull();expect(fetch).toHaveBeenCalledTimes(2);
});
it('retains real values on refresh failure and recovers through the visible retry action',async()=>{
 await act(async()=>root.render(<OverviewPage/>));
 const old=box.querySelector('.summary-total')!.textContent;
 vi.mocked(fetch).mockRejectedValueOnce(new Error('network unavailable'));
 await act(async()=>[...box.querySelectorAll('button')].find(button=>button.textContent==='立即刷新')!.click());
 expect(box.querySelector('.summary-total')?.textContent).toBe(old);
 expect(box.querySelector('.runtime-reconnecting')?.textContent).toContain('上次成功读取');expect(box.textContent).not.toContain('过期');
 await act(async()=>[...box.querySelectorAll('button')].find(button=>button.textContent==='立即重试')!.click());
 expect(box.querySelector('.runtime-reconnecting')).toBeNull();expect(fetch).toHaveBeenCalledTimes(3);
});
it('labels retained inputs accurately and does not turn an unknown timestamp into an empty history',async()=>{
 const app=data.applications[1];app.usage.last_activity_at=null;app.source.status='unavailable';
 await act(async()=>root.render(<OverviewPage/>));const card=box.querySelector(`[data-application-id="${app.id}"]`)!;
 expect(card.querySelector('.fleet-usage')?.textContent).toContain('来源保留输入');
 expect(card.querySelector('.fleet-usage')?.textContent).not.toContain('累计');
 expect(card.querySelector('.runtime-card-meta')?.textContent).toContain('暂无可用时间');
});
