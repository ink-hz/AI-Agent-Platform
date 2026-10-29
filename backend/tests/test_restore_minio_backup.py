"""Local synthetic AEAD/tar/S3/isolation contracts; real execution is separate."""
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import sys
import tarfile

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
            'network_mode':'none','ports':{},'port_bindings':{},'mounts':[], 'tmpfs':{'/data':'rw,uid=10001,gid=10001','/tmp':'rw,uid=10001,gid=10001'},
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
        def capture(self,args,**kwargs):
            calls.append((args,kwargs))
            xml = ('<ListAllMyBucketsResult><Buckets><Bucket><Name>bucket</Name></Bucket></Buckets>'
                   '<ContinuationToken>next-bucket-page</ContinuationToken></ListAllMyBucketsResult>')
            return b'HTTP/1.1 200 OK\r\nContent-Type: application/xml\r\n\r\n'+xml.encode()
    class Local:
        identity = 'a'*64; docker = Runner()
    client = mod.CurlS3(Local(),'temporary-user-sentinel','temporary-password-sentinel')
    page = client.list_buckets()
    assert page['ContinuationToken'] == 'next-bucket-page'
    args,settings = calls[0]
    assert 'temporary-user-sentinel' not in str(args) and 'temporary-password-sentinel' not in str(args)
    assert b'Authorization: AWS4-HMAC-SHA256 ' in settings['data']
    assert b'temporary-password-sentinel' not in settings['data']
    client.list_buckets(ContinuationToken='next-bucket-page')
    assert len(calls) == 2
    assert b'continuation-token=next-bucket-page' in calls[-1][1]['data']
