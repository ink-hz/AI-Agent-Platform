import json,time
from pathlib import Path
from app.hr_agent.model import _http_lines,_anthropic_events,collect_reply
p=json.loads(Path('/Users/neo/Developer/work/AI-Agent-Platform/.hr-agent/provider.json').read_text());cred=Path(p['credential_file']).read_text().strip()
question='请用中文两句话说明：公开招聘职责写“协助项目经理”和“月度库存关账”，能否据此断定现有经理已经在位、流程正在运行？不要只回答是或否。'
cases=[('plain_no_tools',{},question),('plain_empty_tools',{'tools':[]},question),('plain_system',{'system':'请分析公开招聘资料，区分证据和推断。'},question),('plain_summary',{},'请总结以下公开资料，保留证据与不确定性：甲岗位职责为协助项目经理分配任务；乙岗位职责为月度库存关账。没有实际流程或人员在位记录。')]
for name,extra,text in cases:
 body={'model':p['model'],'messages':[{'role':'user','content':text}],'max_tokens':512,'stream':True,**extra};lines=[];start=time.monotonic()
 try:
  lines=list(_http_lines(p['endpoint'],{'Authorization':'Bearer '+cred,'anthropic-version':'2023-06-01','Content-Type':'application/json'},body,time.monotonic()+90));reply=collect_reply(_anthropic_events(lines));outcome={'status':'valid','text':reply.text}
 except Exception as e:outcome={'status':'invalid','code':getattr(e,'code',type(e).__name__)}
 shapes=[]
 for line in lines:
  if line.startswith('data:'):
   i=json.loads(line[5:]);shapes.append({'type':i.get('type'),'stop_reason':i.get('delta',{}).get('stop_reason'),'block_type':i.get('content_block',{}).get('type'),'text_characters':len(i.get('delta',{}).get('text','')),'reported_model':i.get('message',{}).get('model')})
 result={'name':name,'request':body,'outcome':outcome,'seconds':time.monotonic()-start,'event_shapes':shapes}
 Path('.superpowers/sdd/c3-diagnosis/'+name+'.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:result[k] for k in ('name','outcome','seconds')},ensure_ascii=False),flush=True)
