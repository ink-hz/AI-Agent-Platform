"""Local synthetic AEAD/tar/S3/isolation contracts; real execution is separate."""
from copy import deepcopy
import base64
from datetime import UTC, datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

ROOT = Path(__file__).parents[2]
HELPER = ROOT / 'deploy/cloud/restore_minio_backup.py'
SET_ID = '20261029T032929866671Z-4b220d42'
IMAGE = 'sha256:' + 'b' * 64


def module():
    assert HELPER.is_file(), 'MinIO offline restore helper is missing'
    spec = importlib.util.spec_from_file_location('minio_restore', HELPER)
    value = importlib.util.module_from_spec(spec); sys.modules[spec.name] = value; spec.loader.exec_module(value)
    return value


def fixture_set(tmp_path, *, unsafe=False):
    mod = module(); shared = mod.load_shared(); crypto = shared.load_crypto()
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key = tmp_path / 'key'; key.write_bytes(private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())); key.chmod(0o600)
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode='w') as archive:
        directory = tarfile.TarInfo('./bucket'); directory.type = tarfile.DIRTYPE; archive.addfile(directory)
        item = tarfile.TarInfo('../escape' if unsafe else './bucket/synthetic-key'); content = b'synthetic body'
        item.size = len(content); archive.addfile(item, io.BytesIO(content))
    artifacts = []
    for kind, scope, plaintext in [('replica','database:agent_platform',b'dump'), ('control','database:agent_platform_control',b'dump'), ('attachments','minio_data_directory',tar.getvalue())]:
        output = io.BytesIO(); receipt = crypto.encrypt_stream(io.BytesIO(plaintext), output, public)
        data = output.getvalue(); name = f'{kind}-{SET_ID}.orb'; (tmp_path / name).write_bytes(data)
        artifacts.append(dict(filename=name, scope=scope, file_size=len(data), ciphertext_sha256=hashlib.sha256(data).hexdigest(), encrypted_size=receipt.encrypted_size, plaintext_size=len(plaintext), producer_returncode=0, encryptor_returncode=0))
    manifest = {'version':1,'status':'complete','set_id':SET_ID,'snapshot_consistency':'individual_captures_not_cross_resource_atomic',
                'runtime':{'recovery_public_key':{'sha256':hashlib.sha256(public).hexdigest()},'containers':{'platform-minio':{'image_id':IMAGE}}},'artifacts':artifacts}
    path = tmp_path / 'manifest.json'; path.write_text(json.dumps(manifest))
    return mod, dict(manifest_path=path, artifacts_dir=tmp_path, private_key_path=key), manifest


class Client:
    def __init__(self): self.calls = []
    def list_buckets(self): return {'Buckets':[{'Name':'bucket'}]}
    def get_bucket_versioning(self, **kw): return {'Status':'Enabled'}
    def list_objects_v2(self, **kw):
        self.calls.append(('objects',kw))
        return ({'IsTruncated':True,'NextContinuationToken':'cursor','Contents':[{'Key':'private-key-sentinel','Size':14,'ETag':'e'}]}
                if 'ContinuationToken' not in kw else {'IsTruncated':False})
    def list_object_versions(self, **kw):
        self.calls.append(('versions',kw))
        return ({'IsTruncated':True,'NextKeyMarker':'k','NextVersionIdMarker':'v','Versions':[{'Key':'private-key-sentinel','VersionId':'v1','Size':14,'ETag':'e','IsLatest':True}]}
                if 'KeyMarker' not in kw else {'IsTruncated':False,'DeleteMarkers':[{'Key':'deleted-key-sentinel','VersionId':'v2','IsLatest':True}]})
    def list_multipart_uploads(self, **kw):
        self.calls.append(('multipart',kw))
        return ({'IsTruncated':True,'NextKeyMarker':'m','NextUploadIdMarker':'u','Uploads':[{'Key':'pending-key-sentinel','UploadId':'u1'}]}
                if 'KeyMarker' not in kw else {'IsTruncated':False})


class Target:
    def __init__(self):
        self.identity = 'a' * 64; self.attempt = 'c' * 32; self.name = 'synthetic-local-restore'
        self.files = {}; self.calls = []; self.fail_auth = False; self.fail_cleanup = False
    def prepare(self): self.calls.append('prepare')
    def credentials(self, user, password): self.calls.append('credentials')
    def mkdir(self, path): self.calls.append('mkdir')
    def write(self, path, source, size):
        self.files[path] = source.read(); assert len(self.files[path]) == size
    def digest(self, path): return hashlib.sha256(self.files[path]).hexdigest()
    def launch(self): self.calls.append('launch')
    def ready(self): self.calls.append('ready')
    def authenticate(self, user, password):
        self.calls.append('authenticate')
        if self.fail_auth: raise RuntimeError('secret-sentinel-auth-failure')
        return Client()
    def close(self):
        self.calls.append('cleanup')
        if self.fail_cleanup: raise RuntimeError('secret-sentinel-cleanup-failure')


