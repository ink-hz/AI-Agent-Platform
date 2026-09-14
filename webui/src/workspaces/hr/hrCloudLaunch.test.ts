/** @vitest-environment jsdom */
import {expect,it,vi} from 'vitest';
vi.mock('../../router',async(original)=>({...await original<typeof import('../../router')>(),navigate:vi.fn()}));
import {navigate} from '../../router';
import {openHrWork,takeHrWorkDraft} from './hrCloudLaunch';
it('opens the canonical position context without sending work and retains the private draft once',()=>{
 openHrWork('owner','11111111-1111-4111-8111-111111111111','整理岗位要求');
 expect(navigate).toHaveBeenCalledWith('/hr/?position=11111111-1111-4111-8111-111111111111');
 expect(takeHrWorkDraft('owner','11111111-1111-4111-8111-111111111111')).toEqual({text:'整理岗位要求',notice:''});
 expect(takeHrWorkDraft('owner','11111111-1111-4111-8111-111111111111')).toBeUndefined();
});
it('never restores another owner or position draft',()=>{
 openHrWork('owner','position-a','private');
 expect(takeHrWorkDraft('other','position-a')).toBeUndefined();
 openHrWork('owner','position-a','private');
 expect(takeHrWorkDraft('owner','position-b')).toBeUndefined();
});

it('opens the requested existing candidate panel with an owner-bound unsent launch',()=>{
 openHrWork('owner','position-a','','','candidate-materials');
 expect(takeHrWorkDraft('owner','position-a')).toEqual({text:'',notice:'',panel:'candidate-materials'});
});
