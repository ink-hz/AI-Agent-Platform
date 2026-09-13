import subprocess,sys
expected='908c2f63e1de01f5226d6ddf8eb87dd4d1d3ff1edc45e534193f08203f81f819'
actual=subprocess.check_output(['/usr/bin/docker','--host','unix:///var/run/docker.sock','inspect','--format','{{.Id}}','orbbec-agent-platform-platform-attachments-1'],text=True).strip()
if actual!=expected:raise RuntimeError('attachment identity changed')
result=subprocess.run(['/usr/bin/docker','--host','unix:///var/run/docker.sock','exec','-i',expected,'python','-'],input=b"import hashlib,json,os\nfrom app.attachments.worker_runtime import _build_s3_client\nclient=_build_s3_client();bucket=os.environ['PLATFORM_ATTACHMENT_S3_BUCKET']\ntry:\n response=client.get_bucket_versioning(Bucket=bucket)\n print(json.dumps({'bucket_sha256':hashlib.sha256(bucket.encode()).hexdigest(),'status':response.get('Status','Unversioned'),'http_status':response.get('ResponseMetadata',{}).get('HTTPStatusCode'),'writes':False}))\nexcept Exception as error:\n response=getattr(error,'response',{})\n print(json.dumps({'status':'unknown','error_type':type(error).__name__,'error_code':response.get('Error',{}).get('Code'),'writes':False}))\n raise SystemExit(1)\n",timeout=30)
raise SystemExit(result.returncode)
