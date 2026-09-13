"""Observer classification tests; S3 boundary substituted, no production calls."""

import importlib.util
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

ROOT = Path(__file__).parents[2]


def module(monkeypatch):
    folder = ROOT / "artifacts/2026-09-13-hr-launch/production"
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location(
        "observer", folder / "attachment_erasure_observe.py"
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class S3:
    def __init__(self, versions=(), error=None):
        self.versions = list(versions)
        self.error = error
        self.calls = []

    def list_object_versions(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "IsTruncated": False,
            "Versions": [
                {"Key": "owned", "VersionId": value} for value in self.versions
            ],
        }

    def head_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise ClientError(
                {
                    "Error": {"Code": self.error},
                    "ResponseMetadata": {"HTTPStatusCode": 403},
                },
                "HeadObject",
            )
        if kwargs.get("VersionId") in self.versions:
            return {"ContentLength": 10, "VersionId": kwargs["VersionId"]}
        raise ClientError(
            {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
            "HeadObject",
        )


def test_deleted_key_does_not_hide_old_version(monkeypatch):
    observer = module(monkeypatch)
    assert (
        observer.verify_absence(S3(["old"]), "bucket", {"owned": ["old"]})["status"]
        == "remaining"
    )


def test_head_forbidden_is_unknown(monkeypatch):
    observer = module(monkeypatch)
    with pytest.raises(observer.Unknown):
        observer.verify_absence(S3(error="AccessDenied"), "bucket", {"owned": ["old"]})


def test_complete_enumeration_and_exact_version_absent(monkeypatch):
    observer = module(monkeypatch)
    result = observer.verify_absence(S3(), "bucket", {"owned": ["old"]})
    assert result["status"] == "absent"
    assert result["checked_versions"] == 1


def test_pagination_must_finish_and_filter_exact_key(monkeypatch):
    observer = module(monkeypatch)

    class Paged(S3):
        def list_object_versions(self, **kwargs):
            self.calls.append(kwargs)
            if "KeyMarker" not in kwargs:
                return {
                    "IsTruncated": True,
                    "Versions": [{"Key": "owned-other", "VersionId": "unrelated"}],
                    "NextKeyMarker": "owned",
                    "NextVersionIdMarker": "v1",
                }
            return {
                "IsTruncated": False,
                "Versions": [{"Key": "owned", "VersionId": "v2"}],
            }

    client = Paged()
    assert observer.inventory(client, "bucket", "owned") == ["v2"]
    assert client.calls[1]["VersionIdMarker"] == "v1"
    assert all(call["Prefix"] == "owned" for call in client.calls)


def test_missing_pagination_cursor_unknown(monkeypatch):
    observer = module(monkeypatch)
    client = S3()
    client.list_object_versions = lambda **_: {"IsTruncated": True}
    with pytest.raises(observer.Unknown):
        observer.inventory(client, "bucket", "owned")


def test_missing_reference_set_unknown(monkeypatch):
    observer = module(monkeypatch)
    with pytest.raises(observer.Unknown):
        observer.verify_absence(S3(), "bucket", {})


# Reuse the real DingTalk session/HTTP/PG fixture; only login exchange, local
# object store and scanner provider boundaries are substituted there.
from tests.test_attachment_erasure_production_canary import api  # noqa: F401


def test_real_owner_pg_readonly_snapshot_and_terminal_gate(api, tmp_path, monkeypatch):  # noqa: F811
    from uuid import uuid4

    import psycopg
    from tests.test_attachment_erasure_production_canary import harness

    observer = module(monkeypatch)
    canary_module = harness(monkeypatch)
    canary = canary_module.AttachmentCanary(
        api["config"], tmp_path / "canary", api["client"]
    )
    import hashlib

    from app.attachments.worker import StoredDerivative

    def put_derivative(data, *, object_key):
        api["store"].objects[object_key] = data
        return StoredDerivative(object_key, len(data), hashlib.sha256(data).digest())

    api["store"].put_derivative = put_derivative
    canary.prepare()
    repository = observer.Repository(api["db"].dsn)
    ledger = canary.ledger
    aid, uid = ledger["attachment_id"], ledger["upload_id"]
    with repository.connection() as connection:
        assert (
            connection.execute("show transaction_read_only").fetchone()[
                "transaction_read_only"
            ]
            == "on"
        )
        assert (
            connection.execute("select current_user").fetchone()["current_user"]
            == "platform_control_app"
        )
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            connection.execute(
                "delete from platform_attachments.attachments where attachment_id=%s",
                (aid,),
            )
    with pytest.raises(observer.Unknown, match="owner_attachment_unproven"):
        repository.read(str(uuid4()), aid, uid)
    graph = repository.read(str(api["owner"]), aid, uid)
    graph["base"]["immutable_locator"] = "version:v1"
    for row in graph["uploads"]:
        row["immutable_locator"] = "version:v1"
    refs = observer.references(api["codec"], graph, aid)
    assert refs

    class Objects:
        from types import SimpleNamespace

        meta = SimpleNamespace(endpoint_url="http://local-substitute")
        erased = False
        forbidden = False

        def get_bucket_versioning(self, **_):
            return {"Status": "Enabled"}

        def list_object_versions(self, **kwargs):
            assert kwargs["Prefix"] in refs
            return {
                "IsTruncated": False,
                "Versions": []
                if self.erased
                else [{"Key": kwargs["Prefix"], "VersionId": "v1"}],
            }

        def head_object(self, **kwargs):
            assert kwargs["Key"] in refs
            if self.forbidden:
                raise ClientError(
                    {
                        "Error": {"Code": "AccessDenied"},
                        "ResponseMetadata": {"HTTPStatusCode": 403},
                    },
                    "HeadObject",
                )
            if self.erased:
                raise ClientError(
                    {
                        "Error": {"Code": "404"},
                        "ResponseMetadata": {"HTTPStatusCode": 404},
                    },
                    "HeadObject",
                )
            return {"ContentLength": 10, "VersionId": "v1"}

    # MemoryStore supplies etag locators, whereas the production bucket has
    # version IDs. Replace only that provider metadata in returned graph.
    original = repository.read

    def read(*args):
        result = original(*args)
        result["base"]["immutable_locator"] = "version:v1"
        for row in result["uploads"]:
            row["immutable_locator"] = "version:v1"
        return result

    repository.read = read
    objects = Objects()
    observer.authenticate(api["client"], api["config"], aid, "before")
    result, frozen = observer.observe(
        "before", ledger, api["config"], repository, api["codec"], objects, "bucket"
    )
    assert result["status"] == "captured"
    assert all(key not in str(frozen) for key in refs)
    canary.erase()
    # Actual SQL job is still queued; S3 absence alone cannot turn it green.
    objects.erased = True
    result, _ = observer.observe(
        "after",
        canary.ledger,
        api["config"],
        repository,
        api["codec"],
        objects,
        "bucket",
        frozen,
    )
    assert result["status"] == "job_not_completed"
    assert result["physical_erasure_verified"] is False
    from app.attachments.erasure import (
        AttachmentErasureRepository,
        AttachmentErasureService,
    )

    service = AttachmentErasureService(
        AttachmentErasureRepository(
            api["db"].dsn.replace(
                "user=platform_control_app", "user=platform_control_maintenance"
            ),
            content_codec=api["codec"],
        ),
        api["store"],
    )
    assert service.process_next("observer-local")
    # Genuine job completion does not override an old readable S3 version.
    objects.erased = False
    result, _ = observer.observe(
        "after",
        canary.ledger,
        api["config"],
        repository,
        api["codec"],
        objects,
        "bucket",
        frozen,
    )
    assert result["status"] == "remaining"
    assert result["physical_erasure_verified"] is False
    objects.erased = True
    result, _ = observer.observe(
        "after",
        canary.ledger,
        api["config"],
        repository,
        api["codec"],
        objects,
        "bucket",
        frozen,
    )
    assert result["physical_erasure_verified"] is True
    import copy

    corrupt = copy.deepcopy(frozen)
    corrupt["ciphertext"] = "AAAA"
    from app.execution_relay.content_crypto import ContentCryptoError

    with pytest.raises(ContentCryptoError):
        observer.observe(
            "after",
            canary.ledger,
            api["config"],
            repository,
            api["codec"],
            objects,
            "bucket",
            corrupt,
        )
    with pytest.raises(observer.Unknown, match="snapshot_scope_changed"):
        observer.observe(
            "after",
            canary.ledger,
            api["config"],
            repository,
            api["codec"],
            objects,
            "changed-bucket",
            frozen,
        )
    objects.forbidden = True
    with pytest.raises(observer.Unknown):
        observer.observe(
            "after",
            canary.ledger,
            api["config"],
            repository,
            api["codec"],
            objects,
            "bucket",
            frozen,
        )


def test_new_version_is_remaining(monkeypatch):
    observer = module(monkeypatch)
    result = observer.verify_absence(S3(["new"]), "bucket", {"owned": ["old"]})
    assert result["status"] == "remaining"
    assert result["checked_versions"] == 2


def test_network_failure_is_unknown(monkeypatch):
    observer = module(monkeypatch)
    client = S3()

    def broken(**kwargs):
        raise TimeoutError("not a missing version")

    client.head_object = broken
    with pytest.raises(observer.Unknown):
        observer.verify_absence(client, "bucket", {"owned": ["old"]})


def test_cli_whole_deadline_retains_unknown_receipt(monkeypatch, tmp_path):
    import json
    import signal
    import sys
    import time

    observer = module(monkeypatch)
    trust_env_values = []
    real_client = observer.httpx.Client

    def client_factory(**kwargs):
        trust_env_values.append(kwargs.get("trust_env"))
        return real_client(**kwargs)

    monkeypatch.setattr(observer.httpx, "Client", client_factory)
    receipt = tmp_path / "receipt.json"
    snapshot = tmp_path / "snapshot.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "observer",
            "before",
            "--config",
            "/unused",
            "--ledger",
            "/unused",
            "--snapshot",
            str(snapshot),
            "--receipt",
            str(receipt),
        ],
    )
    monkeypatch.setattr(observer, "load_config", lambda _: {})
    monkeypatch.setattr(observer, "private_json", lambda _: {})
    monkeypatch.setattr(observer, "binding", lambda *_: {"attachment_id": "unused"})
    monkeypatch.setattr(observer, "_build_content_codec", lambda: None)
    monkeypatch.setattr(observer, "read_secret_file", lambda _: "unused")
    monkeypatch.setattr(observer, "Repository", lambda _: None)
    monkeypatch.setattr(observer, "authenticate", lambda *_: None)
    monkeypatch.setattr(observer, "_build_s3_client", lambda: None)
    monkeypatch.setenv("PLATFORM_CONTROL_DATABASE_URL_FILE", "/unused")
    monkeypatch.setenv("PLATFORM_ATTACHMENT_S3_BUCKET", "unused")

    def blocked(*args):
        time.sleep(2)
        raise AssertionError("deadline did not interrupt blocking observation")

    monkeypatch.setattr(observer, "observe", blocked)
    real = observer.bounded_request

    def short(client, **kwargs):
        assert kwargs["timeout"] == 60
        return real(client, timeout=0.15)

    monkeypatch.setattr(observer, "bounded_request", short)
    handler = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()
    assert observer.main() == 1
    elapsed = time.monotonic() - started
    assert elapsed < 1.5
    assert json.loads(receipt.read_text())["status"] == "unknown"
    assert not snapshot.exists()
    assert trust_env_values == [False]
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == handler
    print(
        json.dumps(
            {
                "blocking_observation_elapsed_seconds": elapsed,
                "budget_seconds": 0.15,
                "unknown_receipt": True,
            }
        )
    )


