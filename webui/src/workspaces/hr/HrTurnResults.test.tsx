/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { it,expect,vi } from 'vitest';
import { HrTurnResults } from './HrTurnResults';
it('opens the exact result and drafts only the user selected standard, without submitting',async()=>{
  (globalThis as typeof globalThis & {IS_REACT_ACT_ENVIRONMENT:boolean}).IS_REACT_ACT_ENVIRONMENT=true;
  const div=document.createElement('div');document.body.append(div);const root=createRoot(div);const onDraft=vi.fn();
  const fetcher=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify({positionId:'position-a',result:{schemaId:'hr.standard-proposal.v1',title:'岗位标准建议',baseContextVersionId:'base-a',changes:[{changeId:'mission-1',module:'mission',markdown:'明确目标'},{changeId:'unknown-1',module:'unknowns',markdown:'预算未知'}]}})));
  try {
    await act(async()=>root.render(<HrTurnResults turnId="turn-a" readOnly={false} onDraft={onDraft} data={{turns:[{turnId:'turn-a',positionTitle:'岗位 A',scope:{positionId:'position-a',positionCandidateIds:[],attachmentIds:[]}}],results:[{turnId:'turn-a',resultId:'result-a',schemaId:'hr.standard-proposal.v1',contentSha256:'a'.repeat(64)}]}}/>));
    await act(async()=>{const detail=div.querySelector('details')!;detail.open=true;detail.dispatchEvent(new Event('toggle'));});
    expect(fetcher.mock.calls[0]?.[0]).toContain('/api/v1/hr/results/result-a');
    expect(div.querySelector('button')?.disabled).toBe(true);
    await act(async()=>{div.querySelector<HTMLInputElement>('input')!.click();});
    await act(async()=>{div.querySelector('button')!.click();});
    expect(onDraft).toHaveBeenCalledWith(expect.objectContaining({text:'确认所选的 1 项岗位标准。',standardConsent:{proposalResultId:'result-a',proposalContentSha256:'a'.repeat(64),expectedContextVersionId:'base-a',selectedChangeIds:['mission-1']}}),'position-a');
    expect(fetcher).toHaveBeenCalledTimes(1);
  }finally{await act(async()=>root.unmount());div.remove();vi.restoreAllMocks();}
});
