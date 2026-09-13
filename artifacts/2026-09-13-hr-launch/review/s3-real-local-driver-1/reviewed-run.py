"""Disposable real MinIO process: old negative and current S3 adapters.

No production credentials, endpoints, buckets or objects are used.
"""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'backend'))
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from app.attachments.worker_runtime import S3ProcessingObjectStore, AttachmentWorkerRuntimeError
from app.attachments.object_writer import AttachmentObjectWriter

BIN = Path('/tmp/hr-launch-minio-20260913/minio')
OUT = Path(__file__).resolve().parent

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

def must(condition, message):
    if not condition:
        raise RuntimeError(message)

def versions(client, bucket, key):
    found = []
    pages = 0
    for page in client.get_paginator('list_object_versions').paginate(Bucket=bucket, Prefix=key):
        pages += 1
        found.extend((kind, x['VersionId']) for kind in ('Versions', 'DeleteMarkers') for x in page.get(kind, []) if x['Key'] == key)
    return found, pages

def missing(client, bucket, key, version):
    try:
        client.head_object(Bucket=bucket, Key=key, VersionId=version)
    except ClientError as e:
        return e.response['ResponseMetadata']['HTTPStatusCode'] == 404
    return False

class LocalTestDeadline(BaseException):
    pass

def deadline_handler(_signum, _frame):
    raise LocalTestDeadline('local S3 test deadline exceeded')

