/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { OverviewPage } from './OverviewPage';
import { fetchFleetOverview, fetchOperationsBrief } from '../api';
import type { FleetAgent, FleetOverview } from '../types';
vi.mock('../api',()=>({fetchFleetOverview:vi.fn(),fetchOperationsBrief:vi.fn()}));
const agent = (id:string, visibility:'business'|'system'):FleetAgent => ({id,name:id,domain:visibility,description:id,glyph:'AI',accent:'people',visibility,state:'active',live_since:null,last_updated_at:null,total_conversations:visibility==='business'?8:900,conversations_last_7d:visibility==='business'?2:900,recent_summary:null}) as FleetAgent;
const overview:FleetOverview={summary:{total_agents:1,running_agents:1,active_agents:1,degraded_agents:0,offline_agents:0,checking_agents:0,total_conversations:8,conversations_last_7d:2,conversations_previous_7d:1,change_percent:100},trend:[],agents:[agent('business-service','business'),agent('system-monitor','system')],runtime_source:{healthy:true,checked_at:null,stale:false,error:null},usage_source:{healthy:true,checked_at:null,stale:false,error:null}};
let box:HTMLDivElement;let root:ReturnType<typeof createRoot>;
beforeEach(()=>{(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;box=document.createElement('div');document.body.append(box);root=createRoot(box);vi.mocked(fetchOperationsBrief).mockRejectedValue(new Error('unavailable'));vi.mocked(fetchFleetOverview).mockResolvedValue(overview);});
afterEach(async()=>{await act(async()=>root.unmount());box.remove();vi.resetAllMocks();});
it('renders one canonical clickable fleet partition while keeping system usage out of business totals and ranking',async()=>{
 await act(async()=>root.render(<OverviewPage/>));
 expect(box.querySelector('h1')?.textContent).toBe('运行中心');
 expect(box.querySelectorAll('.fleet-agent-card')).toHaveLength(2);
 expect(box.querySelector('[aria-label="Agents"] a')?.getAttribute('href')).toBe('/admin/agents/business-service');
 expect(box.querySelector('[aria-label="系统监控对象"] a')?.getAttribute('href')).toBe('/admin/agents/system-monitor');
 expect([...box.querySelectorAll('.fleet-summary-card strong')].map(node=>node.textContent)).toEqual(['1','1','8','2']);
 expect(box.querySelector('.ranking-list')?.textContent).not.toContain('system-monitor');
 expect(fetchFleetOverview).toHaveBeenCalledTimes(1);
});
it('preserves usage-source failure truth while retaining independently readable runtime entries',async()=>{
 vi.mocked(fetchFleetOverview).mockResolvedValue({...overview,usage_source:{healthy:false,checked_at:null,stale:false,error:'offline'}});
 await act(async()=>root.render(<OverviewPage/>));
 expect(box.querySelector('.source-banner')).not.toBeNull();expect(box.querySelector('.usage-unavailable-card')).not.toBeNull();
 expect(box.querySelectorAll('.fleet-agent-card')).toHaveLength(2);expect(box.querySelector('.ranking-list')).toBeNull();
});
it('reports fleet request failure without inventing healthy cards or a duplicate polling source',async()=>{
 vi.mocked(fetchFleetOverview).mockRejectedValue(new Error('offline'));
 await act(async()=>root.render(<OverviewPage/>));expect(box.querySelector('.error-banner')).not.toBeNull();expect(box.querySelectorAll('.fleet-agent-card')).toHaveLength(0);expect(fetchFleetOverview).toHaveBeenCalledTimes(1);
});

it('retains explicitly stale usage evidence with its warning instead of pretending the source is fresh',async()=>{
 vi.mocked(fetchFleetOverview).mockResolvedValue({...overview,usage_source:{healthy:false,checked_at:null,stale:true,error:'offline'}});
 await act(async()=>root.render(<OverviewPage/>));expect(box.querySelector('.source-banner')).not.toBeNull();expect(box.querySelector('.ranking-list')).not.toBeNull();expect(box.querySelector('.usage-unavailable-card')).toBeNull();
});