def test_pagination_limit_is_unknown(monkeypatch):
    observer = module(monkeypatch)
    client = S3()
    count = 0

    def endless(**kwargs):
        nonlocal count
        count += 1
        return {
            "IsTruncated": True,
            "NextKeyMarker": "owned",
            "NextVersionIdMarker": str(count),
        }

    client.list_object_versions = endless
    with pytest.raises(observer.Unknown, match="version_page_limit"):
        observer.inventory(client, "bucket", "owned")
    assert count == observer.MAX_PAGES


class FenceS3(S3):
    def __init__(self, metadata=None, size=0, marker=False, payload=False):
        super().__init__(['f1', 'f2'] + (['payload'] if payload else []))
        self.metadata = {'platform-erasure-fence': 'v1'} if metadata is None else metadata
        self.size, self.marker = size, marker

    def list_object_versions(self, **kwargs):
        page = super().list_object_versions(**kwargs)
        if self.marker:
            page['DeleteMarkers'] = [{'Key': 'owned', 'VersionId': 'marker'}]
        return page

    def head_object(self, **kwargs):
        self.calls.append(kwargs)
        version = kwargs.get('VersionId')
        if version is None:
            if self.marker:
                raise ClientError({'Error': {'Code': '404'}, 'ResponseMetadata': {'HTTPStatusCode': 404}}, 'HeadObject')
            version = 'f2'
        if version in ('f1', 'f2'):
            return {'VersionId': version, 'ContentLength': self.size, 'Metadata': self.metadata}
        if version == 'payload' and version in self.versions:
            return {'VersionId': version, 'ContentLength': 3, 'Metadata': {}}
        raise ClientError({'Error': {'Code': '404'}, 'ResponseMetadata': {'HTTPStatusCode': 404}}, 'HeadObject')


