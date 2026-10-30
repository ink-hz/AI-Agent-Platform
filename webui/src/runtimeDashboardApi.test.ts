import { afterEach, expect, it, vi } from 'vitest';
import fixture from '../../backend/tests/fixtures/runtime_dashboard_v1.json';
import { fetchRuntimeDashboard } from './runtimeDashboardApi';
afterEach(()=>vi.unstubAllGlobals());
it.each([7,30] as const)('loads the exact versioned fixture for %s days with private authenticated request',async days=>{
 const response=structuredClone(fixture);response.window.days=days;
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify(response))));const controller=new AbortController();
 await expect(fetchRuntimeDashboard(days,controller.signal)).resolves.toEqual(response);
 expect(fetch).toHaveBeenCalledWith(`/api/dashboard/overview?days=${days}`,expect.objectContaining({credentials:'include',cache:'no-store',signal:controller.signal}));
});
it.each([401,403,503])('preserves HTTP failure %s instead of returning invented metrics',async status=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('{}',{status})));await expect(fetchRuntimeDashboard(7)).rejects.toThrow(String(status));});
it.each([
 (v:any)=>{v.schema_version=2;},(v:any)=>{v.summary.usage.turns.value=-1;},(v:any)=>{v.applications[0].can_use='true';},(v:any)=>{v.applications[0].source.status='healthy';},(v:any)=>{v.applications[0].usage.window.turns.value='0';},(v:any)=>{delete v.last_24h;},
])('rejects malformed wire data without coercion',async mutate=>{const data=structuredClone(fixture);mutate(data);vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify(data))));await expect(fetchRuntimeDashboard(7)).rejects.toThrow('Dashboard response invalid');});
it('rejects a response for a different selected window',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify(fixture))));await expect(fetchRuntimeDashboard(30)).rejects.toThrow('Dashboard response invalid');});
it.each(['input_time_basis','missing_question_time_records'])('accepts additive optional clock evidence when %s is omitted',async field=>{const data=structuredClone(fixture);delete (data.applications[0].source as any)[field];vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify(data))));await expect(fetchRuntimeDashboard(7)).resolves.toMatchObject({schema_version:1});});
it.each(['can_use','usage','runtime','deployment','business_sources'])('rejects omitted serialized application contract field %s',async field=>{const data=structuredClone(fixture);delete (data.applications[0] as any)[field];vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify(data))));await expect(fetchRuntimeDashboard(7)).rejects.toThrow('Dashboard response invalid');});
it('rejects unknown properties even when their names exist on Object.prototype',async()=>{
 const data=structuredClone(fixture);(data.applications[0] as any).toString='unexpected private field';vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify(data))));await expect(fetchRuntimeDashboard(7)).rejects.toThrow('Dashboard response invalid');
});