def test_genuine_aead_safe_tar_real_consumer_order_and_explicit_scope(tmp_path):
    mod, args, _ = fixture_set(tmp_path); target = Target()
    report = mod.restore_minio(**args, target_factory=lambda *_:target)
    assert report['status'] == 'verified_minio_restore', report
    assert report['archive']['files'] == 1 and report['archive']['bytes'] == 14
    assert target.files['/data/bucket/synthetic-key'] == b'synthetic body'
    assert target.calls.index('credentials') < target.calls.index('launch') < target.calls.index('authenticate') < target.calls.index('cleanup')
    assert report['cleanup_confirmed'] is True
    assert report['source_iam_validated'] is False and report['business_dr_validated'] is False
    assert 'synthetic body' not in json.dumps(report) and 'private-key-sentinel' not in json.dumps(report)


@pytest.mark.parametrize('change,stage', [('cipher','artifact_integrity'),('keymode','recovery_key'),('unsafe_tar','attachments')])
def test_invalid_inputs_never_create_target(tmp_path, change, stage):
    mod, args, manifest = fixture_set(tmp_path, unsafe=change == 'unsafe_tar')
    if change == 'cipher':
        path = tmp_path / manifest['artifacts'][2]['filename']; path.write_bytes(path.read_bytes()[:-1] + b'X')
    elif change == 'keymode': args['private_key_path'].chmod(0o644)
    made = []
    report = mod.restore_minio(**args, target_factory=lambda *_:made.append(True))
    assert report['status'] == 'failed_minio_restore' and report['stage'] == stage
    assert made == []


@pytest.mark.parametrize('failure', ['authentication','cleanup','signal'])
def test_failure_and_signal_remove_only_own_target_and_never_expose_diagnostics(tmp_path, failure):
    mod, args, _ = fixture_set(tmp_path); target = Target()
    if failure == 'authentication': target.fail_auth = True
    elif failure == 'cleanup': target.fail_auth = target.fail_cleanup = True
    else:
        def interrupted(): raise KeyboardInterrupt()
        target.prepare = interrupted
    report = mod.restore_minio(**args, target_factory=lambda *_:target)
    assert report['status'] == 'failed_minio_restore'
    assert target.calls[-1] == 'cleanup'
    assert report['cleanup_confirmed'] is (failure != 'cleanup')
    assert report['stage'] == {'authentication':'authentication','cleanup':'cleanup','signal':'interrupted'}[failure]
    assert 'secret-sentinel' not in json.dumps(report)


def test_inventory_pages_current_versions_deleted_and_multipart_without_disclosing_keys():
    mod = module(); client = Client(); report = mod.inventory(client, required_bucket='bucket')
    item = report['buckets'][0]
    assert {key:item[key] for key in ('objects','versions','delete_markers','multipart_uploads','object_bytes')} == dict(objects=1, versions=1, delete_markers=1, multipart_uploads=1, object_bytes=14)
    assert [kind for kind,_ in client.calls] == ['objects','objects','versions','versions','multipart','multipart']
    assert report['scope'] == 'all_visible_buckets_including_versions_delete_markers_multipart'
    assert 'key-sentinel' not in json.dumps(report) and len(item['sha256']) == 64


def test_inventory_denial_or_repeated_cursor_is_failure_without_iam_changes():
    mod = module(); client = Client()
    client.list_multipart_uploads = lambda **_: {'IsTruncated':True,'NextKeyMarker':'same','NextUploadIdMarker':'same'}
    with pytest.raises(mod.MinioRestoreError, match='inventory'):
        mod.inventory(client)
    client = Client()
    def denied(**_): raise RuntimeError('credential-sentinel')
    client.list_object_versions = denied
    with pytest.raises(mod.MinioRestoreError, match='inventory'):
        mod.inventory(client)


