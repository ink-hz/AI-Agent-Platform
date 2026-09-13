import hashlib,json,subprocess
from pathlib import Path
base=Path('artifacts/2026-09-13-hr-launch');r=base/'d-real-run-2';e=json.loads((r/'evidence.json').read_text())
sha=lambda b:hashlib.sha256(b).hexdigest()
files={str(p):sha(p.read_bytes()) for p in r.iterdir() if p.is_file()}
f=Path('backend/tests/fixtures/hr_agent_d/scenario.md');files[str(f)]=sha(f.read_bytes())
checks=[];results=[]
for s in e['stages']:
 reads=[t['receipt']['data'] for t in s['tools'] if t['namespace']=='tool:read_resource' and t['receipt'].get('status')=='ok']
 for result in s['results']:
  p=next(r.glob('*-'+result['kind']+'.md'));assert p.read_text()==result['body']
  refs=result['source_refs']+result['preceding_refs']
  statuses=[{'ref':ref,'matching_complete_read':any(x.get('ref')==ref and x.get('coverage_complete') for x in reads), 'matching_same_stage_save_receipt':any(t['namespace']=='tool:save_result' and (t['receipt'].get('data') or {}).get('ref')==ref and isinstance((t['receipt'].get('data') or {}).get('body'),str) for t in s['tools'])} for ref in refs]
  checks.append({'stage':s['name'],'kind':result['kind'],'refs':statuses})
  results.append({'kind':result['kind'],'ref':result['ref'],'markdown_sha256':sha(p.read_bytes()),'markdown_equals_saved_body':True})
role=subprocess.check_output(['git','show','30dcd35:backend/hr_agent_knowledge/role.md'])
assert sha(role)==e['knowledge_release']['role']['sha256']
assert files[str(f)]==e['fixture_sha256']
data={'files_sha256':files,'fixture_hash_matches':True,'role_at_30dcd35_sha256':sha(role),'role_matches_run_release':True,'runner_source_as_recorded':e['runner_source'],'results':results,'source_read_checks':checks,'input_text_sha256':{k:sha(v['text'].encode()) for k,v in e['materials'].items()},'interview_original_text_sha256':sha(e['original_record']['text'].encode()),'prompts_sha256':{s['name']:sha(s['prompt'].encode()) for s in e['stages']},'note':'ResultRef.sha256 is service exact-revision identity, distinct from raw Markdown SHA. No claim that all process code equaled 30dcd35.'}
(base/'d-real-review-2/fingerprints-and-source-checks.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'results_body_equal':len(results),'refs_checked':sum(len(c['refs']) for c in checks),'refs_without_matching_complete_read':sum(not q['matching_complete_read'] for c in checks for q in c['refs']),'fixture_hash_matches':True,'role_hash_matches':True}))
