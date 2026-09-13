"""Disposable real MinIO process: conditional writes and concurrent S3 erasure fences.

No production credentials, endpoints, buckets or objects are used.
"""
from __future__ import annotations
import ast
import io
import threading
import http.client
from concurrent.futures import Future
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
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
from app.attachments.object_writer import AttachmentObjectWriter, AttachmentObjectWriterError

BIN = Path('/tmp/hr-launch-minio-20260913/minio-nocgo')
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

def assert_fenced(client, bucket, key):
    rows, pages = versions(client, bucket, key)
    must(bool(rows), 'erasure removed write fence')
    for kind, version in rows:
        must(kind == 'Versions', 'delete marker would reopen conditional writes')
        response = client.head_object(Bucket=bucket, Key=key, VersionId=version)
        must(response.get('VersionId') == version and response.get('ContentLength') == 0 and response.get('Metadata') == {'platform-erasure-fence': 'v1'}, 'payload or unverified fence remains')
    current = client.head_object(Bucket=bucket, Key=key)
    must(current.get('ContentLength') == 0 and current.get('Metadata') == {'platform-erasure-fence': 'v1'} and current.get('VersionId') in {v for _, v in rows}, 'current key is not verified fence')
    return len(rows), pages


def start_owned_call(resources, function):
    # A ThreadPoolExecutor would register non-daemon threads for an unbounded
    # interpreter-exit join even after shutdown(wait=False). These exact owned
    # daemon threads instead have one explicit, bounded cleanup below.
    future = Future()
    def invoke():
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(function())
        except BaseException as error:
            future.set_exception(error)
    thread = threading.Thread(target=invoke, name=f"hr-fence-owned-{len(resources['threads']) + 1}", daemon=True)
    resources['threads'].append(thread)
    resources['futures'].append(future)
    thread.start()
    return future