def boundary_value():
    return {'id':'a'*64,'image':IMAGE,'name':'/restore-test','labels':{'io.orbbec.minio-restore.attempt':'c'*32},
            'network_mode':'none','ports':{},'port_bindings':{},'mounts':[], 'tmpfs':{'/data':'rw,noexec,uid=10001,gid=10001','/tmp':'rw,noexec,uid=10001,gid=10001'},
            'memory':768*1024*1024,'memory_swap':768*1024*1024,'nano_cpus':1000000000,'ulimits':[{'Name':'core','Soft':0,'Hard':0}],
            'log_driver':'none','read_only':True,'privileged':False,'user':'10001:10001','cap_add':[], 'cap_drop':['ALL'],'security_opt':['no-new-privileges:true']}


@pytest.mark.parametrize('change', ['network','ports','bind','root','log','cap','write_root','image','label'])
def test_container_boundary_rejects_host_network_storage_root_logging_or_foreign_identity(change):
    mod = module(); value = boundary_value()
    if change == 'network': value['network_mode'] = 'bridge'
    elif change == 'ports': value['port_bindings'] = {'9000/tcp':[{'HostPort':'9000'}]}
    elif change == 'bind': value['mounts'] = [{'Type':'bind','Source':'/private'}]
    elif change == 'root': value['user'] = '0'
    elif change == 'log': value['log_driver'] = 'json-file'
    elif change == 'cap': value['cap_add'] = ['SYS_ADMIN']
    elif change == 'write_root': value['read_only'] = False
    elif change == 'image': value['image'] = 'sha256:'+'f'*64
    else: value['labels'] = {}
    with pytest.raises(mod.MinioRestoreError, match='container_boundary'):
        mod.boundary(value, identity='a'*64, image=IMAGE, name='restore-test', attempt='c'*32)


@pytest.mark.parametrize('change', ['swap','core','cpu','data_exec','tmp_exec'])
def test_actual_boundary_rejects_swap_core_unlimited_cpu_or_executable_tmpfs(change):
    mod = module(); value = boundary_value()
    if change == 'swap': value['memory_swap'] = -1
    elif change == 'core': value['ulimits'] = [{'Name':'core','Soft':-1,'Hard':-1}]
    elif change == 'cpu': value['nano_cpus'] = 0
    elif change == 'data_exec': value['tmpfs']['/data'] = 'rw,uid=10001,gid=10001'
    else: value['tmpfs']['/tmp'] = 'rw,uid=10001,gid=10001'
    with pytest.raises(mod.MinioRestoreError,match='container_boundary'):
        mod.boundary(value,identity='a'*64,image=IMAGE,name='restore-test',attempt='c'*32)


def test_explicit_colima_endpoint_and_no_default_or_remote_docker():
    mod = module()
    with pytest.raises(mod.MinioRestoreError, match='docker_endpoint'):
        mod.Docker('tcp://production:2376')
    assert mod.Docker(mod.LOCAL_ENDPOINT).prefix[-2:] == ['--host',mod.LOCAL_ENDPOINT]


@pytest.mark.parametrize('phase', ['authentication','inventory'])
def test_real_sigterm_inside_s3_calls_preserves_interruption_and_cleans_target(tmp_path, phase):
    mod,args,_ = fixture_set(tmp_path); target = Target()
    if phase == 'authentication':
        def authenticate(*_): os.kill(os.getpid(),signal.SIGTERM)
        target.authenticate = authenticate
    else:
        client = Client()
        def interrupted(**_): os.kill(os.getpid(),signal.SIGTERM)
        client.list_multipart_uploads = interrupted
        target.authenticate = lambda *_:client
    report = mod.restore_minio(**args,target_factory=lambda *_:target)
    assert report['stage'] == 'interrupted' and report['cleanup_confirmed'] is True
    assert target.calls[-1] == 'cleanup'


def test_curl_xml_bucket_pagination_and_signature_only_on_stdin():
    mod = module(); calls = []
    class Runner:
        def capture(self,config,**kwargs):
            calls.append((config,kwargs))
            xml = ('<ListAllMyBucketsResult><Buckets><Bucket><Name>bucket</Name></Bucket></Buckets>'
                   '<ContinuationToken>next-bucket-page</ContinuationToken></ListAllMyBucketsResult>')
            return b'HTTP/1.1 200 OK\r\nContent-Type: application/xml\r\n\r\n'+xml.encode()
    class Local:
        identity = 'a'*64; bridge = Runner()
    client = mod.CurlS3(Local(),'temporary-user-sentinel','temporary-password-sentinel')
    page = client.list_buckets()
    assert page['ContinuationToken'] == 'next-bucket-page'
    config,settings = calls[0]
    assert b'Authorization: AWS4-HMAC-SHA256 ' in config
    assert b'temporary-password-sentinel' not in config
    client.list_buckets(ContinuationToken='next-bucket-page')
    assert len(calls) == 2
    assert b'continuation-token=next-bucket-page' in calls[-1][0]


