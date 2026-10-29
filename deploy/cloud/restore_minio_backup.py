#!/usr/bin/env python3
"""Restore one authenticated MinIO archive to a disposable local tmpfs service."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
from urllib.parse import quote, urlencode
import uuid
import xml.etree.ElementTree as ET

LOCAL_ENDPOINT = 'unix:///Users/neo/.colima/default/docker.sock'
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}\Z')
CONTAINER_ID = re.compile(r'[0-9a-f]{64}\Z')
STAGES = {'manifest','recovery_key','artifact_integrity','decrypt','attachments','docker_endpoint',
          'image','docker','container_boundary','extract','authentication','startup','inventory',
          'cleanup','interrupted','unexpected'}


class MinioRestoreError(RuntimeError):
    def __init__(self, stage):
        self.stage = stage if stage in STAGES else 'unexpected'
        super().__init__(self.stage)


def load_shared():
    source = Path(__file__).resolve().with_name('restore_backup_set.py')
    spec = importlib.util.spec_from_file_location('same_checkout_offline_restore', source)
    value = importlib.util.module_from_spec(spec); sys.modules[spec.name] = value; spec.loader.exec_module(value)
    return value


def inventory(client, required_bucket=None):
    """Only S3 GET/list APIs; key/version/upload identifiers stay in process memory."""
    import hashlib
    import json
    import time
    try:
        deadline = time.monotonic() + 120
        buckets, bucket_cursors, request = [], set(), {}
        for _ in range(128):
            response = client.list_buckets(**request)
            buckets.extend(value['Name'] for value in response['Buckets'])
            cursor = response.get('ContinuationToken')
            if not cursor: break
            if not isinstance(cursor, str) or cursor in bucket_cursors: raise ValueError()
            bucket_cursors.add(cursor); request = {'ContinuationToken':cursor}
        else: raise ValueError()
        if (len(buckets) > 128 or len(set(buckets)) != len(buckets)
                or any(not isinstance(value,str) or not value for value in buckets)
                or required_bucket is not None and required_bucket not in buckets): raise ValueError()
        reports = []
        for bucket in sorted(buckets):
            records = []
            counts = dict(objects=0, versions=0, delete_markers=0, multipart_uploads=0, object_bytes=0)
            versioning = client.get_bucket_versioning(Bucket=bucket).get('Status')
            if versioning not in (None,'Enabled','Suspended'): raise ValueError()
            for method, fields, marker_fields, request_markers, page_size in (
                ('list_objects_v2', [('Contents','objects')], ['NextContinuationToken'], ['ContinuationToken'], 'MaxKeys'),
                ('list_object_versions', [('Versions','versions'),('DeleteMarkers','delete_markers')], ['NextKeyMarker','NextVersionIdMarker'], ['KeyMarker','VersionIdMarker'], 'MaxKeys'),
                ('list_multipart_uploads', [('Uploads','multipart_uploads')], ['NextKeyMarker','NextUploadIdMarker'], ['KeyMarker','UploadIdMarker'], 'MaxUploads'),
            ):
                request, cursors = {'Bucket':bucket,page_size:1000}, set()
                for _ in range(128):
                    if time.monotonic() > deadline: raise ValueError()
                    response = getattr(client,method)(**request)
                    if type(response.get('IsTruncated')) is not bool: raise ValueError()
                    for field, kind in fields:
                        values = response.get(field, [])
                        if not isinstance(values,list): raise ValueError()
                        for value in values:
                            key = value['Key']
                            if not isinstance(key,str) or not key: raise ValueError()
                            record = [kind,key]
                            if kind in ('objects','versions'):
                                size = value['Size']; etag = value.get('ETag','')
                                if type(size) is not int or size < 0 or not isinstance(etag,str): raise ValueError()
                                record += [size,etag]
                                if kind == 'objects': counts['object_bytes'] += size
                            if kind in ('versions','delete_markers'):
                                version = value['VersionId']; latest = value['IsLatest']
                                if not isinstance(version,str) or not version or type(latest) is not bool: raise ValueError()
                                record += [version,latest]
                            if kind == 'multipart_uploads':
                                upload = value['UploadId']
                                if not isinstance(upload,str) or not upload: raise ValueError()
                                record += [upload]
                            counts[kind] += 1; records.append(record)
                            if len(records) > 100000: raise ValueError()
                    if not response['IsTruncated']: break
                    cursor = tuple(response.get(field) for field in marker_fields)
                    if any(not isinstance(value,str) or not value for value in cursor) or cursor in cursors: raise ValueError()
                    cursors.add(cursor); request.update(zip(request_markers,cursor))
                else: raise ValueError()
            encoded = [json.dumps(record,separators=(',',':'),ensure_ascii=True) for record in records]
            if len(encoded) != len(set(encoded)): raise ValueError()
            digest = hashlib.sha256()
            for record in sorted(encoded): digest.update(record.encode()+b'\n')
            reports.append(dict(bucket=bucket, versioning=versioning, sha256=digest.hexdigest(), **counts))
        return {'scope':'all_visible_buckets_including_versions_delete_markers_multipart','bucket_count':len(reports),'buckets':reports}
    except MinioRestoreError:
        raise
    except Exception:
        raise MinioRestoreError('inventory') from None


def boundary(value, *, identity, image, name, attempt):
    if (value.get('id') != identity or value.get('image') != image or value.get('name') != '/'+name
            or (value.get('labels') or {}).get('io.orbbec.minio-restore.attempt') != attempt
            or value.get('network_mode') != 'none' or any((value.get('ports') or {}).values())
            or any((value.get('port_bindings') or {}).values())
            or any(item.get('Type') != 'tmpfs' for item in value.get('mounts',[]))
            or set(value.get('tmpfs') or {}) != {'/data','/tmp'}
            or value.get('log_driver') != 'none' or value.get('read_only') is not True
            or value.get('privileged') is not False or value.get('user') != '10001:10001'
            or value.get('cap_add') or 'ALL' not in (value.get('cap_drop') or [])
            or not any(item in ('no-new-privileges','no-new-privileges:true') for item in value.get('security_opt') or [])):
        raise MinioRestoreError('container_boundary')


class Docker:
    def __init__(self, endpoint):
        if endpoint != LOCAL_ENDPOINT: raise MinioRestoreError('docker_endpoint')
        self.prefix = [shutil.which('docker') or '/opt/homebrew/bin/docker','--host',endpoint]

    def capture(self, args, *, data=None, timeout=30):
        process = None
        try:
            process = subprocess.Popen(self.prefix+args, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
            output,_ = process.communicate(data,timeout=timeout)
            if process.returncode or len(output)>4*1024*1024: raise MinioRestoreError('docker')
            return output
        except (OSError,subprocess.SubprocessError): raise MinioRestoreError('docker') from None
        finally:
            if process is not None:
                if process.poll() is None: os.killpg(process.pid,signal.SIGKILL)
                process.wait()

    def stream(self, args, source, size):
        process = subprocess.Popen(self.prefix+args,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,start_new_session=True)
        errors = []
        def feed():
            try:
                remaining = size
                while remaining:
                    block = source.read(min(1024*1024,remaining))
                    if not block: raise ValueError()
                    process.stdin.write(block); remaining -= len(block)
            except BaseException: errors.append(True)
            finally:
                try: process.stdin.close()
                except OSError: errors.append(True)
        thread = threading.Thread(target=feed,daemon=True); thread.start()
        try:
            process.wait(timeout=120); thread.join(timeout=5)
            if process.returncode or thread.is_alive() or errors: raise MinioRestoreError('extract')
        except subprocess.SubprocessError: raise MinioRestoreError('extract') from None
        finally:
            if process.poll() is None: os.killpg(process.pid,signal.SIGKILL)
            process.wait(); thread.join(timeout=5)


START = '''set -eu
while [ ! -e /tmp/launch ]; do sleep 1; done
export MINIO_ROOT_USER="$(cat /tmp/root-user)"
export MINIO_ROOT_PASSWORD="$(cat /tmp/root-password)"
exec minio server /data --address 127.0.0.1:9000 --console-address 127.0.0.1:9001 --quiet
'''


class Target:
    def __init__(self, image, endpoint):
        self.docker = Docker(endpoint); self.image = image; self.attempt = uuid.uuid4().hex
        self.name = 'orbbec-minio-restore-'+self.attempt; self.identity = None

    def metadata(self, identity):
        # Selective inspect: no Config.Env and no plaintext filesystem contents.
        fields = {'id':'.Id','image':'.Image','name':'.Name','labels':'.Config.Labels','running':'.State.Running',
                  'exit_code':'.State.ExitCode','status':'.State.Status','network_mode':'.HostConfig.NetworkMode',
                  'ports':'.NetworkSettings.Ports','port_bindings':'.HostConfig.PortBindings','mounts':'.Mounts',
                  'tmpfs':'.HostConfig.Tmpfs','log_driver':'.HostConfig.LogConfig.Type','read_only':'.HostConfig.ReadonlyRootfs',
                  'privileged':'.HostConfig.Privileged','user':'.Config.User','cap_add':'.HostConfig.CapAdd',
                  'cap_drop':'.HostConfig.CapDrop','security_opt':'.HostConfig.SecurityOpt'}
        template = '{'+','.join(json.dumps(key)+':{{json '+value+'}}' for key,value in fields.items())+'}'
        return json.loads(self.docker.capture(['inspect','--format',template,identity]))

    def prepare(self):
        if self.docker.capture(['image','inspect','--format','{{.Id}}',self.image]).decode().strip() != self.image:
            raise MinioRestoreError('image')
        output = self.docker.capture(['create','--pull=never','--restart=no','--name',self.name,
            '--label','io.orbbec.minio-restore.attempt='+self.attempt,'--network','none','--log-driver','none',
            '--read-only','--user','10001:10001','--cap-drop','ALL','--security-opt','no-new-privileges:true',
            '--memory','768m','--pids-limit','128','--tmpfs','/data:rw,nosuid,nodev,size=2g,uid=10001,gid=10001,mode=0700',
            '--tmpfs','/tmp:rw,nosuid,nodev,size=32m,uid=10001,gid=10001,mode=0700',
            '--env','HOME=/tmp','--entrypoint','/bin/sh',self.image,'-ec',START])
        self.identity = output.decode().strip()
        if not CONTAINER_ID.fullmatch(self.identity): raise MinioRestoreError('container_boundary')
        boundary(self.metadata(self.identity),identity=self.identity,image=self.image,name=self.name,attempt=self.attempt)
        self.docker.capture(['start',self.identity])
        if self.metadata(self.identity).get('running') is not True: raise MinioRestoreError('startup')
        self.docker.capture(['exec',self.identity,'/bin/sh','-ec','command -v curl >/dev/null; command -v sha256sum >/dev/null; command -v mkdir >/dev/null'])

    def credentials(self,user,password):
        self.docker.capture(['exec','-i',self.identity,'/bin/sh','-ec',
            'umask 077; IFS= read -r u; IFS= read -r p; printf %s "$u" > /tmp/root-user; printf %s "$p" > /tmp/root-password'],
            data=(user+'\n'+password+'\n').encode())

    def mkdir(self,path): self.docker.capture(['exec',self.identity,'mkdir','-p','--',path])

    def write(self,path,source,size):
        self.docker.stream(['exec','-i',self.identity,'/bin/sh','-ec','umask 077; mkdir -p -- "${1%/*}"; cat > "$1"','restore-file',path],source,size)

    def digest(self,path):
        value = self.docker.capture(['exec',self.identity,'sha256sum','--',path]).decode().split()[0]
        if not re.fullmatch(r'[0-9a-f]{64}',value): raise MinioRestoreError('extract')
        return value

    def launch(self): self.docker.capture(['exec',self.identity,'touch','/tmp/launch'])

    def ready(self):
        deadline = time.monotonic()+30
        while time.monotonic()<deadline:
            if self.metadata(self.identity).get('running') is not True: raise MinioRestoreError('startup')
            try:
                status = self.docker.capture(['exec',self.identity,'curl','-q','--silent','--noproxy','*','--max-time','2','-o','/dev/null','-w','%{http_code}','http://127.0.0.1:9000/minio/health/ready'],timeout=5)
                if status == b'200': return
            except MinioRestoreError as error:
                if error.stage == 'interrupted': raise
            time.sleep(0.2)
        raise MinioRestoreError('startup')

    def authenticate(self,user,password):
        if CurlS3(self,user,password+'-invalid').request('/',[])[0] != 403: raise MinioRestoreError('authentication')
        client = CurlS3(self,user,password)
        try: client.list_buckets()
        except MinioRestoreError as error:
            if error.stage == 'interrupted': raise
            raise MinioRestoreError('authentication') from None
        except Exception: raise MinioRestoreError('authentication') from None
        return client

    def close(self):
        args = ['ps','--all','--no-trunc','--filter','label=io.orbbec.minio-restore.attempt='+self.attempt,'--format','{{.ID}}']
        for identity in self.docker.capture(args).decode().split():
            if not CONTAINER_ID.fullmatch(identity): raise MinioRestoreError('cleanup')
            value = self.metadata(identity)
            if (value.get('id') != identity or value.get('image') != self.image or value.get('name') != '/'+self.name
                    or (value.get('labels') or {}).get('io.orbbec.minio-restore.attempt') != self.attempt
                    or self.identity and identity != self.identity): raise MinioRestoreError('cleanup')
            if value.get('running'):
                try: self.docker.capture(['stop','--time','3',identity],timeout=10)
                except MinioRestoreError: pass
                value = self.metadata(identity)
            if value.get('running') is not False: raise MinioRestoreError('cleanup')
            self.docker.capture(['rm',identity])
        if self.docker.capture(args).strip(): raise MinioRestoreError('cleanup')


class CurlS3:
    def __init__(self,target,user,password):
        self.target,self.user,self.password = target,user,password; self.bucket_cache = None

    def request(self,path,query):
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.credentials import Credentials
        url = 'http://127.0.0.1:9000'+path+('?' + urlencode(sorted(query),quote_via=quote) if query else '')
        request = AWSRequest(method='GET',url=url,headers={'Host':'127.0.0.1:9000','x-amz-content-sha256':hashlib.sha256(b'').hexdigest()})
        SigV4Auth(Credentials(self.user,self.password),'s3','us-east-1').add_auth(request)
        config = 'url = '+json.dumps(url)+'\n'+''.join('header = '+json.dumps(key+': '+value)+'\n' for key,value in request.headers.items())
        raw = self.target.docker.capture(['exec','-i',self.target.identity,'curl','-q','--silent','--noproxy','*','--max-time','5','--dump-header','-','--config','-'],data=config.encode(),timeout=10)
        try:
            header,body = raw.split(b'\r\n\r\n',1); status = int(header.splitlines()[0].split()[1])
            return status,body
        except (ValueError,IndexError): raise MinioRestoreError('inventory') from None

    def xml(self,path,query):
        status,body = self.request(path,query)
        if status != 200: raise MinioRestoreError('inventory')
        try: return ET.fromstring(body)
        except ET.ParseError: raise MinioRestoreError('inventory') from None

    def list_buckets(self,**kwargs):
        if not kwargs and self.bucket_cache is not None: return self.bucket_cache
        root = self.xml('/',[('continuation-token',kwargs['ContinuationToken'])] if kwargs else [])
        result = {'Buckets':[{'Name':item.findtext('{*}Name')} for item in root.findall('{*}Buckets/{*}Bucket')]}
        cursor = root.findtext('{*}ContinuationToken')
        if cursor: result['ContinuationToken'] = cursor
        if not kwargs: self.bucket_cache = result
        return result

    def get_bucket_versioning(self,**kw):
        root = self.xml('/'+quote(kw['Bucket'],safe=''),[('versioning','')])
        return {'Status':root.findtext('{*}Status')}

    def page(self,kind,kw):
        specs = {'objects':([('list-type','2'),('max-keys','1000')],{'ContinuationToken':'continuation-token'},['NextContinuationToken'], [('Contents','Contents')]),
                 'versions':([('versions',''),('max-keys','1000')],{'KeyMarker':'key-marker','VersionIdMarker':'version-id-marker'},['NextKeyMarker','NextVersionIdMarker'],[('Version','Versions'),('DeleteMarker','DeleteMarkers')]),
                 'multipart':([('uploads',''),('max-uploads','1000')],{'KeyMarker':'key-marker','UploadIdMarker':'upload-id-marker'},['NextKeyMarker','NextUploadIdMarker'],[('Upload','Uploads')])}
        query,markers,next_fields,entries = specs[kind]; query += [(value,kw[key]) for key,value in markers.items() if key in kw]
        root = self.xml('/'+quote(kw['Bucket'],safe=''),query)
        truncated = root.findtext('{*}IsTruncated')
        if truncated not in ('true','false'): raise MinioRestoreError('inventory')
        result = {'IsTruncated':truncated == 'true'}
        result.update({field:root.findtext('{*}'+field) for field in next_fields})
        for tag,field in entries:
            values = []
            for entry in root.findall('{*}'+tag):
                value = {'Key':entry.findtext('{*}Key')}
                if kind == 'objects' or tag == 'Version': value.update(Size=int(entry.findtext('{*}Size')),ETag=entry.findtext('{*}ETag'))
                if kind == 'versions':
                    latest = entry.findtext('{*}IsLatest')
                    if latest not in ('true','false'): raise MinioRestoreError('inventory')
                    value.update(VersionId=entry.findtext('{*}VersionId'),IsLatest=latest == 'true')
                if kind == 'multipart': value['UploadId'] = entry.findtext('{*}UploadId')
                values.append(value)
            result[field] = values
        return result

    def list_objects_v2(self,**kw): return self.page('objects',kw)
    def list_object_versions(self,**kw): return self.page('versions',kw)
    def list_multipart_uploads(self,**kw): return self.page('multipart',kw)


def extract_archive(producer,target):
    read_fd,write_fd = os.pipe(); errors = []; records = []
    def feed():
        try:
            with os.fdopen(write_fd,'wb') as output: producer(output)
        except BaseException: errors.append(True)
    thread = threading.Thread(target=feed,daemon=True); thread.start()
    try:
        with os.fdopen(read_fd,'rb') as source:
            with tarfile.open(fileobj=source,mode='r|') as archive:
                for entry in archive:
                    name = str(PurePosixPath(entry.name))
                    if entry.isdir(): target.mkdir('/data/'+name)
                    else:
                        with archive.extractfile(entry) as content: target.write('/data/'+name,content,entry.size)
                        records.append((name,entry.size,target.digest('/data/'+name)))
                while padding := archive.fileobj.read(1024*1024):
                    if any(padding): raise MinioRestoreError('attachments')
    finally: thread.join(timeout=5)
    if errors or thread.is_alive(): raise MinioRestoreError('decrypt')
    digest = hashlib.sha256()
    for record in sorted(records): digest.update(json.dumps(record,separators=(',',':'),ensure_ascii=True).encode()+b'\n')
    return dict(files=len(records),bytes=sum(record[1] for record in records),sha256=digest.hexdigest())


def restore_minio(*,manifest_path,artifacts_dir,private_key_path,endpoint=LOCAL_ENDPOINT,target_factory=None):
    shared = load_shared(); target = None; report = {'status':'failed_minio_restore','source_iam_validated':False,'business_dr_validated':False,'cleanup_confirmed':False}
    handlers = {number:signal.getsignal(number) for number in (signal.SIGTERM,signal.SIGINT)}
    def interrupted(*_): raise MinioRestoreError('interrupted')
    for number in handlers: signal.signal(number,interrupted)
    try:
        manifest = shared._manifest(Path(manifest_path)); report['set_id'] = manifest['set_id']
        image = manifest['runtime']['containers']['platform-minio']['image_id']
        if not isinstance(image,str) or not IMAGE_ID.fullmatch(image): raise MinioRestoreError('manifest')
        key = shared._private_key(Path(private_key_path),manifest['runtime']['recovery_public_key']['sha256'])
        item = manifest['artifacts'][2]; crypto = shared.load_crypto()
        with shared._open_regular(Path(artifacts_dir)/item['filename'],'artifact_integrity') as source:
            version = shared._version(source); digest = hashlib.sha256()
            while block := source.read(1024*1024): digest.update(block)
            if version != shared._version(source) or version[2] != item['file_size'] or digest.hexdigest() != item['ciphertext_sha256']: raise MinioRestoreError('artifact_integrity')
            def producer(output): return shared._decrypt(crypto,source,output,key,item,version)
            archive,metadata = shared._attachments(producer,item['plaintext_size'])
            report.update(archive=archive,ciphertext_sha256=item['ciphertext_sha256'],plaintext_sha256=metadata.plaintext_sha256,image_id=image)
            target = (target_factory or Target)(image,endpoint); target.prepare()
            report.update(attempt_id=target.attempt,target_container_id=target.identity,target_name=target.name)
            user,password = 'restore-'+secrets.token_hex(6),secrets.token_hex(16)
            target.credentials(user,password)
            if extract_archive(producer,target) != archive: raise MinioRestoreError('extract')
            target.launch(); target.ready()
            try: client = target.authenticate(user,password)
            except MinioRestoreError as error:
                if error.stage == 'interrupted': raise
                raise MinioRestoreError('authentication') from None
            except Exception: raise MinioRestoreError('authentication') from None
            report['inventory'] = inventory(client)
            report.update(status='verified_minio_restore',authentication='temporary_local_root_signed_GET_with_invalid_secret_rejected',created_at=datetime.now(timezone.utc).isoformat())
    except BaseException as error:
        report['stage'] = ('interrupted' if isinstance(error,(KeyboardInterrupt,SystemExit)) else error.stage if hasattr(error,'stage') and error.stage in STAGES else 'unexpected')
    finally:
        for number in handlers: signal.signal(number,signal.SIG_IGN)
        if target is not None:
            report.update(attempt_id=target.attempt,target_container_id=target.identity,target_name=target.name)
            try: target.close(); report['cleanup_confirmed'] = True
            except BaseException:
                report.update(status='failed_minio_restore',primary_stage=report.get('stage'),stage='cleanup')
        else: report['cleanup_confirmed'] = True
        for number,handler in handlers.items(): signal.signal(number,handler)
    return report


def production_inventory_program():
    """Execute only inside the existing API container; never returns credential values."""
    return ('import json,os\nclass MinioRestoreError(RuntimeError): pass\n'+inspect.getsource(inventory)+'''
try:
    import boto3
    from botocore.config import Config
    from app.attachments.object_writer import _credential
    client=boto3.client('s3',endpoint_url=os.environ['PLATFORM_ATTACHMENT_S3_ENDPOINT'],region_name='us-east-1',
        aws_access_key_id=_credential(os.environ['PLATFORM_ATTACHMENT_S3_ACCESS_KEY_FILE']),
        aws_secret_access_key=_credential(os.environ['PLATFORM_ATTACHMENT_S3_SECRET_KEY_FILE']),
        config=Config(s3={'addressing_style':'path'},retries={'total_max_attempts':1},connect_timeout=3,read_timeout=5))
    result=inventory(client,required_bucket=os.environ['PLATFORM_ATTACHMENT_S3_BUCKET'])
    print(json.dumps({'status':'readonly_production_s3_inventory','inventory':result},sort_keys=True))
except BaseException:
    print(json.dumps({'status':'failed_readonly_production_s3_inventory','stage':'inventory'}))
    raise SystemExit(1)
''')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True,type=Path,dest='manifest_path')
    parser.add_argument('--artifacts-dir',required=True,type=Path)
    parser.add_argument('--private-key',required=True,type=Path,dest='private_key_path')
    parser.add_argument('--docker-endpoint',default=LOCAL_ENDPOINT,dest='endpoint')
    report = restore_minio(**vars(parser.parse_args(argv)))
    print(json.dumps(report,sort_keys=True,allow_nan=False))
    return 0 if report['status'] == 'verified_minio_restore' else 1


if __name__ == '__main__': raise SystemExit(main())
