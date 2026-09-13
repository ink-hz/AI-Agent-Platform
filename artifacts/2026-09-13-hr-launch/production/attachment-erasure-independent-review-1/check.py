import hashlib,importlib.util,json,pathlib,socket,subprocess,sys,threading,time
root=pathlib.Path.cwd();out=root/'artifacts/2026-09-13-hr-launch/production/attachment-erasure-independent-review-1';commit='961bac7502e3f66ed7278ffd208a342591a2c832';prefix='artifacts/2026-09-13-hr-launch/production/'
files=[prefix+'attachment_erasure_canary.py',prefix+'api_canary.py','backend/tests/test_attachment_erasure_production_canary.py','backend/tests/hr_agent_support.py',prefix+'attachment-erasure-engineering/README.md',prefix+'attachment-erasure-engineering/green-final-1/command.json',prefix+'attachment-erasure-engineering/green-final-1/output.log']
manifest=[]
for f in files:
 data=subprocess.check_output(['git','show',commit+':'+f]);dest=out/'snapshot'/f;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data);manifest.append({'path':f,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
(out/'fingerprints.json').write_text(json.dumps({'commit':commit,'files':manifest},indent=2)+'\n')
sys.path.insert(0,str(out/'snapshot'/prefix));from attachment_erasure_canary import AttachmentCanary
import httpx
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
def server():
 conn,_=listener.accept()
 with conn:
  conn.recv(65536);body=b'{"status":"synthetic-slow-stream"}'
  conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n')
  for c in body:
   conn.sendall(bytes([c]));time.sleep(.025)
 listener.close()
thread=threading.Thread(target=server,daemon=True);thread.start()
config={'owner_id':'00000000-0000-0000-0000-000000000001','session_cookie':'synthetic-local-only','csrf':'synthetic-local-only','public_origin':f'http://127.0.0.1:{port}','api_base_url':f'http://127.0.0.1:{port}'}
with httpx.Client(trust_env=False) as client:
 runner=AttachmentCanary(config,out/'local-only-ledger',client);runner.ledger['deadline']=time.time()+.1;runner.save();start=time.monotonic();result=runner.request('GET','/api/v1/account');elapsed=time.monotonic()-start
 print(json.dumps({'boundary':'local loopback slow-stream; synthetic headers; config loader intentionally bypassed to avoid TLS fixture; real httpx timeout mechanics','remaining_seconds':.1,'elapsed_seconds':elapsed,'returned_after_deadline':time.time()>runner.ledger['deadline'],'result':result},indent=2))
 assert elapsed>.1,'expected original deadline defect'
