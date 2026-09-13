"""Bounded public/synthetic HTTP canary. No credential issuance or service control.

Requires an existing owner session in an absolute, regular 0600 JSON file.
See api-canary-engineering/README.md before explicitly executing against production.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx


class CanaryError(Exception):
    """Fixed safe diagnosis, never raw server exceptions or credential values."""


def private_json(path):
    path = Path(path)
    if not path.is_absolute():
        raise CanaryError("private_file_required")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > 2_000_000
            ):
                raise CanaryError("private_file_required")
            value = json.load(stream)
        if not isinstance(value, dict):
            raise CanaryError("private_file_required")
        return value
    except (OSError, ValueError):
        raise CanaryError("private_file_required") from None


def load_config(path):
    value = private_json(path)
    fields = ("owner_id", "session_cookie", "csrf", "public_origin", "api_base_url")
    if any(not isinstance(value.get(k), str) or not value[k] for k in fields):
        raise CanaryError("credentials_required")
    try:
        value["owner_id"] = str(UUID(value["owner_id"]))
    except ValueError:
        raise CanaryError("owner_invalid") from None
    for key in ("session_cookie", "csrf"):
        if any(ord(c) < 33 or ord(c) > 126 or c in ';,"\\' for c in value[key]):
            raise CanaryError("credential_invalid")
    for key in ("public_origin", "api_base_url"):
        parsed = urlsplit(value[key])
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise CanaryError("https_origin_required")
        value[key] = value[key].rstrip("/")
    if value["api_base_url"] != value["public_origin"]:
        raise CanaryError("same_origin_required")
    return {k: value[k] for k in fields}


def sha(value):
    return hashlib.sha256(value).hexdigest()


def write_private(path, value):
    data = json.dumps(value, ensure_ascii=False, indent=2).encode() + b"\n"
    temp = path.with_name(path.name + "." + str(uuid4()))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


class Canary:
    def __init__(self, config, directory, client, *, resume=False):
        self.config, self.client = config, client
        self.directory = Path(directory)
        if not self.directory.is_absolute():
            raise CanaryError("absolute_evidence_directory_required")
        if resume:
            if self.directory.is_symlink():
                raise CanaryError("evidence_directory_invalid")
            self.ledger = private_json(self.directory / "ledger.json")
            if any(
                self.ledger.get(k) != config[k] for k in ("owner_id", "public_origin")
            ):
                raise CanaryError("ledger_identity_mismatch")
        else:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
            self.ledger = {
                "run_id": str(uuid4()),
                "owner_id": config["owner_id"],
                "public_origin": config["public_origin"],
                "deadline": time.time() + 900,
                "operations": {},
                "http": [],
                "turns": [],
                "status": "created",
            }
            self.save()

    def save(self):
        write_private(self.directory / "ledger.json", self.ledger)

    def request(
        self, method, path, *, key=None, body=None, content=None, accepted=(200,)
    ):
        remaining = self.ledger["deadline"] - time.time()
        if remaining <= 0:
            raise CanaryError("deadline_exhausted")
        if not path.startswith("/api/") or ".." in path or "?" in path or "#" in path:
            raise CanaryError("request_path_invalid")
        request_id = str(uuid4())
        receipt = {
            "request_id": request_id,
            "method": method,
            "path": path,
            "status": "sending",
        }
        self.ledger["http"].append(receipt)
        self.save()
        headers = {
            "Cookie": "__Host-platform_session=" + self.config["session_cookie"],
            "Origin": self.config["public_origin"],
            "X-CSRF-Token": self.config["csrf"],
            "X-Request-ID": request_id,
        }
        if key:
            headers["Idempotency-Key"] = key
        if content is not None:
            headers.update(
                {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(content)),
                }
            )
        try:
            response = self.client.request(
                method,
                self.config["api_base_url"] + path,
                headers=headers,
                json=body,
                content=content,
                timeout=min(20, remaining),
                follow_redirects=False,
            )
        except httpx.HTTPError:
            receipt["status"] = "transport_unknown"
            self.save()
            raise CanaryError("outcome_unknown") from None
        receipt["status"] = response.status_code
        # Caller-generated UUID is always retained; server request IDs only if UUID.
        try:
            receipt["server_request_id"] = str(
                UUID(response.headers.get("X-Request-ID", ""))
            )
        except ValueError:
            pass
        self.save()
        if response.status_code not in accepted:
            raise CanaryError("http_" + str(response.status_code))
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise TypeError
            return value
        except (ValueError, TypeError):
            raise CanaryError("response_invalid") from None

    def mutate(
        self, name, path, body, accepted, *, method="POST", content=None, replay=False
    ):
        operations = self.ledger["operations"]
        if name not in operations:
            operations[name] = {
                "key": str(uuid4()),
                "path": path,
                "body": body,
                "content_sha256": sha(content) if content is not None else None,
                "method": method,
                "status": "prepared",
            }
            self.save()
        op = operations[name]
        if (op["path"], op["body"], op["method"], op["content_sha256"]) != (
            path,
            body,
            method,
            sha(content) if content is not None else None,
        ):
            raise CanaryError("operation_mismatch")
        if op["status"] == "received" and not replay:
            return op["response"]
        if op["status"] != "prepared" and not (replay and name in {"work", "input"}):
            raise CanaryError("outcome_unknown")
        op["status"] = "sending"
        self.save()
        try:
            value = self.request(
                method,
                path,
                key=op["key"],
                body=body,
                content=content,
                accepted=accepted,
            )
        except CanaryError:
            op["status"] = "outcome_unknown"
            self.save()
            raise
        op.update(status="received", response=value)
        self.save()
        return value

    def preflight(self, *, admission=True):
        account = self.request("GET", "/api/v1/account")
        safe_account = {
            k: account.get(k)
            for k in ("internal_user_id", "role", "hard_stale_read_only")
        }
        self.ledger["account"] = safe_account
        self.save()
        if safe_account != {
            "internal_user_id": self.config["owner_id"],
            "role": "platform_owner",
            "hard_stale_read_only": False,
        }:
            raise CanaryError("owner_identity_denied")
        readiness = self.request(
            "GET", "/api/v1/manage/hr-readiness", accepted=(200, 503)
        )
        self.ledger.setdefault("readiness_observations", []).append(readiness)
        self.save()
        if not readiness.get("ready") or (
            admission
            and (
                readiness.get("phase") != "cloud"
                or not readiness.get("new_admission_enabled")
            )
        ):
            raise CanaryError("hr_readiness_denied")
        identity = {
            key: readiness.get(key)
            for key in (
                "release_sha",
                "configuration_sha256",
                "runtime_identity_sha256",
                "knowledge_sha256",
                "knowledge_release",
            )
        }
        if not all(isinstance(value, str) and value for value in identity.values()):
            raise CanaryError("release_identity_absent")
        if (
            "release_identity" in self.ledger
            and self.ledger["release_identity"] != identity
        ):
            raise CanaryError("release_identity_changed")
        self.ledger["release_identity"] = identity
        configuration = self.request("GET", "/api/hr/agent/configuration")
        limits = configuration.get("budget_limits", {})
        ceilings = {"model_calls": 32, "total_tokens": 600000, "active_seconds": 900}
        if not configuration.get("budget_profile") or any(
            type(limits.get(k)) is not int or not 0 < limits[k] <= v
            for k, v in ceilings.items()
        ):
            raise CanaryError("budget_limits_unproven_or_excessive")
        evidence = {
            "source": "authenticated_configuration_api",
            "budget_profile": configuration["budget_profile"],
            "limits": limits,
            "configuration_sha256": identity["configuration_sha256"],
        }
        if (
            "budget_evidence" in self.ledger
            and self.ledger["budget_evidence"] != evidence
        ):
            raise CanaryError("budget_identity_changed")
        self.ledger["budget_evidence"] = evidence
        self.save()
        return configuration

    def synthetic(self):
        return (
            "纯合成上线验收材料；不涉及任何真实公司、员工或候选人。\n"
            + "本次唯一标记："
            + self.ledger["run_id"]
            + "\n"
            + "虚构的星光样例实验室设立一个虚构的文档整理岗位：整理虚构设备说明、校对格式、维护目录。"
            + "当前三名虚构使用者每周各提交两篇样例说明；每篇校对耗时十分钟。"
            + "计划将每篇耗时降至五分钟；这是合成假设，不是已验证效果。"
            + "请只使用本文材料做简短工作需求研究，区分已给条件和待验证假设；不搜索外网、不处理个人材料。\n"
        ).encode()

    def owned_work(self):
        work_id = self.ledger.get("work_id")
        try:
            work_id = str(UUID(work_id))
        except (ValueError, TypeError, AttributeError):
            raise CanaryError("owned_work_absent") from None
        op = self.ledger["operations"].get("work", {})
        if op.get("response", {}).get("work_id") != work_id or self.ledger[
            "run_id"
        ] not in op.get("body", {}).get("text", ""):
            raise CanaryError("owned_work_unproven")
        messages = self.request("GET", "/api/hr/agent/works/" + work_id + "/messages")
        if not any(
            item.get("kind") == "user"
            and self.ledger["run_id"] in str(item.get("body", ""))
            for item in messages.get("items", [])
        ):
            raise CanaryError("owned_work_unproven")
        return work_id, messages

    def upload(self):
        raw = self.synthetic()
        body = {
            "conversation_id": None,
            "original_name": "synthetic-canary-" + self.ledger["run_id"] + ".txt",
            "declared_mime": "text/plain",
            "declared_size": len(raw),
        }
        upload = self.mutate(
            "upload_begin", "/api/v1/attachments/uploads", body, (201,)
        )
        try:
            upload_id, attachment_id = (
                str(UUID(upload[key])) for key in ("upload_id", "attachment_id")
            )
        except (KeyError, ValueError, TypeError):
            raise CanaryError("upload_identity_invalid") from None
        self.ledger.update(upload_id=upload_id, attachment_id=attachment_id)
        self.save()
        self.mutate(
            "upload_content",
            "/api/v1/attachments/uploads/" + upload_id + "/content",
            None,
            (200,),
            method="PUT",
            content=raw,
        )
        self.mutate(
            "upload_complete",
            "/api/v1/attachments/uploads/" + upload_id + "/complete",
            None,
            (200,),
        )
        while True:
            material = self.request("GET", "/api/hr/agent/materials/" + attachment_id)
            self.ledger["material"] = material
            self.save()
            if material.get("state") == "ready":
                if (
                    material.get("visibility", {}).get("subject_id")
                    != self.config["owner_id"]
                    or material.get("original_ref", {}).get("revision") != sha(raw)
                    or not material.get("text_ref")
                ):
                    raise CanaryError("material_identity_mismatch")
                return material["text_ref"]
            if material.get("state") in {
                "failed",
                "rejected",
                "deleted",
                "unavailable",
            }:
                raise CanaryError("material_not_ready")
            self.sleep()

    def sleep(self):
        remaining = self.ledger["deadline"] - time.time()
        if remaining <= 0:
            raise CanaryError("deadline_exhausted")
        time.sleep(min(2, remaining))

    def snapshot(self):
        work_id, messages = self.owned_work()
        work = self.request("GET", "/api/hr/agent/works/" + work_id)
        if work.get("work_id") != work_id:
            raise CanaryError("work_identity_mismatch")
        results = []
        for ref in work.get("result_refs", []):
            try:
                result_id = str(UUID(ref["id"]))
                revision = str(UUID(ref["revision"]))
            except (ValueError, KeyError, TypeError):
                raise CanaryError("result_reference_invalid") from None
            result = self.request(
                "GET", "/api/hr/agent/results/" + result_id + "/revisions/" + revision
            )
            if result.get("ref") != ref:
                raise CanaryError("result_reference_mismatch")
            results.append(result)
        record = {
            "observed_at": time.time(),
            "work": work,
            "messages": messages,
            "results": results,
        }
        self.ledger["latest"] = record
        self.save()
        return record

    def observe(self, label="manual"):
        if label not in {"manual", "before_external_restart", "after_external_restart"}:
            raise CanaryError("observation_label_invalid")
        self.preflight(admission=False)
        record = self.snapshot()
        self.ledger.setdefault("observations", []).append({"label": label, **record})
        # An API state transition alone does not prove a worker was restarted.
        self.save()
        return record

    def wait_result(self, *, pause_at_running=False):
        while True:
            record = self.snapshot()
            state = record["work"]["state"]
            if pause_at_running and state == "running":
                self.ledger.update(
                    status="paused_for_external_restart",
                    restart_observation="running_window_observed",
                )
                self.ledger.setdefault("observations", []).append(
                    {"label": "before_external_restart", **record}
                )
                self.save()
                return None
            if state == "completed":
                if not record["results"]:
                    raise CanaryError("saved_result_absent")
                if pause_at_running:
                    self.ledger["restart_observation"] = "restart_window_missed"
                return record
            if state in {"failed", "cancelled", "waiting_user", "waiting_budget"}:
                raise CanaryError("work_" + state)
            if state not in {"queued", "running"}:
                raise CanaryError("work_state_unexpected")
            self.sleep()

    def run(self, *, pause_at_running=False):
        try:
            configuration = self.preflight()
            self.ledger.setdefault("restart_observation", "not_requested")
            if self.ledger.get("status") == "completed":
                self.snapshot()
                return self.ledger
            ref = self.upload()
            body = {
                "thread_id": None,
                "text": "纯合成 API canary "
                + self.ledger["run_id"]
                + "。请读取附件，以附件的虚构岗位为对象保存一份简短 research 成果，列明工作需求与假设。禁止外网和个人材料。",
                "objects": [],
                "references": [ref],
                "budget_profile": configuration["budget_profile"],
            }
            # Work and input endpoints implement UUID idempotency. Repeating the same
            # body/key can resolve lost replies; attachment initialization cannot.
            work = self.mutate(
                "work", "/api/hr/agent/works", body, (200, 201), replay=True
            )
            replayed_work = self.mutate(
                "work", "/api/hr/agent/works", body, (200,), replay=True
            )
            if replayed_work.get("work_id") != work.get("work_id"):
                raise CanaryError("work_replay_mismatch")
            self.ledger["work_id"] = str(UUID(work["work_id"]))
            self.save()
            if not self.ledger["turns"]:
                first = self.wait_result(pause_at_running=pause_at_running)
                if first is None:
                    return self.ledger
                self.ledger["turns"].append(first)
                self.save()
            first = self.ledger["turns"][0]
            work_id, _ = self.owned_work()
            body = {
                "expected_input_revision": first["work"]["input_revision"],
                "question_id": None,
                "text": "纯合成 API canary "
                + self.ledger["run_id"]
                + "：在刚才同一份 research 成果中补充一个可验证的验收指标，并保存同一成果的新修订。其他合成条件与材料保持不变；不得外网查询。",
                "objects": [],
                "references": [ref, *first["work"]["result_refs"]],
            }
            path = "/api/hr/agent/works/" + work_id + "/inputs"
            updated = self.mutate("input", path, body, (202,), replay=True)
            replayed = self.mutate("input", path, body, (202,), replay=True)
            if (
                updated["work_id"] != work_id
                or replayed["work_id"] != work_id
                or replayed["input_revision"] != first["work"]["input_revision"] + 1
            ):
                raise CanaryError("input_replay_mismatch")
            second = self.wait_result()
            before = {
                ref["id"]: ref["revision"] for ref in first["work"]["result_refs"]
            }
            after = {
                ref["id"]: ref["revision"] for ref in second["work"]["result_refs"]
            }
            if not all(
                key in after and after[key] != revision
                for key, revision in before.items()
            ):
                raise CanaryError("saved_revision_absent")
            self.ledger["turns"] = [first, second]
            self.ledger["status"] = "completed"
            self.save()
            return self.ledger
        except CanaryError as error:
            self.ledger.update(status="stopped", failure=str(error))
            self.save()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "resume", "observe"))
    parser.add_argument("--private-config", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--pause-at-running", action="store_true")
    parser.add_argument(
        "--label",
        choices=("manual", "before_external_restart", "after_external_restart"),
        default="manual",
    )
    args = parser.parse_args(argv)
    try:
        config = load_config(args.private_config)
        with httpx.Client(
            trust_env=False, verify=True, follow_redirects=False
        ) as client:
            runner = Canary(
                config, args.evidence_dir, client, resume=args.command != "run"
            )
            if args.command == "observe":
                runner.observe(args.label)
            else:
                runner.run(pause_at_running=args.pause_at_running)
        print(
            json.dumps(
                {"status": runner.ledger["status"], "run_id": runner.ledger["run_id"]}
            )
        )
        return 0
    except CanaryError as error:
        print(json.dumps({"status": "stopped", "reason": str(error)}))
        return 1
    except Exception:  # noqa: BLE001 - suppress credential-bearing library exceptions
        # No traceback: transports and libraries can embed credentials in errors.
        print(json.dumps({"status": "stopped", "reason": "unexpected_local_failure"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
