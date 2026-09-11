import json,time,sys
from pathlib import Path
from app.hr_agent.model import ConfiguredHttpModelPort,ProviderProfile,_http_lines,_anthropic_events,collect_reply
from app.hr_agent.types import ModelRequest
from uuid import UUID
root=Path('.superpowers/sdd/c3-diagnosis');p=json.loads(Path('/Users/neo/Developer/work/AI-Agent-Platform/.hr-agent/provider.json').read_text());p['timeout_seconds']=300
r=json.loads(Path('.superpowers/sdd/c3-opus5-subsidiary-run10/public-requests.json').read_text())[-1];r['attempt_id']=UUID(r['attempt_id']);mode='16384'
r['max_output_tokens']=16384
request=ModelRequest(**r)
port=ConfiguredHttpModelPort.from_mapping(p);cred=Path(p['credential_file']).read_text().strip();headers,body=port._wire_request(request,cred)
lines=[];started=time.monotonic()
try:
 lines=list(_http_lines(p['endpoint'],headers,body,time.monotonic()+300));reply=collect_reply(_anthropic_events(lines));result={'status':'valid','text':reply.text,'usage':reply.usage.raw,'tool_calls':[{'name':t.name,'arguments':t.arguments} for t in reply.tool_calls]}
except Exception as e:result={'status':'invalid','code':getattr(e,'code',type(e).__name__)}
shapes=[]
for line in lines:
 if not line.startswith('data:'):continue
 i=json.loads(line[5:]);d=i.get('delta',{});m=i.get('message',{});b=i.get('content_block',{});shapes.append({'type':i.get('type'),'stop_reason':d.get('stop_reason'),'block_type':b.get('type'),'initial_text_characters':len(b.get('text','')),'delta_type':d.get('type'),'text_characters':len(d.get('text','')),'usage':i.get('usage',m.get('usage')),'reported_model':m.get('model'),'tool_json_fragment':d.get('partial_json')})
out={'request':body,'request_source':'run10/public-requests.json last failed work request; only max_output_tokens increased to 16384; no tool execution','result':result,'elapsed_seconds':time.monotonic()-started,'event_shapes':shapes};(root/'run10-output-16384.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':result['status'],'code':result.get('code'),'text_chars':len(result.get('text','')),'seconds':out['elapsed_seconds']},ensure_ascii=False))