def fake_proc(tmp_path):
    root = tmp_path / 'proc'; process = root / '123'; (process / 'ns').mkdir(parents=True)
    (root / 'self/ns').mkdir(parents=True)
    (root / 'self/ns/net').write_text('host-net')
    (process / 'ns/net').write_text('target-net')
    (process / 'cgroup').write_text('0::/system.slice/docker-'+'a'*64+'.scope\n')
    (process / 'status').write_text('Uid:\t10001\t10001\t10001\t10001\nNSpid:\t123\t1\n')
    (process / 'stat').write_text('123 (minio worker) S '+' '.join(['0']*18+['777']+['0']*8))
    return root,process


@pytest.mark.parametrize('change', ['cgroup','uid','nspid','starttime','netns'])
def test_proc_gate_rejects_wrong_identity_or_pid_reuse_before_namespace_entry(tmp_path,change):
    mod=module();root,process=fake_proc(tmp_path)
    original,fd=mod.proc_identity(root,123,'a'*64);os.close(fd)
    if change=='cgroup': (process/'cgroup').write_text('0::/docker/'+'b'*64+'\n')
    elif change=='uid': (process/'status').write_text('Uid:\t0\t0\t0\t0\nNSpid:\t123\t1\n')
    elif change=='nspid': (process/'status').write_text('Uid:\t10001\t10001\t10001\t10001\nNSpid:\t123\t2\n')
    elif change=='starttime': (process/'stat').write_text((process/'stat').read_text().replace('777','888'))
    else:
        replacement=process/'ns/new';replacement.write_text('different-net');os.replace(replacement,process/'ns/net')
    with pytest.raises(mod.MinioRestoreError,match='vm_identity'):
        mod.proc_identity(root,123,'a'*64,original)


def test_namespace_fd_remains_bound_when_proc_path_is_replaced(tmp_path):
    mod=module();root,process=fake_proc(tmp_path)
    identity,fd=mod.proc_identity(root,123,'a'*64)
    try:
        (process/'ns/net').unlink();(process/'ns/net').write_text('replacement')
        observed=subprocess.check_output([sys.executable,'-c','import os,sys;print(os.fstat(int(sys.argv[1])).st_ino)',str(fd)],pass_fds=(fd,),text=True)
        assert int(observed)==identity['net_inode']
    finally:os.close(fd)


def test_native_client_timeout_kills_and_reaps_real_child_before_return(tmp_path):
    mod=module();registered=[]
    command=[sys.executable,'-c','import os,time;os.read(0,1);time.sleep(30)']
    code,output,stage=mod.bounded_client(command,b'\0synthetic-config',lambda pid:registered.append(pid),0.1)
    assert stage=='vm_timeout' and code!=0 and registered
    with pytest.raises(ProcessLookupError):os.kill(registered[0],0)


