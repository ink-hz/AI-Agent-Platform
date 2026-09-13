import hashlib,json,pathlib,shutil,subprocess
root=pathlib.Path.cwd(); out=root/'artifacts/2026-09-13-hr-launch/history-real-review-1'; launch=root/'artifacts/2026-09-13-hr-launch'
paths=list((launch/'history-real-run-1').rglob('*'))+list((launch/'runs/history-real-1').rglob('*'))
corpus=launch/'history/replay-v2'; paths += [corpus/'corpus.json',corpus/'manifest.json']
for case in ['H01','H03','H06','H13']: paths+=list((corpus/'fixtures'/case).rglob('*'))
manifest=[]
for p in paths:
 if not p.is_file(): continue
 rel=p.relative_to(root); dest=out/'snapshot'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
 manifest.append({'path':str(rel),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size})
for name in ['backend/tests/helpers/hr_history_replay.py','backend/tests/test_hr_agent_history_replay.py','backend/hr_agent_knowledge/role.md','HR_Agent工作流.md','HR总体架构设计.md']:
 data=subprocess.check_output(['git','show','fc339d8:'+name]); p=out/'baseline-fc339d8'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
 manifest.append({'path':'baseline-fc339d8/'+name,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
checks=[]
for case in ['H01','H03','H06','H13']:
 for p in sorted((launch/'history-real-run-1'/case).glob('turn-*.json')):
  d=json.loads(p.read_text()); reads=[t['receipt']['data'] for t in d['persisted']['tools'] if t['namespace']=='tool:read_resource' and t['receipt'].get('data')]; refs=[r['ref'] for r in reads]
  checks.append({'case':case,'turn':d['number'],'reads':[{'ref':r['ref'],'coverage_complete':r.get('coverage_complete'),'characters':r.get('total_characters'),'text_sha256':hashlib.sha256(r.get('text','').encode()).hexdigest()} for r in reads], 'result_sources_all_read':all(ref in refs for result in d['persisted']['results'] for ref in result['source_refs']),'results':[{'ref':r['ref'],'kind':r['kind'],'body_utf8_sha256':hashlib.sha256(r['body'].encode()).hexdigest()} for r in d['persisted']['results']]})
(out/'fingerprints.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n');(out/'reference-audit.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'snapshot_files':len(manifest),'all_result_sources_read':all(c['result_sources_all_read'] for c in checks),'turns':len(checks)},indent=2))
