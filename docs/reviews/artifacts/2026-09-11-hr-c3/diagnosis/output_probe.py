import json,time,sys
from pathlib import Path
from app.hr_agent.model import _http_lines, _anthropic_events, collect_reply
p=json.loads(Path('/Users/neo/Developer/work/AI-Agent-Platform/.hr-agent/provider.json').read_text())
credential=Path(p['credential_file']).read_text().strip()
base=json.loads(Path('docs/reviews/artifacts/2026-09-11-hr-c3/summary-data-transport-probe.json').read_text())['request']
base['max_tokens']=8192
for mode in sys.argv[1:] or ['system','user_prefix']:
 body=json.loads(json.dumps(base))
 if mode=='user_prefix':
  instructions=body.pop('system')
  body['messages']=[{'role':'user','content':instructions+'\n\n'+body['messages'][0]['content']}]+body['messages'][1:]
 lines=[];start=time.monotonic()
 try:
  lines=list(_http_lines(p['endpoint'],{'Authorization':'Bearer '+credential,'anthropic-version':'2023-06-01','Content-Type':'application/json'},body,time.monotonic()+120))
  reply=collect_reply(_anthropic_events(lines));outcome={'status':'valid','text':reply.text}
 except Exception as error:outcome={'status':'invalid','code':getattr(error,'code',type(error).__name__)}
 shapes=[]
 for line in lines:
  if not line.startswith('data:'):continue
  item=json.loads(line[5:]);message=item.get('message',{});delta=item.get('delta',{});block=item.get('content_block',{})
  shapes.append({'type':item.get('type'),'reported_model':message.get('model'),'block_type':block.get('type'),'initial_text_characters':len(block.get('text','')),'delta_type':delta.get('type'),'text_characters':len(delta.get('text','')),'stop_reason':delta.get('stop_reason'),'usage':item.get('usage',message.get('usage'))})
 record={'instruction_delivery':mode,'request':body,'outcome':outcome,'elapsed_seconds':time.monotonic()-start,'event_shapes':shapes,'scope':'Public protocol diagnostic only; no candidate material, no identity guarantee beyond gateway report.'}
 Path('.superpowers/sdd/c3-diagnosis/'+mode+'-8192.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:record[k] for k in ['instruction_delivery','outcome','elapsed_seconds']},ensure_ascii=False),flush=True)
