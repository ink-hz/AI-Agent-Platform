"""Account/attachment-only canary. HTTP deletion is NOT physical object erasure."""
from __future__ import annotations

import argparse
import json
import time
from uuid import UUID, uuid4

import httpx
from api_canary import Canary, CanaryError, load_config, sha


class AttachmentCanary(Canary):
    """Reuse private credentials/journal only; no HR endpoint or runtime dependency."""

    def __init__(self, *args, resume=False, **kwargs):
        super().__init__(*args, resume=resume, **kwargs)
        self.readonly_until = None
        if resume:
            if self.ledger.get("kind") != "attachment_erasure":
                raise CanaryError("attachment_ledger_required")
        else:
            self.ledger.update(kind="attachment_erasure", deadline=time.time() + 300)
            self.save()

    def request(self, method, path, *, key=None, body=None, content=None, accepted=(200,)):
        remaining = (self.readonly_until if self.readonly_until is not None else self.ledger["deadline"]) - time.time()
        if remaining <= 0:
            raise CanaryError("deadline_exhausted")
        if self.readonly_until is not None and method != "GET":
            raise CanaryError("readonly_observation")
        if path != "/api/v1/account" and not path.startswith("/api/v1/attachments/"):
            raise CanaryError("attachment_endpoint_required")
        if any(part in path for part in ("..", "?", "#")):
            raise CanaryError("request_path_invalid")
        receipt = {"request_id": str(uuid4()), "method": method, "path": path, "status": "sending"}
        self.ledger["http"].append(receipt)
        self.save()
        headers = {"Cookie": "__Host-platform_session=" + self.config["session_cookie"],
                   "Origin": self.config["public_origin"], "X-CSRF-Token": self.config["csrf"],
                   "X-Request-ID": receipt["request_id"]}
        if key:
            headers["Idempotency-Key"] = key
        if content is not None:
            headers.update({"Content-Type": "application/octet-stream", "Content-Length": str(len(content))})
        try:
            response = self.client.request(method, self.config["api_base_url"] + path, headers=headers,
                json=body, content=content, timeout=min(20, remaining), follow_redirects=False)
        except httpx.HTTPError:
            receipt["status"] = "transport_unknown"
            self.save()
            raise CanaryError("outcome_unknown") from None
        receipt["status"] = response.status_code
        self.save()
        if response.status_code not in accepted:
            raise CanaryError("http_" + str(response.status_code))
        if response.status_code in (204, 404):
            return {"http_status": response.status_code}
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise TypeError
            return value
        except (ValueError, TypeError):
            raise CanaryError("response_invalid") from None

    def identity(self):
        account = self.request("GET", "/api/v1/account")
        safe = {key: account.get(key) for key in ("internal_user_id", "role", "hard_stale_read_only")}
        self.ledger["account"] = safe
        self.save()
        if safe != {"internal_user_id": self.config["owner_id"], "role": "platform_owner", "hard_stale_read_only": False}:
            raise CanaryError("owner_identity_denied")

    def synthetic(self):
        return ("纯合成附件擦除验收；不包含真实人员或业务材料。\n"
                + self.ledger["run_id"] + "\n虚构样例设备：甲、乙、丙。仅用于一次性上传和擦除验证。\n").encode()

    def validate_metadata(self, metadata):
        original = self.ledger["operations"]["upload_begin"].get("response", {})
        try:
            aid = str(UUID(original["attachment_id"]))
        except (KeyError, ValueError, TypeError):
            raise CanaryError("owned_attachment_unproven") from None
        if (self.ledger.get("attachment_id") != aid or metadata.get("attachment_id") != aid
            or metadata.get("original_name") != "synthetic-erasure-" + self.ledger["run_id"] + ".txt"
            or metadata.get("size_bytes") != len(self.synthetic()) or metadata.get("declared_mime") != "text/plain"):
            raise CanaryError("owned_attachment_unproven")
        return aid

    def prepare(self):
        self.identity()
        if "erase" in self.ledger["operations"]:
            raise CanaryError("erasure_already_requested")
        raw = self.synthetic()
        body = {"conversation_id": None, "original_name": "synthetic-erasure-" + self.ledger["run_id"] + ".txt",
                "declared_mime": "text/plain", "declared_size": len(raw)}
        upload = self.mutate("upload_begin", "/api/v1/attachments/uploads", body, (201,))
        try:
            uid, aid = (str(UUID(upload[k])) for k in ("upload_id", "attachment_id"))
        except (ValueError, KeyError, TypeError):
            raise CanaryError("upload_identity_invalid") from None
        self.ledger.update(upload_id=uid, attachment_id=aid)
        self.save()
        self.mutate("upload_content", "/api/v1/attachments/uploads/" + uid + "/content", None, (200,), method="PUT", content=raw)
        self.mutate("upload_complete", "/api/v1/attachments/uploads/" + uid + "/complete", None, (200,))
        while True:
            metadata = self.request("GET", "/api/v1/attachments/" + aid)
            self.validate_metadata(metadata)
            self.ledger["latest_metadata"] = metadata
            self.save()
            if metadata.get("state") == "ready":
                self.ledger.update(status="prepared", prepared={"metadata": metadata, "content_sha256": sha(raw),
                    "metadata_sha256": sha(json.dumps(metadata, sort_keys=True).encode()),
                    "hash_boundary": "content hash is bytes sent; HTTP metadata has no server byte hash"})
                self.save()
                return self.ledger
            if metadata.get("state") not in {"uploaded", "quarantined", "scanning", "processing", "pending"}:
                raise CanaryError("attachment_not_ready")
            self.sleep()

    def erase(self):
        if self.ledger["deadline"] <= time.time():
            raise CanaryError("deadline_exhausted")
        self.identity()
        op = self.ledger["operations"].get("erase")
        if op:
            if op["status"] != "received":
                raise CanaryError("outcome_unknown")
            return self.ledger
        prepared = self.ledger.get("prepared")
        if not prepared or prepared.get("content_sha256") != sha(self.synthetic()) or prepared.get("metadata_sha256") != sha(json.dumps(prepared["metadata"], sort_keys=True).encode()):
            raise CanaryError("prepared_attachment_required")
        aid = self.validate_metadata(prepared["metadata"])
        metadata = self.request("GET", "/api/v1/attachments/" + aid)
        self.validate_metadata(metadata)
        if metadata.get("state") != "ready":
            raise CanaryError("attachment_not_ready")
        self.mutate("erase", "/api/v1/attachments/" + aid, None, (204,), method="DELETE")
        self.ledger["status"] = "awaiting_external_erasure_evidence"
        self.save()
        return self.ledger

    def observe(self):
        # This read-only window does not renew the original mutation deadline.
        self.readonly_until = time.time() + 20
        try:
            self.identity()
            prepared = self.ledger.get("prepared")
            if not prepared:
                raise CanaryError("prepared_attachment_required")
            aid = self.validate_metadata(prepared["metadata"])
            metadata = self.request("GET", "/api/v1/attachments/" + aid, accepted=(200, 404))
            if metadata.get("http_status") != 404:
                self.validate_metadata(metadata)
            self.ledger.setdefault("observations", []).append({"observed_at": time.time(), "metadata": metadata,
                "physical_erasure_verified": False})
            self.save()
            return metadata
        finally:
            self.readonly_until = None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "erase", "observe"))
    parser.add_argument("--private-config", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--resume-prepare", action="store_true")
    args = parser.parse_args(argv)
    runner = None
    try:
        config = load_config(args.private_config)
        with httpx.Client(trust_env=False, verify=True, follow_redirects=False) as client:
            runner = AttachmentCanary(config, args.evidence_dir, client, resume=args.command != "prepare" or args.resume_prepare)
            getattr(runner, args.command)()
        print(json.dumps({"status": runner.ledger["status"], "run_id": runner.ledger["run_id"], "physical_erasure_verified": False}))
        return 0
    except CanaryError as error:
        if runner:
            runner.ledger["failure"] = str(error)
            runner.save()
        print(json.dumps({"status": "stopped", "reason": str(error)}))
        return 1
    except Exception:  # noqa: BLE001 - never print credential-bearing library exceptions
        print(json.dumps({"status": "stopped", "reason": "unexpected_local_failure"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