def test_multiple_exact_fences_and_current_head_required(monkeypatch):
    observer = module(monkeypatch)
    client = FenceS3()
    result = observer.verify_absence(client, 'bucket', {'owned': ['old']})
    assert result['status'] == 'absent'
    assert result['verified_fence_versions'] == 2
    assert any(call.get('Key') == 'owned' and 'VersionId' not in call for call in client.calls)
    assert {'f1', 'f2', 'old'} <= {call.get('VersionId') for call in client.calls}


@pytest.mark.parametrize('metadata,size', [({}, 0), ({'platform-erasure-fence': 'v1', 'extra': 'x'}, 0), ({'platform-erasure-fence': 'v1'}, 1)])
def test_unmarked_zero_or_nonempty_marker_is_payload(monkeypatch, metadata, size):
    observer = module(monkeypatch)
    result = observer.verify_absence(FenceS3(metadata, size), 'bucket', {'owned': ['old']})
    assert result['status'] == 'remaining'


def test_current_delete_marker_is_not_write_fence(monkeypatch):
    observer = module(monkeypatch)
    result = observer.verify_absence(FenceS3(marker=True), 'bucket', {'owned': ['old']})
    assert result['status'] == 'unprotected'


def test_new_payload_among_fences_fails(monkeypatch):
    observer = module(monkeypatch)
    result = observer.verify_absence(FenceS3(payload=True), 'bucket', {'owned': ['old']})
    assert result['status'] == 'remaining'
