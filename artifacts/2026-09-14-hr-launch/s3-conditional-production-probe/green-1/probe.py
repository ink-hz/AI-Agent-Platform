"""One synthetic key; no blind retries, bare deletes or production invocation here."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import stat
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

ENDPOINT = "http://platform-minio:9000"
BUCKET = "orbbec-agent-attachments"
FENCE = {"platform-erasure-fence": "v1"}


def sha(value):
    return hashlib.sha256(value).hexdigest()


class Rejected(Exception):
    pass


class Deadline(BaseException):
    pass


def require(value, code):
    if not value:
        raise Rejected(code)


class Probe:
    def __init__(self, client, path, binding):
        self.client, self.path, self.binding = client, path, binding
        self.key = (
            "platform-synthetic-conditional-probe/"
            + binding["run_id"]
            + "/"
            + uuid4().hex
        )
        self.owned = []
        self.events = []
        self.rejections = []
        self.state = "started"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        self.record("reserved")

    def record(self, stage, **values):
        self.events.append({"stage": stage, **values})
        payload = {
            "binding": self.binding,
            "key": self.key,
            "bucket": BUCKET,
            "owned_versions": self.owned,
            "events": self.events,
            "status": self.state,
        }
        temporary = self.path.with_name(self.path.name + ".part")
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(payload, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.path)
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def versions(self):
        marker = {}
        rows = []
        seen = set()
        for _ in range(8):
            value = self.client.list_object_versions(
                Bucket=BUCKET, Prefix=self.key, MaxKeys=1000, **marker
            )
            require(type(value.get("IsTruncated")) is bool, "listing_incomplete")
            for kind in ("Versions", "DeleteMarkers"):
                for row in value.get(kind, []):
                    if row["Key"] == self.key:
                        rows.append((kind, row["VersionId"]))
            require(len(rows) <= 8, "unexpected_versions")
            if not value["IsTruncated"]:
                return rows
            pair = (value.get("NextKeyMarker"), value.get("NextVersionIdMarker"))
            require(
                all(isinstance(x, str) and x for x in pair) and pair not in seen,
                "listing_incomplete",
            )
            seen.add(pair)
            marker = {"KeyMarker": pair[0], "VersionIdMarker": pair[1]}
        raise Rejected("listing_incomplete")

    def put(self, stage, body, metadata=None, reject=False):
        self.record(
            stage + "_sending",
            request_id=uuid4().hex,
            body_sha256=sha(body),
            bytes=len(body),
            conditional=True,
        )
        try:
            response = self.client.put_object(
                Bucket=BUCKET,
                Key=self.key,
                Body=body,
                ContentLength=len(body),
                Metadata=metadata or {},
                IfNoneMatch="*",
            )
        except ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if reject and status == 412:
                self.rejections.append(status)
                self.record(
                    stage + "_rejected",
                    http_status=status,
                    request_id=error.response.get("ResponseMetadata", {}).get(
                        "RequestId"
                    ),
                )
                return None
            raise
        version = response.get("VersionId")
        if (
            not isinstance(version, str)
            or not version
            or version == "null"
            or version in self.owned
        ):
            raise RuntimeError("unknown version response")
        self.owned.append(version)
        self.record(
            stage + "_received",
            version=version,
            request_id=response.get("ResponseMetadata", {}).get("RequestId"),
        )
        require(not reject, "conditional_put_not_rejected")
        return version

    def verify(self, version, body, metadata):
        require(self.versions() == [("Versions", version)], "version_set_changed")
        for explicit in (True, False):
            args = {"Bucket": BUCKET, "Key": self.key}
            if explicit:
                args["VersionId"] = version
            head = self.client.head_object(**args)
            require(
                head.get("VersionId") == version
                and type(head.get("ContentLength")) is int
                and head["ContentLength"] == len(body)
                and head.get("Metadata", {}) == metadata,
                "head_changed",
            )
            response = self.client.get_object(**args)
            stream = response["Body"]
            try:
                actual = stream.read(len(body) + 1)
            finally:
                stream.close()
            require(
                response.get("VersionId") == version and actual == body,
                "payload_changed",
            )
        self.record(
            "exact_version_and_current_verified", version=version, body_sha256=sha(body)
        )

    def delete(self, version):
        require(version in self.owned and version != "null", "delete_not_owned")
        require(
            self.client.get_bucket_versioning(Bucket=BUCKET).get("Status") == "Enabled",
            "versioning_changed",
        )
        self.record("delete_sending", version=version, request_id=uuid4().hex)
        response = self.client.delete_object(
            Bucket=BUCKET, Key=self.key, VersionId=version
        )
        if response.get("VersionId") != version or response.get("DeleteMarker") is True:
            raise RuntimeError("unknown delete response")
        require(not self.versions(), "cleanup_incomplete")
        self.record("delete_verified", version=version)

    def execute(self):
        failure = None
        try:
            require(
                self.client.get_bucket_versioning(Bucket=BUCKET).get("Status")
                == "Enabled",
                "versioning_not_enabled",
            )
            require(not self.versions(), "key_not_empty")
            self.record("key_absent")
            payload = b"SYNTHETIC conditional PUT production capability probe; no personal data.\n"
            version = self.put("payload_put", payload)
            self.put("conflicting_put", b"SYNTHETIC differing payload.\n", reject=True)
            self.verify(version, payload, {})
            self.delete(version)
            fence = self.put("fence_put", b"", FENCE)
            self.put(
                "after_fence_put", b"SYNTHETIC must not overwrite fence.\n", reject=True
            )
            self.verify(fence, b"", FENCE)
            self.delete(fence)
            self.state = "passed"
        except Rejected as error:
            self.state, failure = "failed", str(error)
        except BaseException:  # noqa: BLE001 - retain unknown mutation evidence on signals
            self.state, failure = "unknown", "request_or_observation_unknown"
        self.record("finished", failure=failure)
        return {
            "status": self.state,
            "failure": failure,
            "key_sha256": sha(self.key.encode()),
            "conditional_rejections": self.rejections,
            "private_ledger_sha256": sha(self.path.read_bytes()),
            "cleanup_verified": self.state == "passed",
            "binding_sha256": sha(json.dumps(self.binding, sort_keys=True).encode()),
            "boundary": "synthetic S3 only; MinIO generation requires external before/after inspect",
        }


def private(path, *, directory=False):
    info = path.lstat()
    require(
        path.is_absolute()
        and not path.is_symlink()
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
        and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)),
        "private_path_invalid",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    require(args.execute, "explicit_execution_required")
    private(args.binding)
    private(args.evidence, directory=True)
    binding = json.loads(args.binding.read_bytes())
    require(
        set(binding) == {"run_id", "minio", "client", "script_sha256"},
        "binding_invalid",
    )
    require(re.fullmatch("[0-9a-f]{32}", binding["run_id"]), "run_invalid")
    for name in ("minio", "client"):
        identity = binding[name]
        require(
            re.fullmatch("[0-9a-f]{64}", identity["id"])
            and re.fullmatch("sha256:[0-9a-f]{64}", identity["image"]),
            "identity_invalid",
        )
    require(
        type(binding["minio"]["pid"]) is int
        and binding["minio"]["pid"] > 0
        and isinstance(binding["minio"]["started_at"], str)
        and binding["minio"]["started_at"],
        "generation_invalid",
    )
    require(
        socket.gethostname() == binding["client"]["id"][:12], "client_hostname_mismatch"
    )
    require(
        sha(Path(__file__).read_bytes()) == binding["script_sha256"], "source_changed"
    )
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "existing_timer")
    secrets = []
    for name in ("attachment-s3-access-key", "attachment-s3-secret-key"):
        p = Path("/run/secrets") / name
        st = p.lstat()
        require(
            not p.is_symlink()
            and stat.S_ISREG(st.st_mode)
            and stat.S_IMODE(st.st_mode) == 0o600
            and st.st_uid in (0, 10001),
            "secret_metadata_invalid",
        )
        secrets.append(p.read_text().strip())
    require(all(secrets), "secret_missing")
    client = boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id=secrets[0],
        aws_secret_access_key=secrets[1],
        config=Config(
            signature_version="s3v4",
            connect_timeout=3,
            read_timeout=5,
            retries={"total_max_attempts": 1},
            proxies={},
            s3={"addressing_style": "path"},
        ),
    )

    def stop(_number, _frame):
        raise Deadline()

    numbers = (
        signal.SIGALRM,
        signal.SIGINT,
        signal.SIGTERM,
        signal.SIGHUP,
        signal.SIGQUIT,
    )
    previous = {n: signal.getsignal(n) for n in numbers}
    try:
        for n in numbers:
            signal.signal(n, stop)
        signal.setitimer(signal.ITIMER_REAL, 90)
        result = Probe(client, args.evidence / "private-ledger.json", binding).execute()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for n, h in previous.items():
            signal.signal(n, h)
        client.close()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception, Deadline):  # noqa: BLE001 - fixed diagnostics, no private exception output
        print(
            '{"status":"unknown","failure":"probe_failed_private_ledger_requires_inspection"}'
        )
        raise SystemExit(1) from None
