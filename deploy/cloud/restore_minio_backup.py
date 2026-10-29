#!/usr/bin/env python3
"""Restore one authenticated MinIO archive to a disposable local tmpfs service."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import fcntl
import importlib.util
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import select
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import threading
import time
import tempfile
from urllib.parse import quote, urlencode
import uuid
import xml.etree.ElementTree as ET

LOCAL_ENDPOINT = 'unix:///Users/neo/.colima/default/docker.sock'
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}\Z')
CONTAINER_ID = re.compile(r'[0-9a-f]{64}\Z')
STAGES = {'manifest','recovery_key','artifact_integrity','decrypt','attachments','docker_endpoint',
          'image','docker','container_boundary','extract','authentication','startup','inventory',
          'cleanup','interrupted','unexpected','vm_identity','vm_control','vm_timeout','vm_client','vm_cleanup'}


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
            or any('noexec' not in options.split(',') or 'exec' in options.split(',') for options in (value.get('tmpfs') or {}).values())
            or value.get('memory') != 768*1024*1024 or value.get('memory_swap') != value.get('memory')
            or value.get('nano_cpus') != 1000000000
            or [item for item in (value.get('ulimits') or []) if item.get('Name') == 'core'] != [{'Name':'core','Soft':0,'Hard':0}]
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


def proc_identity(root,pid,container_id,expected=None):
    """Pin a checked target netns FD; never enter its mount, PID or environment."""
    directory=netfd=None
    try:
        directory=os.open(Path(root)/str(pid),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        def read(name):
            descriptor=os.open(name,os.O_RDONLY,dir_fd=directory)
            with os.fdopen(descriptor,'r') as source:return source.read(65536)
        cgroups=read('cgroup')
        pattern=r'(?:^|/)(?:docker-)?'+re.escape(container_id)+r'(?:\.scope)?(?:/|$)'
        if not any(re.search(pattern,line.split(':',2)[-1]) for line in cgroups.splitlines()):raise ValueError()
        fields={line.split(':',1)[0]:line.split(':',1)[1].split() for line in read('status').splitlines() if ':' in line}
        if fields.get('Uid')!=['10001']*4 or len(fields.get('NSpid',[]))<2 or fields['NSpid'][-1]!='1':raise ValueError()
        process_stat=read('stat').rsplit(') ',1)[1].split()
        if process_stat[0] in ('Z','X'):raise ValueError()
        netfd=os.open('ns/net',os.O_RDONLY,dir_fd=directory)
        inode=os.fstat(netfd).st_ino
        if inode==os.stat(Path(root)/'self/ns/net').st_ino:raise ValueError()
        value={'pid':pid,'container_id':container_id,'uid':10001,'nspid':[int(item) for item in fields['NSpid']],
               'starttime':process_stat[19],'net_inode':inode}
        if expected is not None and value!=expected:raise ValueError()
        return value,netfd
    except (OSError,ValueError,KeyError,IndexError):
        if netfd is not None:os.close(netfd)
        raise MinioRestoreError('vm_identity') from None
    finally:
        if directory is not None:os.close(directory)


def bounded_client(command,data,register,timeout,pass_fds=(),pidfd_required=False):
    """Register a gated client before input; reap its session on every exit."""
    process=None;output=b'';stage=None;pidfd=None
    try:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                                 start_new_session=True,pass_fds=pass_fds)
        register(process.pid)
        if pidfd_required:pidfd=os.pidfd_open(process.pid)
        output,_=process.communicate(data,timeout=timeout)
        if process.returncode or len(output)>4*1024*1024:stage='vm_client'
    except subprocess.TimeoutExpired:stage='vm_timeout'
    except BaseException as error:
        stage='interrupted' if isinstance(error,(KeyboardInterrupt,SystemExit)) else getattr(error,'stage','vm_control')
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    if pidfd_required:
                        if pidfd is None:
                            process.stdin.close()  # Gate EOF; no request was released.
                            try:process.wait(timeout=2)
                            except subprocess.TimeoutExpired:raise MinioRestoreError('vm_cleanup') from None
                        else:signal.pidfd_send_signal(pidfd,signal.SIGKILL)
                    else:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            process.wait()
        if pidfd is not None:os.close(pidfd)
    return process.returncode if process is not None else None,output,stage


def vm_record(path,value):
    descriptor,name=tempfile.mkstemp(dir=path.parent,prefix='.request-')
    try:
        os.fchmod(descriptor,0o600)
        with os.fdopen(descriptor,'w') as output:json.dump(value,output);output.flush();os.fsync(output.fileno())
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)


def vm_load(path,scope):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(descriptor,'r') as source:
        item=os.fstat(source.fileno())
        if not stat.S_ISREG(item.st_mode) or item.st_uid!=0 or stat.S_IMODE(item.st_mode)!=0o600:raise MinioRestoreError('vm_cleanup')
        value=json.load(source)
    if any(value.get(key)!=scope.get(key) for key in ('attempt','request','container_id')):raise MinioRestoreError('vm_cleanup')
    return value


@contextmanager
def vm_lock(path):
    # Stable inode survives every record replacement and cancellation forever.
    descriptor=os.open(path.with_suffix('.lock'),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        item=os.fstat(descriptor)
        if not stat.S_ISREG(item.st_mode) or item.st_uid!=os.geteuid() or stat.S_IMODE(item.st_mode)!=0o600:raise MinioRestoreError('vm_control')
        fcntl.flock(descriptor,fcntl.LOCK_EX)
        yield
    finally:os.close(descriptor)


def vm_claim(path,scope,controller):
    with vm_lock(path):
        record=vm_load(path,scope)
        if record.get('state')!='armed':raise MinioRestoreError('vm_control')
        record.update(state='starting',**controller);vm_record(path,record)
        return record


def vm_update(path,scope,changes,expected=None):
    with vm_lock(path):
        record=vm_load(path,scope)
        if record.get('state')=='cancelled':
            if expected is not None:raise MinioRestoreError('vm_control')
            return record
        if expected is not None and record.get('state') not in expected:raise MinioRestoreError('vm_control')
        record.update(changes);vm_record(path,record)
        return record


def vm_alive(pid,starttime,token):
    try:
        directory=Path('/proc')/str(pid)
        fields=(directory/'stat').read_text().rsplit(') ',1)[1].split()
        if fields[19]!=starttime or fields[0] in ('Z','X'):return False
        if token.encode() not in (directory/'cmdline').read_bytes():raise MinioRestoreError('vm_cleanup')
        return True
    except FileNotFoundError:return False


def vm_stop_verified(pid,starttime,token,number):
    """Open first, recheck identity, signal only the stable kernel handle."""
    if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):raise MinioRestoreError('vm_cleanup')
    try:descriptor=os.pidfd_open(pid)
    except ProcessLookupError:return
    try:
        if not vm_alive(pid,starttime,token):return
        try:signal.pidfd_send_signal(descriptor,number)
        except ProcessLookupError:return
        poller=select.poll();poller.register(descriptor,select.POLLIN)
        if not any(flags & select.POLLIN for _,flags in poller.poll(2000)):raise MinioRestoreError('vm_cleanup')
    finally:os.close(descriptor)


def vm_cleanup(path,scope):
    with vm_lock(path):
        if not path.exists():
            vm_record(path,dict(scope,state='cancelled',cancel_confirmed=False))
            if scope.get('run_invoked'):raise MinioRestoreError('vm_cleanup')
        record=vm_load(path,scope)
        record.update(state='cancelled',cancel_confirmed=False);vm_record(path,record)
    for role in ('client','controller'):
        pid=record.get(role+'_pid');start=record.get(role+'_starttime')
        if pid:vm_stop_verified(pid,start,scope['request'],signal.SIGKILL if role=='client' else signal.SIGTERM)
    with vm_lock(path):
        latest=vm_load(path,scope)
        if latest.get('state')!='cancelled':raise MinioRestoreError('vm_cleanup')
        latest.update(cancel_confirmed=True,client_stopped=True);vm_record(path,latest)


VM_MAIN=r'''
import resource
os.umask(0o077);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
mode=sys.argv[1];payload=json.loads(sys.argv[2]);netfd=None;record=None;path=None
def terminated(*_):raise KeyboardInterrupt()
for number in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(number,terminated)
def request_path(scope):
    if (not re.fullmatch('[0-9a-f]{32}',scope.get('attempt','')) or not re.fullmatch('[0-9a-f]{32}',scope.get('request',''))
        or not re.fullmatch('[0-9a-f]{64}',scope.get('container_id',''))):raise MinioRestoreError('vm_control')
    directory=Path('/run/orbbec-minio-http')/scope['attempt']
    for parent in (directory.parent,directory):
        parent.mkdir(mode=0o700,exist_ok=True);item=parent.lstat()
        if not stat.S_ISDIR(item.st_mode) or item.st_uid!=0 or stat.S_IMODE(item.st_mode)!=0o700:raise MinioRestoreError('vm_control')
    return directory/(scope['request']+'.json')
try:
    if mode=='cleanup':
        for scope in payload['requests']:vm_cleanup(request_path(scope),scope)
        print(json.dumps({'ok':True,'client_stopped':True}));raise SystemExit(0)
    scope=payload;path=request_path(scope)
    if mode=='arm':
        with vm_lock(path):
            if path.exists():raise MinioRestoreError('vm_control')
            vm_record(path,dict(scope,state='armed'))
        print(json.dumps({'ok':True}));raise SystemExit(0)
    if mode!='run':raise MinioRestoreError('vm_control')
    if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):raise MinioRestoreError('vm_control')
    record=vm_claim(path,scope,{'controller_pid':os.getpid(),'controller_starttime':Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()[19]})
    process,netfd=proc_identity(Path('/proc'),scope['pid'],scope['container_id'],scope.get('expected_process'))
    # Gated child cannot make a request before its exact PID/starttime is saved.
    gate="import os,sys;token=os.read(0,1);sys.exit(125) if token!=bytes([0]) else os.execvpe(sys.argv[1],sys.argv[1:],{'PATH':'/usr/bin:/bin','HOME':'/nonexistent','LC_ALL':'C'})"
    arguments=['/usr/bin/nsenter','--net=/proc/self/fd/'+str(netfd),'/usr/bin/curl','-q','--silent','--noproxy','*',
               '--max-time','2' if scope['health'] else '5','--user-agent','orbbec-minio-http-'+scope['request']]
    arguments+=['-o','/dev/null','-w','%{http_code}'] if scope['health'] else ['--dump-header','-']
    arguments+=['--config','-']
    def register(pid):
        vm_update(path,scope,{'state':'running','client_pid':pid,'client_starttime':Path('/proc/'+str(pid)+'/stat').read_text().rsplit(') ',1)[1].split()[19]},expected={'starting'})
    code,body,stage=bounded_client(['/usr/bin/python3','-c',gate,*arguments],bytes([0])+sys.stdin.buffer.read(65537),register,7,(netfd,),True)
    vm_update(path,scope,{'state':'completed','client_stopped':True,'returncode':code,'stage':stage})
    print(json.dumps({'ok':True,'client_stopped':True,'returncode':code,'stage':stage,'process':process,'response':base64.b64encode(body).decode()}))
except SystemExit:raise
except BaseException as error:
    if record is not None and path is not None:
        vm_update(path,scope,{'state':'failed'})
    print(json.dumps({'ok':False,'stage':'interrupted' if isinstance(error,KeyboardInterrupt) else getattr(error,'stage','vm_control')}))
finally:
    if netfd is not None:os.close(netfd)
'''


def vm_program():
    imports='import base64,fcntl,json,os,re,select,signal,stat,subprocess,sys,tempfile,time\nfrom pathlib import Path\nfrom contextlib import contextmanager\n'
    return imports+'STAGES='+repr(STAGES)+'\n'+''.join(inspect.getsource(value)+'\n' for value in
        (MinioRestoreError,proc_identity,bounded_client,vm_record,vm_load,vm_lock,vm_claim,vm_update,vm_alive,vm_stop_verified,vm_cleanup))+VM_MAIN


class NativeVMBridge:
    """One bounded native read-only HTTP client per registered request."""
    def __init__(self,target):
        self.target=target;self.requests=[];self.receipts=[];self.process=None;self.host=None;self.closed=False;self.confirmed=False

    def _call(self,mode,payload,data=None):
        command=[shutil.which('colima') or '/opt/homebrew/bin/colima','--profile','default','ssh','--',
                 'sudo','-n','/usr/bin/python3','-c',vm_program(),mode,json.dumps(payload,separators=(',',':'))]
        code,output,stage=bounded_client(command,data or b'',lambda _:None,12)
        if stage:raise MinioRestoreError(stage)
        try:
            value=json.loads(output)
            if not isinstance(value,dict):raise ValueError()
            return value
        except (ValueError,TypeError):raise MinioRestoreError('vm_control') from None

    def _identity(self):
        value=self.target.metadata(self.target.identity)
        boundary(value,identity=self.target.identity,image=self.target.image,name=self.target.name,attempt=self.target.attempt)
        identity={key:value.get(key) for key in ('id','image','pid','started_at')}
        if (value.get('running') is not True or type(identity['pid']) is not int or identity['pid']<=0
                or not isinstance(identity['started_at'],str) or not identity['started_at']
                or self.host is not None and identity!=self.host):raise MinioRestoreError('vm_identity')
        return identity

    def capture(self,config,*,health=False):
        if self.closed or not isinstance(config,bytes) or len(config)>65536:raise MinioRestoreError('vm_control')
        self.host=self._identity()
        scope={'attempt':self.target.attempt,'request':uuid.uuid4().hex,'container_id':self.target.identity,
               'pid':self.host['pid'],'health':health,'expected_process':self.process,'run_invoked':False}
        self.requests.append(scope)
        if self._call('arm',scope).get('ok') is not True:raise MinioRestoreError('vm_control')
        scope['run_invoked']=True
        value=self._call('run',scope,config)
        if value.get('ok') is not True:raise MinioRestoreError(value.get('stage','vm_control'))
        if value.get('client_stopped') is not True:raise MinioRestoreError('vm_cleanup')
        observed=value.get('process') or {}
        if (observed.get('pid')!=scope['pid'] or observed.get('container_id')!=scope['container_id']
                or observed.get('uid')!=10001 or not isinstance(observed.get('starttime'),str)
                or type(observed.get('net_inode')) is not int or self.process is not None and observed!=self.process):raise MinioRestoreError('vm_identity')
        self.process=observed;self._identity()
        self.receipts.append({'request_id':scope['request'],'health':health,'returncode':value.get('returncode'),
                              'stage':value.get('stage'),'client_stopped':True})
        if health and value.get('returncode')==7 and value.get('stage')=='vm_client':return b'000'
        if value.get('stage') or value.get('returncode')!=0:raise MinioRestoreError(value.get('stage') or 'vm_client')
        try:return base64.b64decode(value['response'],validate=True)
        except (KeyError,ValueError):raise MinioRestoreError('vm_control') from None

    def close(self):
        if self.closed:
            if not self.confirmed:raise MinioRestoreError('vm_cleanup')
            return
        self.closed=True
        try:
            if self.requests:
                value=self._call('cleanup',{'requests':self.requests})
                if value.get('ok') is not True or value.get('client_stopped') is not True:raise ValueError()
            self.confirmed=True
        except BaseException:raise MinioRestoreError('vm_cleanup') from None

    def summary(self):
        return {'status':'confirmed' if self.confirmed else 'incomplete','process':self.process,'requests':self.receipts,
                'armed_request_ids':[value['request'] for value in self.requests]}


START = '''set -eu
while [ ! -e /tmp/launch ]; do sleep 1; done
export MINIO_ROOT_USER="$(cat /tmp/root-user)"
export MINIO_ROOT_PASSWORD="$(cat /tmp/root-password)"
exec minio server /data --address 127.0.0.1:9000 --console-address 127.0.0.1:9001 --quiet
'''


class Target:
    def __init__(self, image, endpoint):
        self.docker = Docker(endpoint); self.image = image; self.attempt = uuid.uuid4().hex
        self.name = 'orbbec-minio-restore-'+self.attempt; self.identity = None; self.bridge=None

    def metadata(self, identity):
        # Selective inspect: no Config.Env and no plaintext filesystem contents.
        fields = {'id':'.Id','image':'.Image','name':'.Name','labels':'.Config.Labels','running':'.State.Running',
                  'pid':'.State.Pid','started_at':'.State.StartedAt',
                  'exit_code':'.State.ExitCode','status':'.State.Status','network_mode':'.HostConfig.NetworkMode',
                  'ports':'.NetworkSettings.Ports','port_bindings':'.HostConfig.PortBindings','mounts':'.Mounts',
                  'tmpfs':'.HostConfig.Tmpfs','log_driver':'.HostConfig.LogConfig.Type','read_only':'.HostConfig.ReadonlyRootfs',
                  'memory':'.HostConfig.Memory','memory_swap':'.HostConfig.MemorySwap','nano_cpus':'.HostConfig.NanoCpus','ulimits':'.HostConfig.Ulimits',
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
            '--memory','768m','--memory-swap','768m','--cpus','1','--ulimit','core=0','--pids-limit','128',
            '--tmpfs','/data:rw,noexec,nosuid,nodev,size=2g,uid=10001,gid=10001,mode=0700',
            '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=32m,uid=10001,gid=10001,mode=0700',
            '--env','HOME=/tmp','--entrypoint','/bin/sh',self.image,'-ec',START])
        self.identity = output.decode().strip()
        if not CONTAINER_ID.fullmatch(self.identity): raise MinioRestoreError('container_boundary')
        boundary(self.metadata(self.identity),identity=self.identity,image=self.image,name=self.name,attempt=self.attempt)
        self.docker.capture(['start',self.identity])
        if self.metadata(self.identity).get('running') is not True: raise MinioRestoreError('startup')
        self.docker.capture(['exec',self.identity,'/bin/sh','-ec','command -v sha256sum >/dev/null; command -v mkdir >/dev/null'])
        self.bridge=NativeVMBridge(self)

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
            status=self.bridge.capture(b'url = "http://127.0.0.1:9000/minio/health/ready"\n',health=True)
            if status==b'200':return
            time.sleep(0.2)
        raise MinioRestoreError('startup')

    def authenticate(self,user,password):
        if CurlS3(self,user,password+'-invalid').request('/',[])[0] != 403: raise MinioRestoreError('authentication')
        client = CurlS3(self,user,password)
        try: client.list_buckets()
        except MinioRestoreError as error:
            if error.stage == 'interrupted' or error.stage.startswith('vm_'): raise
            raise MinioRestoreError('authentication') from None
        except Exception: raise MinioRestoreError('authentication') from None
        return client

    def close(self):
        if self.bridge is not None:self.bridge.close()
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
        raw = self.target.bridge.capture(config.encode())
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
                if error.stage == 'interrupted' or error.stage.startswith('vm_'): raise
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
            if getattr(target,'bridge',None) is not None:report['http_bridge']=target.bridge.summary()
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