def test_native_client_real_sigterm_kills_and_reaps_child(tmp_path):
    mod=module();marker=tmp_path/'client.pid';harness=tmp_path/'harness.py'
    harness.write_text("import importlib.util,sys,os,pathlib,signal\ns=importlib.util.spec_from_file_location('bridge',sys.argv[1]);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)\ndef stop(*_):raise KeyboardInterrupt()\nsignal.signal(signal.SIGTERM,stop)\ndef register(pid):pathlib.Path(sys.argv[2]).write_text(str(pid))\ncode,output,stage=m.bounded_client([sys.executable,'-c','import os,time;os.read(0,1);time.sleep(30)'],b'\\0config',register,30)\nprint(stage,flush=True)\n")
    process=subprocess.Popen([sys.executable,str(harness),str(HELPER),str(marker)],stdout=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+5
        while not marker.exists() and time.monotonic()<deadline:time.sleep(0.01)
        pid=int(marker.read_text());process.send_signal(signal.SIGTERM)
        assert process.communicate(timeout=5)[0].strip()=='interrupted'
        with pytest.raises(ProcessLookupError):os.kill(pid,0)
    finally:
        if process.poll() is None:process.kill();process.wait()


def test_bridge_fixed_stage_no_vendor_fallback_and_single_cleanup(tmp_path):
    mod=module();calls=[]
    class Local:
        identity='a'*64;image=IMAGE;attempt='c'*32;name='restore-test'
        def metadata(self,_):return dict(boundary_value(),running=True,pid=123,started_at='stable-start')
    bridge=mod.NativeVMBridge(Local())
    def control(mode,payload,data=None):
        calls.append((mode,payload,data))
        if mode=='arm':return {'ok':True}
        if mode=='run':return {'ok':True,'client_stopped':True,'returncode':139,'stage':'vm_client','response':'','process':{'pid':123,'starttime':'777','net_inode':1,'container_id':'a'*64,'uid':10001,'nspid':[123,1]}}
        return {'ok':True,'client_stopped':True}
    bridge._call=control
    with pytest.raises(mod.MinioRestoreError,match='vm_client'):bridge.capture(b'config-secret-sentinel')
    bridge.close()
    assert [mode for mode,_,_ in calls]==['arm','run','cleanup']
    assert calls[1][2]==b'config-secret-sentinel'
    assert 'config-secret-sentinel' not in json.dumps([payload for _,payload,_ in calls])


def test_unknown_vm_cleanup_prevents_target_cleanup():
    mod=module();target=mod.Target.__new__(mod.Target)
    class Bridge:
        def close(self):raise mod.MinioRestoreError('vm_cleanup')
    target.bridge=Bridge()
    class Docker:
        def capture(self,*_,**__):raise AssertionError('target cleanup ran before client exit confirmation')
    target.docker=Docker()
    with pytest.raises(mod.MinioRestoreError,match='vm_cleanup'):target.close()


def test_client_registration_failure_never_releases_stdin_gate(tmp_path):
    mod=module();marker=tmp_path/'request-started';registered=[]
    command=[sys.executable,'-c','import os,pathlib,sys,time;gate=os.read(0,1);pathlib.Path(sys.argv[1]).write_bytes(gate);time.sleep(30)',str(marker)]
    def unavailable(pid):registered.append(pid);raise OSError('synthetic metadata write failure')
    code,_,stage=mod.bounded_client(command,b'\0private-config',unavailable,1)
    assert stage=='vm_control' and code!=0 and not marker.exists()
    with pytest.raises(ProcessLookupError):os.kill(registered[0],0)


def test_vm_program_compiles_and_host_cli_uses_explicit_profile_with_stdin_only(monkeypatch):
    mod=module();calls=[]
    compile(mod.vm_program(),'native_vm_bridge_program','exec')
    def run(command,data,register,timeout,pass_fds=()):
        calls.append((command,data));return 0,b'{"ok":true}',None
    monkeypatch.setattr(mod,'bounded_client',run)
    bridge=mod.NativeVMBridge(object())
    assert bridge._call('arm',{'request':'a'*32},b'private-config-sentinel')=={'ok':True}
    command,data=calls[0]
    assert command[1:8]==['--profile','default','ssh','--','sudo','-n','/usr/bin/python3']
    assert 'private-config-sentinel' not in str(command) and data==b'private-config-sentinel'


def test_changed_docker_identity_rejected_before_any_vm_request():
    mod=module();calls=[]
    class Local:
        identity='a'*64;image=IMAGE;attempt='c'*32;name='restore-test'
        def metadata(self,_):return dict(boundary_value(),id='b'*64,running=True,pid=123,started_at='stable')
    bridge=mod.NativeVMBridge(Local());bridge._call=lambda *args:calls.append(args)
    with pytest.raises(mod.MinioRestoreError,match='container_boundary'):bridge.capture(b'config')
    assert calls==[]


def test_client_disappearing_at_kill_still_reaps_and_preserves_timeout(monkeypatch):
    mod=module();kill=os.killpg;registered=[]
    def disappeared(pid,number):
        kill(pid,number)
        raise ProcessLookupError()
    monkeypatch.setattr(mod.os,'killpg',disappeared)
    try:
        code,_,stage=mod.bounded_client([sys.executable,'-c','import time;time.sleep(30)'],b'',registered.append,0.05)
        assert stage=='vm_timeout' and code!=0
        with pytest.raises(ProcessLookupError):os.kill(registered[0],0)
    finally:
        if registered:
            try:os.waitpid(registered[0],0)
            except ChildProcessError:pass


def test_vm_cleanup_confirms_a_client_that_exits_between_probe_and_kill(tmp_path,monkeypatch):
    mod=module();path=tmp_path/'request.json';path.write_text('{}')
    record={'client_pid':123,'client_starttime':'777'};states=iter([True,False,False])
    monkeypatch.setattr(mod,'vm_load',lambda *_:record)
    monkeypatch.setattr(mod,'vm_alive',lambda *_:next(states,False))
    monkeypatch.setattr(mod.os,'getpgid',lambda _:123)
    def exited(*_):raise ProcessLookupError()
    monkeypatch.setattr(mod.os,'killpg',exited)
    mod.vm_cleanup(path,{'request':'a'*32})
    assert not path.exists()
