/** @vitest-environment jsdom */
import { readFileSync } from 'node:fs';
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyFailure, applySuccess, initialDashboardState, startPolling } from './dashboard';
import { fetchRuntimeDashboard } from './runtimeDashboardApi';
import { fetchDeployment } from './api';
import { fetchAgentCatalog, fetchManagementAgentCatalog } from './brainApi';
import { AppShell } from './AppShell';
import type { Account } from './auth';
import { OverviewPage } from './pages/OverviewPage';
import fixture from '../../backend/tests/fixtures/runtime_dashboard_v1.json';
import type { DashboardOverview } from './runtimeDashboardTypes';
import type { ClusterSnapshot } from './types';
vi.mock('./api',()=>({fetchDeployment:vi.fn().mockResolvedValue(null)}));
vi.mock('./brainApi',()=>({fetchAgentCatalog:vi.fn().mockResolvedValue([]),fetchManagementAgentCatalog:vi.fn().mockResolvedValue([])}));
vi.mock('./runtimeDashboardApi',()=>({fetchRuntimeDashboard:vi.fn()}));
const data=fixture as unknown as DashboardOverview;
const snapshot:ClusterSnapshot={summary:{total:0,healthy:0,degraded:0,offline:0,checking:0},source:{healthy:true,checked_at:null,error:null},instances:[]};
function deferred<T>() {let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done;});return {promise,resolve};}
describe('dashboard state',()=>{
 it('stores a successful snapshot and clears degradation',()=>expect(applySuccess({snapshot:null,degraded:true},snapshot)).toEqual({snapshot,degraded:false}));
 it('keeps the previous snapshot when refresh fails',()=>expect(applyFailure({snapshot,degraded:false})).toEqual({snapshot,degraded:true}));
 it('starts without a fabricated snapshot',()=>expect(initialDashboardState).toEqual({snapshot:null,degraded:false}));
});
it('schedules the next run only after the current run settles',async()=>{
 const pending=deferred<void>();const task=vi.fn(()=>pending.promise);const schedule=vi.fn(()=>1);const cancel=vi.fn();const stop=startPolling(task,30_000,schedule,cancel);
 expect(task).toHaveBeenCalledTimes(1);expect(schedule).not.toHaveBeenCalled();pending.resolve();await Promise.resolve();await Promise.resolve();expect(schedule).toHaveBeenCalledWith(expect.any(Function),30_000);stop();expect(cancel).toHaveBeenCalledWith(1);
});
it('uses only the unified factual API and does not infer all healthy or restore old entry cards',()=>{
 const source=readFileSync('src/pages/OverviewPage.tsx','utf8');expect(source).not.toContain('fetchFleetOverview');expect(source).not.toContain('fetchOperationsBrief');expect(source).not.toContain('FleetAgentCard');
});
describe('Overview snapshot lifecycle',()=>{
 let box:HTMLDivElement;let root:ReturnType<typeof createRoot>;
 beforeEach(()=>{vi.useFakeTimers();vi.mocked(fetchDeployment).mockRejectedValue(new Error('deployment not observed'));vi.mocked(fetchAgentCatalog).mockResolvedValue([]);vi.mocked(fetchManagementAgentCatalog).mockResolvedValue([]);vi.mocked(fetchRuntimeDashboard).mockReset().mockResolvedValue(data);(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;box=document.createElement('div');document.body.append(box);root=createRoot(box);});
 afterEach(async()=>{await act(async()=>root.unmount());box.remove();vi.useRealTimers();vi.restoreAllMocks();});
 const calls=()=>vi.mocked(fetchRuntimeDashboard).mock.calls;
 async function render(identityKey='owner'){await act(async()=>root.render(createElement(OverviewPage,{identityKey})));}
 it('loads one snapshot and refreshes only after the 30 second cadence',async()=>{
  await render();expect(box.textContent).toContain('数据覆盖');expect(calls()).toHaveLength(1);await act(async()=>vi.advanceTimersByTimeAsync(29_999));expect(calls()).toHaveLength(1);await act(async()=>vi.advanceTimersByTimeAsync(1));expect(calls()).toHaveLength(2);
 });
 it('retains the exact previous snapshot with a stale warning after a refresh error',async()=>{
  vi.mocked(fetchRuntimeDashboard).mockResolvedValueOnce(data).mockRejectedValue(new Error('offline'));await render();await act(async()=>vi.advanceTimersByTimeAsync(30_000));expect(box.querySelectorAll('[data-application-id]')).toHaveLength(7);expect(box.querySelector('[role="alert"]')?.textContent).toContain('数据已过期');expect(calls()).toHaveLength(2);await act(async()=>vi.advanceTimersByTimeAsync(29_999));expect(calls()).toHaveLength(2);
 });
 it('aborts the active request on cleanup and ignores its late resolution',async()=>{
  const late=deferred<DashboardOverview>();vi.mocked(fetchRuntimeDashboard).mockReturnValue(late.promise);await render();const signal=calls()[0][1]!;await act(async()=>root.unmount());root=createRoot(box);expect(signal.aborted).toBe(true);await act(async()=>late.resolve(data));await act(async()=>vi.advanceTimersByTimeAsync(60_000));expect(box.textContent).toBe('');expect(calls()).toHaveLength(1);
 });
 it('times out once without overlapping an uncooperative request or accepting its late response',async()=>{
  const late=deferred<DashboardOverview>();vi.mocked(fetchRuntimeDashboard).mockReturnValue(late.promise);await render();await act(async()=>vi.advanceTimersByTimeAsync(5_000));expect(calls()[0][1]?.aborted).toBe(true);expect(box.querySelector('[role="alert"]')).not.toBeNull();await act(async()=>vi.advanceTimersByTimeAsync(60_000));expect(calls()).toHaveLength(1);await act(async()=>late.resolve(data));expect(box.querySelectorAll('[data-application-id]')).toHaveLength(0);await act(async()=>vi.advanceTimersByTimeAsync(29_999));expect(calls()).toHaveLength(1);
 });
 it('marks retained data stale when a later cycle times out and rejects the replacement',async()=>{
  const late=deferred<DashboardOverview>();vi.mocked(fetchRuntimeDashboard).mockResolvedValueOnce(data).mockReturnValueOnce(late.promise);await render();await act(async()=>vi.advanceTimersByTimeAsync(35_000));expect(box.querySelector('[role="alert"]')?.textContent).toContain('已过期');await act(async()=>late.resolve({...data,inventory:{...data.inventory,applications:999}}));expect(box.textContent).not.toContain('999');expect(box.querySelectorAll('[data-application-id]')).toHaveLength(7);
 });
 it('clears previous data and aborts before rendering a new window, ignoring late old-window results',async()=>{
  const old=deferred<DashboardOverview>();const next=deferred<DashboardOverview>();vi.mocked(fetchRuntimeDashboard).mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);await render();const signal=calls()[0][1]!;await act(async()=>{const select=box.querySelector('select')!;select.value='30';select.dispatchEvent(new Event('change',{bubbles:true}));});expect(signal.aborted).toBe(true);expect(calls()[1][0]).toBe(30);await act(async()=>old.resolve(data));expect(box.querySelectorAll('[data-application-id]')).toHaveLength(0);await act(async()=>next.resolve({...data,window:{...data.window,days:30}}));expect(box.textContent).toContain('当前独立服务使用 · 近 30 天');
 });
 it('clears previous account data synchronously and ignores late previous-account refresh',async()=>{
  const late=deferred<DashboardOverview>();const next=deferred<DashboardOverview>();vi.mocked(fetchRuntimeDashboard).mockResolvedValueOnce(data).mockReturnValueOnce(late.promise).mockReturnValueOnce(next.promise);await render();await act(async()=>vi.advanceTimersByTimeAsync(30_000));const signal=calls()[1][1]!;await render('member');expect(signal.aborted).toBe(true);expect(box.querySelectorAll('[data-application-id]')).toHaveLength(0);await act(async()=>late.resolve(data));expect(box.querySelectorAll('[data-application-id]')).toHaveLength(0);await act(async()=>next.resolve(data));expect(box.querySelectorAll('[data-application-id]')).toHaveLength(7);
 });
 it('binds the real AppShell account boundary to dashboard cleanup without an explicit identity prop',async()=>{
  const late=deferred<DashboardOverview>();vi.mocked(fetchRuntimeDashboard).mockResolvedValueOnce(data).mockReturnValue(late.promise);
  const account:Account={internal_user_id:'owner',display_name:'Owner',role:'platform_owner',departments:[],gender:null,workspace_scopes:[],observation_agent_ids:[],directory_freshness:'fresh',hard_stale_read_only:false,csrf_token:'csrf'};
  const shell=(internal_user_id:string)=>createElement(AppShell,{route:{name:'admin-overview'},account:{...account,internal_user_id},children:createElement(OverviewPage)});
  await act(async()=>root.render(shell('first')));expect(box.querySelectorAll('[data-application-id]')).toHaveLength(7);
  await act(async()=>root.render(shell('second')));expect(box.querySelectorAll('[data-application-id]')).toHaveLength(0);const signal=calls()[1][1]!;
  await act(async()=>root.render(shell('third')));expect(signal.aborted).toBe(true);await act(async()=>late.resolve(data));
 });
 it('retries explicitly after a first failure and clears the error on success',async()=>{
  vi.mocked(fetchRuntimeDashboard).mockRejectedValueOnce(new Error('503')).mockResolvedValueOnce(data);await render();expect(box.querySelector('[role="alert"]')).not.toBeNull();expect(calls()).toHaveLength(1);await act(async()=>box.querySelector<HTMLButtonElement>('[role="alert"] button')!.click());expect(box.querySelector('[role="alert"]')).toBeNull();expect(calls()).toHaveLength(2);
 });
});