def exercise_fences(client, port, secret, receipt, resources):
    bucket = 'hr-disposable-fence-test'
    client.create_bucket(Bucket=bucket)
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={'Status': 'Enabled'})
    key = 'a' * 64
    original = []
    for number in range(1001):
        original.append(client.put_object(Bucket=bucket, Key=key, Body=f'fictional {number}'.encode())['VersionId'])
    client.delete_object(Bucket=bucket, Key=key)  # An old-generation marker is part of the fixture.
    client.put_object(Bucket=bucket, Key=key+'-sibling', Body=b'sibling')
    before, pages = versions(client, bucket, key)
    must(pages > 1, 'fixture lacks real pagination')
    store = S3ProcessingObjectStore(client, bucket)
    store.delete(key)
    count, _ = assert_fenced(client, bucket, key)
    must(all(missing(client, bucket, key, v) for v in original), 'prior payload remains accessible')
    must(client.head_object(Bucket=bucket, Key=key+'-sibling')['ContentLength'] == 7, 'prefix sibling changed')
    receipt['checks'].append({'real_pagination_and_fence': 'passed', 'old_data_versions': len(original), 'old_pages': pages, 'fences': count})

    writer = AttachmentObjectWriter(client, bucket)
    raw = b'fictional duplicate payload'
    duplicate = 'b' * 64
    writer.put_stream(duplicate, io.BytesIO(raw), len(raw))
    canonical, _ = versions(client, bucket, duplicate)
    try:
        writer.put_stream(duplicate, io.BytesIO(raw), len(raw))
    except AttachmentObjectWriterError:
        pass
    else:
        raise RuntimeError('same-key original PUT did not reject collision')
    must(versions(client, bucket, duplicate)[0] == canonical, '412 cleanup changed canonical versions')
    writer.delete(duplicate)
    try:
        writer.put_stream(duplicate, io.BytesIO(raw), len(raw))
    except AttachmentObjectWriterError:
        pass
    else:
        raise RuntimeError('future original write crossed fence')
    assert_fenced(client, bucket, duplicate)
    receipt['checks'].append('duplicate original PUT preserves canonical; later PUT cannot cross erasure fence')

    derivative = 'c' * 64
    one = store.put_derivative(raw, object_key=derivative)
    two = store.put_derivative(raw, object_key=derivative)
    must(one.sha256 == two.sha256 and one.object_ref == two.object_ref, 'matching derivative retry failed')
    must(len(versions(client, bucket, derivative)[0]) == 1, 'derivative retry created another payload version')
    store.delete(derivative)
    try:
        store.put_derivative(raw, object_key=derivative)
    except AttachmentWorkerRuntimeError:
        pass
    else:
        raise RuntimeError('future derivative write crossed fence')
    assert_fenced(client, bucket, derivative)
    receipt['checks'].append('derivative identical retry keeps one payload; later derivative rejected by fence')

    concurrent = 'd' * 64
    prior = client.put_object(Bucket=bucket, Key=concurrent, Body=raw)['VersionId']
    barrier = threading.Barrier(3)
    def erase_together():
        barrier.wait(timeout=5)
        store.delete(concurrent)
    futures = [start_owned_call(resources, erase_together) for _ in range(2)]
    barrier.wait(timeout=5)
    for future in futures:
        future.result(timeout=30)
    count, _ = assert_fenced(client, bucket, concurrent)
    must(missing(client, bucket, concurrent, prior), 'concurrent erasers left old payload')
    receipt['checks'].append({'two_real_erasers': 'passed', 'retained_fences': count})

    # Actual in-flight signed HTTP PUT: send all but its final body byte.
    racing = 'e' * 64
    path = f'/{bucket}/{racing}'
    request = AWSRequest(method='PUT', url=f'http://127.0.0.1:{port}'+path, data=raw, headers={'If-None-Match': '*', 'Content-Length': str(len(raw)), 'X-Amz-Content-SHA256': hashlib.sha256(raw).hexdigest()})
    SigV4Auth(Credentials('hr-local-test', secret), 's3', 'us-east-1').add_auth(request)
    prepared = request.prepare()
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    resources['connections'].append(connection)
    connection.putrequest('PUT', path)
    for name, value in prepared.headers.items():
        connection.putheader(name, value)
    connection.endheaders()
    connection.send(raw[:-1])
    receipt['partial_put_transmission'] = {
        'client_prefix_bytes_sent': len(raw) - 1,
        'declared_content_length': len(raw),
        'client_tail_bytes_sent': 0,
        'server_partial_acceptance_observed': False,
        'boundary': 'send completed in the client; thread start and local socket send do not prove server handler acceptance',
    }
    fence_started = threading.Event()
    def concurrent_fence():
        fence_started.set()
        store.delete(racing)
    try:
        future = start_owned_call(resources, concurrent_fence)
        must(fence_started.wait(5), 'eraser did not start')
        # This is only a client-thread scheduling event, not a server receipt.
        receipt['partial_put_transmission']['eraser_client_thread_started'] = True
        # Complete the body after a bounded eraser opportunity; either server
        # serialization is allowed. The final fence assertion remains mandatory.
        try:
            future.result(timeout=0.25)
            fence_before_tail = True
        except TimeoutError:
            fence_before_tail = False
        connection.send(raw[-1:])
        receipt['partial_put_transmission']['client_tail_bytes_sent'] = 1
        response = connection.getresponse()
        status = response.status
        response.read()
        must(status in (200, 409, 412), 'signed in-flight PUT did not exercise conditional semantics')
        future.result(timeout=30)
    finally:
        # No task join occurs here; close the partial-body connection before the
        # main finally terminates the server and boundedly joins owned threads.
        if connection.sock is not None:
            try:
                connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()
    assert_fenced(client, bucket, racing)
    try:
        client.put_object(Bucket=bucket, Key=racing, Body=raw, IfNoneMatch='*')
    except ClientError as error:
        must(error.response['ResponseMetadata']['HTTPStatusCode'] == 412, 'post-fence conditional PUT error is ambiguous')
    else:
        raise RuntimeError('post-fence conditional PUT succeeded')
    receipt['checks'].append({'real_inflight_signed_put': status, 'fence_finished_before_body_tail': fence_before_tail, 'post_fence_put': 412})

    receipt['suspended_trace'] = []
    def trace_suspended(model, parsed, **_kwargs):
        if len(receipt['suspended_trace']) < 100:
            item = {'operation': model.name, 'parsed': {k: v for k, v in parsed.items() if k in ('VersionId', 'DeleteMarker', 'ContentLength', 'Metadata', 'Status', 'Versions', 'DeleteMarkers', 'Error')}}
            for field in ('Versions', 'DeleteMarkers'):
                if field in item['parsed']:
                    item['parsed'][field] = [{k: v for k, v in row.items() if k in ('Key', 'VersionId', 'IsLatest')} for row in item['parsed'][field]]
            item['http_status'] = parsed.get('ResponseMetadata', {}).get('HTTPStatusCode')
            receipt['suspended_trace'].append(item)
            with (OUT/'suspended-trace.jsonl').open('a') as trace:
                trace.write(json.dumps(item)+'\n')
    client.meta.events.register('after-call.s3', trace_suspended)
    suspended = 'f' * 64
    old = client.put_object(Bucket=bucket, Key=suspended, Body=raw)['VersionId']
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={'Status': 'Suspended'})
    client.put_object(Bucket=bucket, Key=suspended, Body=b'null payload')
    store.delete(suspended)
    assert_fenced(client, bucket, suspended)
    must(missing(client, bucket, suspended, old), 'suspended old version remains')
    receipt['checks'].append('suspended bucket retains verified null fence and deletes old payload')
    plain = 'hr-disposable-fence-unversioned'
    client.create_bucket(Bucket=plain)
    client.put_object(Bucket=plain, Key=key, Body=raw)
    AttachmentObjectWriter(client, plain).delete(key)
    current = client.head_object(Bucket=plain, Key=key)
    must(current.get('ContentLength') == 0 and current.get('Metadata') == {'platform-erasure-fence': 'v1'}, 'unversioned key lacks fence')
    try:
        client.put_object(Bucket=plain, Key=key, Body=raw, IfNoneMatch='*')
    except ClientError as error:
        must(error.response['ResponseMetadata']['HTTPStatusCode'] == 412, 'unversioned conditional write error ambiguous')
    else:
        raise RuntimeError('unversioned future write crossed fence')
    receipt['checks'].append('unversioned fence blocks future conditional payload')


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
    resources = {'threads': [], 'futures': [], 'connections': []}
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
                exercise_fences(client, port, secret, receipt, resources)
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
            # Close only registered raw connections before stopping our server.
            # No executor context or interpreter hook may wait for active calls.
            connection_errors = []
            for connection in resources['connections']:
                try:
                    if connection.sock is not None:
                        try:
                            connection.sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    connection.close()
                except Exception as error:
                    connection_errors.append(type(error).__name__)
            receipt['owned_raw_connections_closed'] = not connection_errors
            if connection_errors:
                receipt['status'] = 'failed'
                receipt['connection_cleanup_error_types'] = connection_errors
            for future in resources['futures']:
                future.cancel()  # Cancels only work that never started.
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
            join_until = time.monotonic() + 10
            thread_errors = []
            for thread in resources['threads']:
                try:
                    if thread.ident is not None:
                        thread.join(timeout=max(0, join_until - time.monotonic()))
                except Exception as error:
                    thread_errors.append(type(error).__name__)
            receipt['owned_thread_count'] = len(resources['threads'])
            receipt['owned_threads_alive'] = sum(thread.is_alive() for thread in resources['threads'])
            receipt['owned_futures_done'] = all(future.done() for future in resources['futures'])
            if receipt['owned_threads_alive'] or not receipt['owned_futures_done'] or thread_errors:
                receipt['status'] = 'failed'
                receipt['thread_cleanup_error_types'] = thread_errors
            log_path = root / 'server.log'
            if log_path.exists():
                sanitized = log_path.read_text(errors='replace').replace(secret, '<redacted>')
                (OUT / 'server.log').write_text(sanitized)
                receipt['server_log_sha256'] = hashlib.sha256(sanitized.encode()).hexdigest()
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
