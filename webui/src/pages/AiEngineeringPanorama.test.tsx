/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AiEngineeringLanding } from './AiEngineeringPage';
import { AiEngineeringApiError } from '../aiEngineeringApi';
const fetchPanorama=vi.hoisted(()=>vi.fn());
vi.mock('../panorama/panoramaApi',()=>({fetchPanorama}));
vi.mock('../panorama/PanoramaView',()=>({PanoramaView:({data,onAction,onEvidence}:any)=><div><h1>{data.title}</h1><button onClick={()=>onAction('brain')}>使用大脑</button><button onClick={()=>onEvidence('finance')}>经营依据</button></div>}));
const account:any={internal_user_id:'a',role:'platform_admin',display_name:'Admin',workspace_scopes:[]};
const client:any={fetchAccess:vi.fn(),fetchIndex:vi.fn(),fetchDocument:vi.fn()};
const box=document.createElement('div');document.body.append(box);const root=createRoot(box);
(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;
afterEach(async()=>{await act(async()=>root.render(null));vi.resetAllMocks();window.history.replaceState({},'', '/');});
describe('panorama is the homepage',()=>{
 it('loads authorized structured overview without forcing a document, and opens a real workspace route',async()=>{
  client.fetchAccess.mockResolvedValue({allowed:true});fetchPanorama.mockResolvedValue({title:'受保护总览'});const go=vi.fn();
  await act(async()=>root.render(<AiEngineeringLanding account={account} client={client} direct fallback={null} onNavigate={go}/>));
  expect(box.textContent).toContain('受保护总览');expect(client.fetchDocument).not.toHaveBeenCalled();expect(client.fetchIndex).not.toHaveBeenCalled();
  await act(async()=>box.querySelector('button')!.click());expect(go).toHaveBeenCalledWith('/brain');
 });
 it('clears the entire panorama on permission loss from an evidence request',async()=>{
  client.fetchAccess.mockResolvedValue({allowed:true});fetchPanorama.mockResolvedValue({title:'受保护总览'});client.fetchDocument.mockRejectedValue(new AiEngineeringApiError(403));
  await act(async()=>root.render(<AiEngineeringLanding account={account} client={client} direct fallback={null}/>));
  await act(async()=>[...box.querySelectorAll('button')].find(b=>b.textContent==='经营依据')!.click());
  expect(box.textContent).not.toContain('受保护总览');expect(box.textContent).toContain('无权访问');
 });
 it('does not fetch panorama until the server permits it',async()=>{
  client.fetchAccess.mockResolvedValue({allowed:false});
  await act(async()=>root.render(<AiEngineeringLanding account={account} client={client} direct fallback={null}/>));
  expect(fetchPanorama).not.toHaveBeenCalled();
 });
 it('clears an expired session and uses the existing login return path',async()=>{
  client.fetchAccess.mockRejectedValue(new AiEngineeringApiError(401)); const go=vi.fn();
  await act(async()=>root.render(<AiEngineeringLanding account={account} client={client} direct fallback={null} onNavigate={go}/>));
  expect(fetchPanorama).not.toHaveBeenCalled(); expect(go).toHaveBeenCalledWith('/login?return_path=%2F');
 });

});
