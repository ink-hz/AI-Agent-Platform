/** @vitest-environment jsdom */
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {it,expect,vi} from 'vitest';
import {HrPositionIndex} from './HrPositionIndex';
import type {Account} from '../../auth';
import type {HrApi} from '../../hrApi';
it('opens the position workflow from full-width cards, including archived records',async()=>{
 (globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
 const host=document.createElement('div');const root=createRoot(host);
 const api={listPositions:vi.fn().mockResolvedValue({items:[{positionId:'position-a',title:'DQE 工程师',department:'研发',locations:['深圳'],internalStatus:'archived',officialJobId:'J1000',sourceKind:'official_site',officialStatus:'inactive'}],nextCursor:null})} as unknown as HrApi;
 try{await act(async()=>root.render(<HrPositionIndex account={{csrf_token:'token'} as Account} api={api}/>));
 expect(host.querySelector('a[href="/hr/positions/position-a"]')).not.toBeNull();
 expect(host.querySelector('.hr-position-picker-option')).toBeNull();
 expect(host.textContent).toContain('已归档');expect(host.textContent).toContain('JD / JR');
 }finally{await act(async()=>root.unmount());}
});