def main():
    watched = (signal.SIGALRM, signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise RuntimeError('existing timer requires separate test process')
    handlers = {item: signal.getsignal(item) for item in watched}
    receipt = {'started_at': time.time(), 'boundary': 'local disposable real MinIO, real boto3 signed requests; no production or database access', 'checks': []}
    proc = None
    with tempfile.TemporaryDirectory(prefix='hr-real-minio-') as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        port, console = free_port(), free_port()
        secret = secrets.token_urlsafe(36)
        env = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG') if key in os.environ}
        env.update(MINIO_ROOT_USER='hr-local-test', MINIO_ROOT_PASSWORD=secret, MINIO_BROWSER='off', MINIO_UPDATE='off')
        client = boto3.client('s3', endpoint_url=f'http://127.0.0.1:{port}', region_name='us-east-1', aws_access_key_id='hr-local-test', aws_secret_access_key=secret, config=Config(connect_timeout=2, read_timeout=5, retries={'max_attempts': 0}, proxies={}, s3={'addressing_style': 'path'}))
        try:
            for item in watched:
                signal.signal(item, deadline_handler)
            signal.setitimer(signal.ITIMER_REAL, 300)
            receipt['minio_binary_sha256'] = hashlib.sha256(BIN.read_bytes()).hexdigest()
            receipt['minio_source_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd='/tmp/hr-launch-minio-20260913/source', text=True).strip()
            with (root / 'server.log').open('wb') as server_log:
                proc = subprocess.Popen([str(BIN), 'server', str(root / 'data'), '--address', f'127.0.0.1:{port}', '--console-address', f'127.0.0.1:{console}'], env=env, stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + 30
                while True:
                    if proc.poll() is not None:
                        raise RuntimeError('owned MinIO exited before ready')
                    try:
                        client.list_buckets()
                        break
                    except Exception:
                        if time.monotonic() >= deadline:
                            raise RuntimeError('owned MinIO readiness deadline exceeded') from None
                        time.sleep(0.1)
                bucket = 'hr-disposable-version-test'
                client.create_bucket(Bucket=bucket)
                client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={'Status': 'Enabled'})
                must(client.get_bucket_versioning(Bucket=bucket)['Status'] == 'Enabled', 'versioning not enabled')
                key = 'a' * 64
                data = b'fictional private attachment body'
                old = client.put_object(Bucket=bucket, Key=key, Body=data)['VersionId']
                baseline = subprocess.check_output(['git', 'show', 'ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7:backend/app/attachments/worker_runtime.py'], cwd=ROOT, text=True)
                node = next(n for n in ast.parse(baseline).body if isinstance(n, ast.ClassDef) and n.name == 'S3ProcessingObjectStore')
                namespace = {'AttachmentWorkerRuntimeError': AttachmentWorkerRuntimeError}
                exec('from __future__ import annotations\n' + ast.get_source_segment(baseline, node), namespace)
                namespace['S3ProcessingObjectStore'](client, bucket).delete(key)
                response = client.get_object(Bucket=bucket, Key=key, VersionId=old)
                must(response['Body'].read() == data, 'old implementation negative did not reproduce')
                response['Body'].close()
                receipt['checks'].append('ce6 adapter bare deletion leaves exact previous data version readable')
                client.put_object(Bucket=bucket, Key=key + '-sibling', Body=b'sibling')
                # More than the S3 page limit proves actual pagination on this key.
                for n in range(1001):
                    client.put_object(Bucket=bucket, Key=key, Body=f'fictional version {n}'.encode())
                prior, page_count = versions(client, bucket, key)
                must(page_count > 1, 'fixture did not cross real S3 page boundary')
                S3ProcessingObjectStore(client, bucket).delete(key)
                must(versions(client, bucket, key)[0] == [], 'current adapter left data versions or markers')
                must(all(missing(client, bucket, key, version) for kind, version in prior if kind == 'Versions'), 'exact prior data version still exists')
                must(client.head_object(Bucket=bucket, Key=key + '-sibling')['ContentLength'] == 7, 'prefix sibling changed')
                receipt['checks'].append({'versioned_pagination_exact_key': 'passed', 'prior_entries': len(prior), 'prior_pages': page_count, 'exact_old_data_versions_checked': sum(k == 'Versions' for k, _ in prior)})
                # Suspended versioning retains historic versions and a null version.
                suspended = 'b' * 64
                client.put_object(Bucket=bucket, Key=suspended, Body=b'old version')
                client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={'Status': 'Suspended'})
                client.put_object(Bucket=bucket, Key=suspended, Body=b'null version')
                before, _ = versions(client, bucket, suspended)
                must(any(v == 'null' for _, v in before), 'null version fixture missing')
                AttachmentObjectWriter(client, bucket).delete(suspended)
                must(versions(client, bucket, suspended)[0] == [], 'suspended versions remain')
                must(all(missing(client, bucket, suspended, v) for kind, v in before if kind == 'Versions'), 'suspended exact data version still exists')
                receipt['checks'].append('object writer deletes suspended historical and null versions')
                plain = 'hr-disposable-unversioned-test'
                client.create_bucket(Bucket=plain)
                client.put_object(Bucket=plain, Key=key, Body=data)
                AttachmentObjectWriter(client, plain).delete(key)
                try:
                    client.head_object(Bucket=plain, Key=key)
                except ClientError as error:
                    must(error.response['ResponseMetadata']['HTTPStatusCode'] == 404, 'unversioned ambiguous error')
                else:
                    raise RuntimeError('unversioned payload remains')
                receipt['checks'].append('unversioned object writer deletes exact key')
                receipt['status'] = 'passed'
        except BaseException as error:
            receipt['status'] = 'failed'
            receipt['error_type'] = type(error).__name__
            receipt['error_message'] = str(error).replace(secret, '<redacted>')[:1000]
            raise
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            for item in watched:
                signal.signal(item, signal.SIG_IGN)
            if proc is not None:
                try:
                    if proc.poll() is None:
                        try:
                            os.killpg(proc.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            try:
                                os.killpg(proc.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            proc.wait(timeout=5)
                    receipt['owned_process_reaped'] = proc.poll() is not None
                except Exception as error:
                    receipt['status'] = 'failed'
                    receipt['owned_process_reaped'] = False
                    receipt['cleanup_error_type'] = type(error).__name__
            receipt['finished_at'] = time.time()
            receipt['driver_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            receipt['adapter_sha256'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'backend/app/attachments/worker_runtime.py', ROOT/'backend/app/attachments/object_writer.py', ROOT/'backend/app/attachments/s3_erasure.py')}
            (OUT / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
            print(json.dumps(receipt))
            for item, handler in handlers.items():
                signal.signal(item, handler)
            if receipt['status'] != 'passed':
                raise SystemExit(1)

if __name__ == '__main__':
    main()
