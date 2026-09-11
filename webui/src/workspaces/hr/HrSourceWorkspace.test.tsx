/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { HrPanoramaWorkspace } from './HrPanoramaWorkspace';
import type { Account } from '../../auth';
const account = { internal_user_id: 'reader', csrf_token: 'token' } as Account;
const company = {company_key:'insta360',name:'影石',aliases:['Insta360'],job_count:1,coverage_state:'partial',document_state:'succeeded',observed_at:'2026-09-06'};
const catalog={edition:'source-1',source_bundle_id:'bundle-1',observed_at:'2026-09-06',job_count:1,companies:[company]};
const summary={job_id:'job-1',title:'嵌入式工程师',location:'深圳',status:'open',channel:'社招',source_url:'https://example.com/job',observed_at:'2026-09-06'};
const page={edition:'source-1',company,documents:[{title:'官网资料',text:'真实官网正文',source_url:'https://example.com',observed_at:'2026-09-06',evidence_sha256:'abc'}],channels:[{channel:'社招',source_url:'https://example.com/jobs',state:'succeeded',observed_at:'2026-09-06',job_count:1,error_code:null}],limitations:['另一个渠道采集失败'],locations:['深圳'],channel_options:['社招'],items:[summary],total:1,offset:0,limit:25};
const detail={...summary,edition:'source-1',company_key:'insta360',duty:'1. 开发设备\n2. 测试交付',requirement:'熟悉 C++',fields:[{label:'学历',value:'本科'}],evidence_sha256:'abc',public_job_key:'source-job-1',source_kind:'job',content_note:null};
let container:HTMLDivElement; let root:Root;
function mockFetch(){return vi.fn(async (url:string) => new Response(JSON.stringify(url.endsWith('/sources')?catalog:url.includes('/jobs/')?detail:url.includes('/sources/')?page:{edition:'r1',observed_at:'2026-09-06',analyzed_at:'2026-09-09',covered_job_identities:1,companies:[{id:'insta360',name:'影石'}],questions:[],articles:[]}),{status:200}));}
function click(text:string){const button=[...container.querySelectorAll('button')].find(b=>b.textContent?.trim()===text);expect(button).toBeDefined();return act(async()=>button!.click());}
beforeEach(()=>{(globalThis as any).IS_REACT_ACT_ENVIRONMENT=true;history.replaceState({},'','/hr/panorama');container=document.createElement('div');document.body.append(container);root=createRoot(container);});
afterEach(async()=>{await act(async()=>root.unmount());container.remove();vi.unstubAllGlobals();});
it('defaults to sources and preserves company and selected job between layers',async()=>{
 const fetcher=mockFetch();vi.stubGlobal('fetch',fetcher);
 await act(async()=>root.render(<HrPanoramaWorkspace account={account}/>));
 expect(container.querySelector('[data-layer="sources"]')?.hasAttribute('hidden')).toBe(false);
 await act(async()=> (container.querySelector('[data-source-company="insta360"]') as HTMLButtonElement).click());
 expect(container.textContent).toContain('另一个渠道采集失败');expect(container.textContent).toContain('真实官网正文');
 await act(async()=> (container.querySelector('[data-source-job="job-1"]') as HTMLButtonElement).click());
 expect(container.textContent).toContain('1. 开发设备');expect(container.textContent).toContain('熟悉 C++');
 expect(fetcher.mock.calls.some(([u])=>u.includes('/jobs/job-1?edition=source-1'))).toBe(true);
 await click('AI 分析报告');expect(location.search).toContain('research_company=insta360');
 await click('原始资料');expect(location.search).toContain('source_job=job-1');
 await click('返回岗位列表');expect(location.search).not.toContain('source_job=');expect(container.querySelector('[data-source-job="job-1"]')).not.toBeNull();
});
it.each([401,403])('removes source content on denied detail (%s)',async status=>{
 const fetcher=mockFetch();vi.stubGlobal('fetch',fetcher);history.replaceState({},'','/hr/panorama?research_company=insta360');
 await act(async()=>root.render(<HrPanoramaWorkspace account={account}/>));fetcher.mockImplementation(async()=>new Response('{}',{status}));
 await act(async()=> (container.querySelector('[data-source-job="job-1"]') as HTMLButtonElement).click());
 expect(container.textContent).not.toContain('真实官网正文');expect(container.textContent).toContain(status===401?'登录状态已失效':'没有 HR 情报权限');
});
it('keeps old research deep links on the report layer',async()=>{
 vi.stubGlobal('fetch',mockFetch());history.replaceState({},'','/hr/panorama?research=missing&edition=r1');
 await act(async()=>root.render(<HrPanoramaWorkspace account={account}/>));
 expect(container.querySelector('[data-layer="research"]')?.hasAttribute('hidden')).toBe(false);
 expect(container.querySelector('[data-layer="sources"]')?.hasAttribute('hidden')).toBe(true);
});
