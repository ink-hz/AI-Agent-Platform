import { afterEach, expect, it, vi } from 'vitest';
import { manufacturingAccess } from './manufacturingApi';

const good = {allowed:true, available:true, can_edit:true, release:'test', protocol:1, workflow_available:true, workflow_access:true, legacy_access:false, configuration_admin:false, dataset:{id:'demo',mode:'demo',label:'Demo'}, user:{id:'00000000-0000-4000-8000-000000000001',display_name:'Planner'}, project_ids:['00000000-0000-4000-8000-000000000002']};
function response(body:unknown, status=200) {return {ok:status===200,status,json:async()=>body} as Response;}
afterEach(()=>vi.unstubAllGlobals());
it('accepts the complete protocol-1 bootstrap and disabled legacy-only shape',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response(good)));
 expect(await manufacturingAccess()).toEqual(good);
 const legacy={...good,workflow_available:false,workflow_access:false,dataset:null,project_ids:[],legacy_access:true,configuration_admin:true};
 vi.mocked(fetch).mockResolvedValue(response(legacy));expect(await manufacturingAccess()).toEqual(legacy);
});
it.each([
 null, [], {...good,protocol:2}, {...good,dataset:null}, {...good,dataset:{id:'x',mode:'invented',label:'x'}},
 {...good,user:{id:'not-a-uuid',display_name:'x'}}, {...good,user:[]}, {...good,project_ids:'id'}, {...good,project_ids:['bad']},
 {...good,can_edit:'true'}, {...good,configuration_admin:undefined}, {...good,workflow_access:undefined},
 {...good,release:undefined}, {...good,workflow_available:false}, {...good,dataset:{id:1,mode:'demo',label:'x'}},
 {...good,user:{id:good.user.id}}, {allowed:true,available:true},
])('rejects malformed protocol bootstrap %#',async(body)=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response(body)));
 await expect(manufacturingAccess()).rejects.toThrow();
 expect(fetch).toHaveBeenCalledTimes(1);
});
it('falls back only for a missing workflow endpoint, validates legacy response',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(response({},404)).mockResolvedValueOnce(response({allowed:true,available:true,can_edit:true,release:'old'})));
 expect(await manufacturingAccess()).toEqual({allowed:true,available:true,can_edit:true,release:'old'});
 expect(fetch).toHaveBeenCalledTimes(2);
 vi.mocked(fetch).mockResolvedValue(response({},503));await expect(manufacturingAccess()).rejects.toMatchObject({status:503});
 expect(fetch).toHaveBeenCalledTimes(3);
});
