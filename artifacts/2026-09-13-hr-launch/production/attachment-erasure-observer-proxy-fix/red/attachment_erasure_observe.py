"""Read-only, exact-attachment S3 version observation. Never claims or deletes jobs.

Run inside the reviewed image with existing protected DB/S3/keyring files mounted
read-only. Only a real previously issued owner session is accepted. See preparation
README; stdout never contains object keys, version IDs, credentials or DB errors.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
from pathlib import Path
from uuid import UUID

import httpx
import psycopg
from api_canary import (
    RequestDeadline,
    bounded_request,
    load_config,
    private_json,
    sha,
    write_private,
)
from app.attachments.conversation_repository import attachment_object_subject
from app.attachments.worker_runtime import _build_content_codec, _build_s3_client
from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import SealedContent
from app.local_secrets import read_secret_file
from botocore.exceptions import ClientError
from psycopg.rows import dict_row

MAX_KEYS = 32
MAX_VERSIONS = 128
MAX_PAGES = 8


class Unknown(Exception):
    """No protected detail is allowed in diagnostics."""


def inventory(client, bucket, key):
    """List the entire exact-key data-version set; markers are not payload bytes."""
    values, cursors = set(), set()
    kwargs = {"Bucket": bucket, "Prefix": key, "MaxKeys": 128}
    for _ in range(MAX_PAGES):
        page = client.list_object_versions(**kwargs)
        if not isinstance(page.get("IsTruncated"), bool):
            raise Unknown("version_listing_invalid")
        for entry in page.get("Versions", []):
            if entry.get("Key") == key:
                version = entry.get("VersionId")
                if not isinstance(version, str) or not version:
                    raise Unknown("version_missing")
                values.add(version)
                if len(values) > MAX_VERSIONS:
                    raise Unknown("version_limit")
        if not page["IsTruncated"]:
            return sorted(values)
        cursor = (page.get("NextKeyMarker"), page.get("NextVersionIdMarker"))
        if (
            not all(isinstance(item, str) and item for item in cursor)
            or cursor in cursors
        ):
            raise Unknown("version_pagination_incomplete")
        cursors.add(cursor)
        kwargs.update(KeyMarker=cursor[0], VersionIdMarker=cursor[1])
    raise Unknown("version_page_limit")


def present(client, bucket, key, version):
    try:
        result = client.head_object(Bucket=bucket, Key=key, VersionId=version)
    except ClientError as error:
        detail = error.response
        if detail.get("ResponseMetadata", {}).get(
            "HTTPStatusCode"
        ) == 404 and detail.get("Error", {}).get("Code") in {
            "404",
            "NoSuchKey",
            "NoSuchVersion",
            "NotFound",
        }:
            return False
        raise Unknown("head_unknown") from None
    except Exception:  # noqa: BLE001 - transport details must stay private
        raise Unknown("head_unknown") from None
    if result.get("VersionId") != version or "ContentLength" not in result:
        raise Unknown("head_version_unproven")
    return True


def verify_absence(client, bucket, frozen):
    if not frozen or len(frozen) > MAX_KEYS:
        raise Unknown("reference_set_missing_or_large")
    checked, remaining, receipts = 0, False, []
    for key, versions in frozen.items():
        if not versions or len(versions) > MAX_VERSIONS:
            raise Unknown("version_set_missing_or_large")
        current = inventory(client, bucket, key)
        remaining |= bool(current)
        for version in sorted(set(versions) | set(current)):
            exists = present(client, bucket, key, version)
            remaining |= exists
            checked += 1
            if checked > MAX_VERSIONS:
                raise Unknown("total_version_limit")
            receipts.append(
                {
                    "key_sha256": sha(key.encode()),
                    "version_sha256": sha(version.encode()),
                    "exists": exists,
                }
            )
    return {
        "status": "remaining" if remaining else "absent",
        "checked_versions": checked,
        "objects": receipts,
    }


class Repository:
    def __init__(self, dsn):
        validate_control_dsn(dsn, purpose="app")
        self.dsn = dsn
        self.identity = sha(dsn.encode())

    def connection(self):
        return psycopg.connect(
            self.dsn,
            connect_timeout=3,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=3000 -c lock_timeout=1000",
        )

    def read(self, owner, aid, uid):
        with self.connection() as connection:
            connection.execute(
                "set transaction isolation level repeatable read read only"
            )
            base = connection.execute(
                "select attachment_id,owner_internal_user_id,object_ref_ciphertext,object_ref_key_version,immutable_locator,size_bytes,sha256,state from platform_attachments.attachments where attachment_id=%s and owner_internal_user_id=%s",
                (aid, owner),
            ).fetchone()
            if base is None:
                raise Unknown("owner_attachment_unproven")
            uploads = connection.execute(
                "select upload_id,write_attempt_id,object_ref_ciphertext,object_ref_key_version,immutable_locator from platform_attachments.uploads where attachment_id=%s and owner_internal_user_id=%s limit 33",
                (aid, owner),
            ).fetchall()
            if len(uploads) != 1 or str(uploads[0]["upload_id"]) != uid:
                raise Unknown("upload_binding_unproven")
            attempts = connection.execute(
                "select attempt_id,object_ref_ciphertext,object_ref_key_version from platform_attachments.upload_write_attempts where attachment_id=%s and owner_internal_user_id=%s limit 33",
                (aid, owner),
            ).fetchall()
            # Attachment owner was proven in this same repeatable-read snapshot.
            derivatives = connection.execute(
                "select derivative_id,object_ref_ciphertext,object_ref_key_version from platform_attachments.derivatives where attachment_id=%s limit 33",
                (aid,),
            ).fetchall()
            artifacts = connection.execute(
                "select artifact_version_id from platform_attachments.artifact_versions where attachment_id=%s limit 1",
                (aid,),
            ).fetchall()
            active = connection.execute(
                "select count(*) as count from platform_attachments.processing_jobs where attachment_id=%s and state in ('queued','running')",
                (aid,),
            ).fetchone()["count"]
            jobs = connection.execute(
                "select erasure_job_id,requested_by_internal_user_id,state,completed_at from platform_attachments.erasure_jobs where attachment_id=%s limit 2",
                (aid,),
            ).fetchall()
        if artifacts:
            raise Unknown("upload_only_scope_artifact_present")
        if active:
            raise Unknown("processing_active")
        if len(attempts) + len(derivatives) + 2 > MAX_KEYS:
            raise Unknown("reference_limit")
        return {
            "base": base,
            "uploads": uploads,
            "attempts": attempts,
            "derivatives": derivatives,
            "jobs": jobs,
            "row_ids": sorted(
                [str(row["attempt_id"]) for row in attempts]
                + [str(row["derivative_id"]) for row in derivatives]
            ),
        }


def references(codec, graph, aid):
    rows = [
        (
            attachment_object_subject(
                UUID(aid), graph["uploads"][0]["write_attempt_id"]
            ),
            graph["base"],
        )
    ]
    rows += [
        (attachment_object_subject(UUID(aid), row["write_attempt_id"]), row)
        for row in graph["uploads"]
    ]
    rows += [
        (attachment_object_subject(UUID(aid), row["attempt_id"]), row)
        for row in graph["attempts"]
    ]
    rows += [
        (f"attachment:{aid}:derivative:{row['derivative_id']}:object-ref", row)
        for row in graph["derivatives"]
    ]
    result = {}
    for subject, row in rows:
        value = codec.unseal_json(
            subject,
            SealedContent(
                bytes(row["object_ref_ciphertext"]), row["object_ref_key_version"]
            ),
        )
        if (
            set(value) != {"object_ref"}
            or not isinstance(value["object_ref"], str)
            or not value["object_ref"]
        ):
            raise Unknown("reference_invalid")
        result.setdefault(value["object_ref"], set())
        locator = row.get("immutable_locator")
        if locator:
            if not locator.startswith("version:"):
                raise Unknown("version_locator_required")
            result[value["object_ref"]].add(locator[len("version:") :])
    return result


def binding(ledger, config):
    try:
        run, aid, uid = (
            str(UUID(ledger[key])) for key in ("run_id", "attachment_id", "upload_id")
        )
        prepared = ledger["prepared"]
        upload = ledger["operations"]["upload_begin"]["response"]
        raw = (
            "纯合成附件擦除验收；不包含真实人员或业务材料。\n"
            + run
            + "\n虚构样例设备：甲、乙、丙。仅用于一次性上传和擦除验证。\n"
        ).encode()
        if (
            ledger.get("kind") != "attachment_erasure"
            or ledger["account"]
            != {
                "internal_user_id": config["owner_id"],
                "role": "platform_owner",
                "hard_stale_read_only": False,
            }
            or upload["attachment_id"] != aid
            or upload["upload_id"] != uid
            or prepared["content_sha256"] != sha(raw)
            or prepared["metadata"]["attachment_id"] != aid
            or prepared["metadata"]["size_bytes"] != len(raw)
            or prepared["metadata_sha256"]
            != sha(json.dumps(prepared["metadata"], sort_keys=True).encode())
        ):
            raise Unknown("prepared_binding_invalid")
        return {
            "run_id": run,
            "attachment_id": aid,
            "upload_id": uid,
            "owner_id": config["owner_id"],
            "content_sha256": sha(raw),
            "size_bytes": len(raw),
        }
    except (KeyError, TypeError, ValueError):
        raise Unknown("prepared_binding_invalid") from None


def authenticate(client, config, aid, phase):
    headers = {
        "Cookie": "__Host-platform_session=" + config["session_cookie"],
        "Origin": config["public_origin"],
        "X-CSRF-Token": config["csrf"],
    }
    account = client.request(
        "GET",
        config["api_base_url"] + "/api/v1/account",
        headers=headers,
        timeout=5,
        follow_redirects=False,
    )
    if account.status_code != 200:
        raise Unknown("owner_http_denied")
    value = account.json()
    if any(
        value.get(key) != expected
        for key, expected in {
            "internal_user_id": config["owner_id"],
            "role": "platform_owner",
            "hard_stale_read_only": False,
        }.items()
    ):
        raise Unknown("owner_http_denied")
    response = client.request(
        "GET",
        config["api_base_url"] + "/api/v1/attachments/" + aid,
        headers=headers,
        timeout=5,
        follow_redirects=False,
    )
    if response.status_code != (200 if phase == "before" else 404):
        raise Unknown("attachment_http_state_unproven")
    if phase == "before" and (
        response.json().get("attachment_id") != aid
        or response.json().get("state") != "ready"
    ):
        raise Unknown("attachment_http_state_unproven")


def observe(phase, ledger, config, repository, codec, client, bucket, snapshot=None):
    selected = binding(ledger, config)
    aid = selected["attachment_id"]
    graph = repository.read(selected["owner_id"], aid, selected["upload_id"])
    if client.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
        raise Unknown("enabled_versioning_required")
    storage_identity = sha((client.meta.endpoint_url + "\n" + bucket).encode())
    subject = "attachment-erasure-observer:" + selected["run_id"]
    if phase == "before":
        if (
            graph["base"]["state"] != "ready"
            or graph["jobs"]
            or "erase" in ledger["operations"]
        ):
            raise Unknown("before_state_invalid")
        if (
            graph["base"]["size_bytes"] != selected["size_bytes"]
            or bytes(graph["base"]["sha256"]).hex() != selected["content_sha256"]
        ):
            raise Unknown("database_content_binding_invalid")
        frozen = {}
        for key, locators in references(codec, graph, aid).items():
            versions = inventory(client, bucket, key)
            if not versions or not locators.issubset(set(versions)):
                raise Unknown("before_versions_missing")
            if not all(present(client, bucket, key, version) for version in versions):
                raise Unknown("before_version_unavailable")
            frozen[key] = versions
            if sum(map(len, frozen.values())) > MAX_VERSIONS:
                raise Unknown("total_version_limit")
        if not frozen:
            raise Unknown("before_references_missing")
        payload = {
            "binding": selected,
            "bucket": bucket,
            "storage_identity": storage_identity,
            "database_identity": repository.identity,
            "observer_sha256": sha(Path(__file__).read_bytes()),
            "references": frozen,
            "row_ids": graph["row_ids"],
            "created_at": time.time(),
        }
        sealed = codec.seal_json(subject, payload)
        snapshot = {
            "ciphertext": base64.b64encode(sealed.ciphertext).decode(),
            "key_version": sealed.key_version,
        }
        return {
            "status": "captured",
            "physical_erasure_verified": False,
            "reference_count": len(frozen),
            "data_version_count": sum(map(len, frozen.values())),
            "artifact_version_count": 0,
            "active_processing_job_count": 0,
            "objects": [
                {
                    "key_sha256": sha(key.encode()),
                    "version_sha256": sha(version.encode()),
                    "exists": True,
                }
                for key, versions in frozen.items()
                for version in versions
            ],
        }, snapshot
    payload = codec.unseal_json(
        subject,
        SealedContent(
            base64.b64decode(snapshot["ciphertext"], validate=True),
            snapshot["key_version"],
        ),
    )
    if (
        payload["binding"] != selected
        or payload["bucket"] != bucket
        or payload["storage_identity"] != storage_identity
        or payload["database_identity"] != repository.identity
        or payload["observer_sha256"] != sha(Path(__file__).read_bytes())
        or payload["row_ids"] != graph["row_ids"]
    ):
        raise Unknown("snapshot_scope_changed")
    result = verify_absence(client, bucket, payload["references"])
    jobs = graph["jobs"]
    completed = (
        len(jobs) == 1
        and jobs[0]["state"] == "completed"
        and jobs[0]["completed_at"] is not None
        and jobs[0]["completed_at"].timestamp() >= payload["created_at"]
        and str(jobs[0]["requested_by_internal_user_id"]) == selected["owner_id"]
        and graph["base"]["state"] == "deleted"
    )
    result.update(
        job_states=[row["state"] for row in jobs],
        job_id_sha256=[sha(str(row["erasure_job_id"]).encode()) for row in jobs],
        artifact_version_count=0,
        active_processing_job_count=0,
        physical_erasure_verified=result["status"] == "absent" and completed,
    )
    if not completed:
        result["status"] = "job_not_completed"
    return result, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before", "after"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    receipt = {
        "status": "unknown",
        "physical_erasure_verified": False,
        "phase": args.phase,
        "observer_sha256": sha(Path(__file__).read_bytes()),
    }
    output = Path(args.receipt)
    try:
        if not output.is_absolute() or output.exists():
            raise Unknown("new_absolute_receipt_required")
        frozen_path = Path(args.snapshot)
        if not frozen_path.is_absolute() or (
            args.phase == "before" and frozen_path.exists()
        ):
            raise Unknown("new_absolute_snapshot_required")

        class Invocation:
            def request(self, *unused, **kwargs):
                config = load_config(args.config)
                ledger = private_json(args.ledger)
                selected = binding(ledger, config)
                codec = _build_content_codec()
                path = Path(os.environ.get("PLATFORM_CONTROL_DATABASE_URL_FILE", ""))
                if not path.is_absolute():
                    raise Unknown("app_database_file_required")
                repository = Repository(read_secret_file(str(path)))
                bucket = os.environ.get("PLATFORM_ATTACHMENT_S3_BUCKET", "")
                if not bucket:
                    raise Unknown("bucket_required")
                with httpx.Client() as http:
                    authenticate(http, config, selected["attachment_id"], args.phase)
                result, frozen = observe(
                    args.phase,
                    ledger,
                    config,
                    repository,
                    codec,
                    _build_s3_client(),
                    bucket,
                    private_json(frozen_path) if args.phase == "after" else None,
                )
                if frozen is not None:
                    write_private(frozen_path, frozen)
                result.update(
                    snapshot_sha256=sha(frozen_path.read_bytes()),
                    ledger_sha256=sha(Path(args.ledger).read_bytes()),
                )
                return result

        receipt.update(bounded_request(Invocation(), timeout=60))
    except (Exception, RequestDeadline):  # noqa: BLE001 - fail unknown without secrets
        # Never serialize SDK, SQL, HTTP or decryption exception messages.
        receipt.update(status="unknown", physical_erasure_verified=False)
    if output.is_absolute() and not output.exists():
        write_private(output, receipt)
    print(json.dumps(receipt, sort_keys=True))
    return (
        0
        if receipt["status"] == "captured" or receipt["physical_erasure_verified"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
