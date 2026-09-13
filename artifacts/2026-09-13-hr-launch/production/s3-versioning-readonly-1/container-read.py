import hashlib,json,os
from app.attachments.worker_runtime import _build_s3_client
client=_build_s3_client();bucket=os.environ['PLATFORM_ATTACHMENT_S3_BUCKET']
try:
 response=client.get_bucket_versioning(Bucket=bucket)
 print(json.dumps({'bucket_sha256':hashlib.sha256(bucket.encode()).hexdigest(),'status':response.get('Status','Unversioned'),'http_status':response.get('ResponseMetadata',{}).get('HTTPStatusCode'),'writes':False}))
except Exception as error:
 response=getattr(error,'response',{})
 print(json.dumps({'status':'unknown','error_type':type(error).__name__,'error_code':response.get('Error',{}).get('Code'),'writes':False}))
 raise SystemExit(1)
