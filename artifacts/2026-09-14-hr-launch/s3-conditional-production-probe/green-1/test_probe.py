import importlib.util
import io
import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("conditional_probe", HERE / "probe.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class S3:
    def __init__(self, ignore=False, unknown=False):
        self.objects = {}
        self.calls = []
        self.ignore, self.unknown = ignore, unknown
        self.counter = 0

    def get_bucket_versioning(self, **kw):
        return {"Status": "Enabled"}

    def list_object_versions(self, **kw):
        return {
            "Versions": [{"Key": k, "VersionId": v} for (k, v) in self.objects],
            "IsTruncated": False,
        }

    def put_object(self, **kw):
        self.calls.append(("put", dict(kw)))
        assert kw["IfNoneMatch"] == "*"
        key = kw["Key"]
        if any(k == key for k, v in self.objects) and not self.ignore:
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )
        self.counter += 1
        v = "version-" + str(self.counter)
        self.objects[key, v] = (kw["Body"], kw.get("Metadata", {}))
        if self.unknown:
            raise TimeoutError("synthetic lost response")
        return {
            "VersionId": v,
            "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "synthetic"},
        }

    def get_object(self, **kw):
        candidates = [v for k, v in self.objects if k == kw["Key"]]
        v = kw.get("VersionId", candidates[-1] if candidates else "")
        body, metadata = self.objects[kw["Key"], v]
        return {
            "Body": io.BytesIO(body),
            "ContentLength": len(body),
            "Metadata": metadata,
            "VersionId": v,
        }

    def head_object(self, **kw):
        result = self.get_object(**kw)
        result.pop("Body")
        return result

    def delete_object(self, **kw):
        self.calls.append(("delete", dict(kw)))
        assert kw.get("VersionId") and kw["VersionId"] != "null"
        del self.objects[kw["Key"], kw["VersionId"]]
        return {
            "VersionId": kw["VersionId"],
            "ResponseMetadata": {"HTTPStatusCode": 204},
        }


def run(tmp_path, client):
    p = m.Probe(client, tmp_path / "private.json", {"run_id": "a" * 32})
    return p, p.execute()


def test_success_original_and_fence_412_preserve_exact_versions_cleanup_only_owned(
    tmp_path,
):
    c = S3()
    p, result = run(tmp_path, c)
    assert result["status"] == "passed"
    assert result["conditional_rejections"] == [412, 412]
    assert not c.objects
    assert len([x for x in c.calls if x[0] == "put"]) == 4
    assert all(x[1]["VersionId"] in p.owned for x in c.calls if x[0] == "delete")
    assert p.key not in json.dumps(result)
    assert (tmp_path / "private.json").stat().st_mode & 0o777 == 0o600


def test_provider_ignoring_condition_is_red_not_pass(tmp_path):
    c = S3(ignore=True)
    _p, result = run(tmp_path, c)
    assert result["status"] == "failed"
    assert result["failure"] == "conditional_put_not_rejected"
    assert not [x for x in c.calls if x[0] == "delete"]
    assert len(c.objects) == 2


def test_unknown_put_retains_sending_ledger_and_never_retries_or_deletes(tmp_path):
    c = S3(unknown=True)
    _p, result = run(tmp_path, c)
    assert result["status"] == "unknown"
    ledger = json.loads((tmp_path / "private.json").read_text())
    assert ledger["events"][-2]["stage"] == "payload_put_sending"
    assert len(c.calls) == 1 and c.objects


def test_preexisting_any_version_no_write(tmp_path):
    c = S3()
    p = m.Probe(c, tmp_path / "private.json", {"run_id": "b" * 32})
    c.objects[p.key, "business-version"] = (b"untouched", {})
    assert p.execute()["failure"] == "key_not_empty"
    assert not c.calls


def test_suspended_bucket_no_write(tmp_path):
    c = S3()
    c.get_bucket_versioning = lambda **kw: {"Status": "Suspended"}
    assert run(tmp_path, c)[1]["failure"] == "versioning_not_enabled"
    assert not c.calls


def test_403_second_put_unknown_no_cleanup(tmp_path):
    c = S3()
    original = c.put_object

    def put(**kw):
        if c.objects:
            raise ClientError(
                {
                    "ResponseMetadata": {"HTTPStatusCode": 403},
                    "Error": {"Code": "AccessDenied"},
                },
                "PutObject",
            )
        return original(**kw)

    c.put_object = put
    assert run(tmp_path, c)[1]["status"] == "unknown"
    assert not [x for x in c.calls if x[0] == "delete"]


def test_null_version_reply_unknown_never_deletes(tmp_path):
    c = S3()
    original = c.put_object

    def put(**kw):
        original(**kw)
        return {"VersionId": "null"}

    c.put_object = put
    assert run(tmp_path, c)[1]["status"] == "unknown"
    assert not [x for x in c.calls if x[0] == "delete"]


def test_existing_ledger_cannot_resume_or_replace(tmp_path):
    path = tmp_path / "private.json"
    path.write_text("existing")
    with pytest.raises(FileExistsError):
        m.Probe(S3(), path, {"run_id": "a" * 32})
    assert path.read_text() == "existing"


def test_changed_original_contents_cannot_pass_or_cleanup(tmp_path):
    c = S3()
    original = c.get_object

    def get(**kw):
        result = original(**kw)
        result["Body"] = io.BytesIO(b"changed")
        return result

    c.get_object = get
    assert run(tmp_path, c)[1]["status"] == "failed"
    assert not [x for x in c.calls if x[0] == "delete"]


def test_complete_pagination_detects_preexisting_exact_key(tmp_path):
    c = S3()
    p = m.Probe(c, tmp_path / "private.json", {"run_id": "c" * 32})
    calls = []

    def listing(**kw):
        calls.append(kw)
        if len(calls) == 1:
            return {
                "Versions": [{"Key": p.key + "-other", "VersionId": "other"}],
                "IsTruncated": True,
                "NextKeyMarker": p.key,
                "NextVersionIdMarker": "marker",
            }
        return {
            "DeleteMarkers": [{"Key": p.key, "VersionId": "existing-marker"}],
            "IsTruncated": False,
        }

    c.list_object_versions = listing
    assert p.execute()["failure"] == "key_not_empty"
    assert len(calls) == 2 and calls[1]["VersionIdMarker"] == "marker"
    assert not c.calls
