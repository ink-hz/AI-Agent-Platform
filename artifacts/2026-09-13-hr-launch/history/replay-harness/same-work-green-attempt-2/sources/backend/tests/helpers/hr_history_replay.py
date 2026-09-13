"""Bounded synthetic historical-intent replay through production HTTP/PG boundaries."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from app.hr_agent.model import ModelTransportError

ROOT = Path(__file__).resolve().parents[3]
CASE_IDS = ("H01", "H03", "H06", "H13")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def asset_bytes(root, manifest, name):
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("asset integrity: invalid path")
    entries = [asset for asset in manifest["assets"] if asset["path"] == name]
    if len(entries) != 1 or entries[0].get("version") != "synthetic-v2.1":
        raise ValueError("asset integrity: missing immutable version")
    target = Path(root)
    for part in relative.parts:
        target /= part
        if target.is_symlink():
            raise ValueError("asset integrity: symlink")
    raw = target.read_bytes()
    if digest(raw) != entries[0]["sha256"]:
        raise ValueError("asset integrity: digest mismatch")
    return raw


def previous_refs(turn, completed, *, required=True):
    dependency = turn["depends_on_turn"]
    if dependency is None:
        return []
    previous = completed.get(dependency)
    if previous is None or previous["state"] not in {"completed", "waiting_user"}:
        raise ValueError("previous result absent: preceding turn incomplete")
    if required and (not turn["prior_result_binding"] or not previous["result_refs"]):
        raise ValueError("previous result absent: cannot fabricate saved result")
    return list(previous["result_refs"])


def real_configuration(environment):
    try:
        if environment.get("HR_HISTORY_REAL_MODEL") != "1":
            raise ValueError()
        profile_path = Path(environment["HR_HISTORY_REAL_PROFILE_FILE"])
        output = Path(environment["HR_HISTORY_EVIDENCE_DIR"])
        if not profile_path.is_absolute() or profile_path.is_symlink() or stat.S_IMODE(profile_path.stat().st_mode) != 0o600:
            raise ValueError()
        if not output.is_absolute() or output.exists():
            raise ValueError()
        profile = json.loads(profile_path.read_text())
        if profile.get("model") != "claude-opus-5" or profile.get("protocol") != "anthropic_messages_sse":
            raise ValueError()
        profile = {**profile, "timeout_seconds": 300}
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "configuration.json", {
            "model": profile["model"], "protocol": profile["protocol"],
            "profile_sha256": digest(profile_path.read_bytes()),
            "max_generation_calls_shared": 96, "max_output_tokens": 16384,
            "max_call_seconds": 300, "boundary": "synthetic historical-intent adaptations; no candidate intake authority",
        })
        return profile, output
    except (KeyError, ValueError, OSError, TypeError):
        raise ValueError("explicit real replay profile and fresh evidence directory required") from None


class BoundedPort:
    def __init__(self, port):
        self.port = port
        self.calls = 0
        self.observations = []

    def stream(self, request):
        if self.calls >= 96 or not 0 < request.max_output_tokens <= 16384 or not 0 < request.deadline_seconds <= 300:
            raise ModelTransportError("configuration_unavailable")
        self.calls += 1
        observation = {"attempt_id": str(request.attempt_id), "usage": [], "stops": [], "complete": False}
        self.observations.append(observation)
        started = time.monotonic()
        try:
            for event in self.port.stream(request):
                if event.type in {"usage", "stop"}:
                    observation["usage" if event.type == "usage" else "stops"].append(event.payload)
                yield event
            observation["complete"] = True
        except ModelTransportError as error:
            observation["error_code"] = error.code
            raise
        finally:
            observation["seconds"] = time.monotonic() - started


@contextmanager
def environment(directory, profile=None):
    from app.agent_brain.authorization import AgentUseAuthorization
    from app.attachments.conversation_repository import ConversationAttachmentRepository
    from app.attachments.conversation_routes import build_conversation_attachment_router
    from app.attachments.upload_service import AttachmentUploadService
    from app.control_plane.auth import AuthSecrets, DingTalkWebAuth, WebSessionRepository
    from app.control_plane.authorization import AuthorizationRepository, AuthorizationService
    from app.control_plane.middleware import IdentitySecurityMiddleware
    from app.hr_agent.access import HrAccess
    from app.hr_agent.materials import MaterialService
    from app.hr_agent.resources import build_runtime_services
    from app.hr_agent.routes import build_hr_agent_router
    from app.hr_agent.service import HrAgentService
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tests.hr_agent_support import hr_agent_database, make_hr_settings
    from tests.test_hr_agent_materials import MemoryStore
    from tests.test_hr_agent_worker_process import grant_test_owner
    from tools.hr_agent.build_knowledge_release import build

    directory.mkdir(parents=True, exist_ok=False)
    settings = make_hr_settings(directory / "config")
    knowledge = directory / "knowledge"
    build(ROOT / "backend/hr_agent_knowledge", knowledge)
    budget = {**settings.budget_profile,
              "limits": {"model_calls": 32, "total_tokens": 1200000, "active_seconds": 1800},
              "input_target_tokens": 50000, "input_trigger_tokens": 70000,
              "max_output_tokens": 16384,
              "reserve": {"model_calls": 2, "total_tokens": 32768, "active_seconds": 30}}
    provider = profile or {**settings.provider_profile, "context_window_tokens": 131072}
    settings = replace(settings, knowledge_dir=knowledge, provider_profile=provider, budget_profile=budget)
    with hr_agent_database() as db:
        owner = grant_test_owner(db)
        other = uuid4()
        with db.admin_connection() as c:
            generation = c.execute("select active_generation_id from platform_control.directory_state where singleton").fetchone()[0]
            c.execute("update platform_control.internal_users set role='platform_owner', last_confirmed_generation_id=%s where internal_user_id=%s", (generation, owner))
            c.execute("insert into platform_control.internal_users(internal_user_id,display_name,status,last_confirmed_generation_id) values(%s,'Other synthetic owner','active',%s)", (other, generation))
            c.execute("insert into platform_control.directory_members(generation_id,member_key,internal_user_id,subject_kind,lookup_hmac,lookup_key_version,encrypted_provider_id,encryption_key_version,display_name,status) values(%s,%s,%s,'employee',%s,1,%s,1,'Other synthetic owner','active')", (generation, uuid4(), other, other.bytes*2, b'fixture'))
            c.execute("insert into platform_control.agent_use_grants(agent_use_grant_id,agent_id,target_kind,target_internal_user_id,created_by) values(%s,'hr-bot','user',%s,%s)", (uuid4(), other, owner))
        secrets = AuthSecrets(b"h" * 32, key_version=1)
        async def local_login(code, _verifier):
            return owner if code == "owner" else other
        auth = DingTalkWebAuth(repository=WebSessionRepository(db.dsn, secrets=secrets), secrets=secrets,
                              qr_login=local_login, in_client_login=None, environment="production", route_prefix="/",
                              public_base_url="https://localhost", app_key="owned-history-fixture")
        def login(code):
            started = auth.start_qr("/")
            return asyncio.run(auth.complete_qr(started.state, code)).session
        session, outsider = login("owner"), login("other")
        store = MemoryStore()
        codec = settings.create_codec()
        materials = MaterialService(db.connection, codec, store, temporary_root=settings.work_dir)
        access = AgentUseAuthorization(db.dsn)
        repo, resources = build_runtime_services(settings, db.connection, material_service=materials, agent_use_authorization=access)
        app = FastAPI()
        app.state.conversation_attachment_upload_service = AttachmentUploadService(ConversationAttachmentRepository(db.dsn, content_codec=codec), store)
        app.include_router(build_conversation_attachment_router())
        app.include_router(build_hr_agent_router(HrAgentService(repo, HrAccess(access), materials=materials, knowledge=resources.knowledge)))
        app.add_middleware(IdentitySecurityMiddleware, auth=auth, public_assets=frozenset(),
                           authorization=AuthorizationService(AuthorizationRepository(db.dsn)), routes=tuple(app.router.routes))
        with TestClient(app, base_url="https://localhost") as client, TestClient(app, base_url="https://localhost") as other_client:
            http_log = []
            def record_http(response):
                http_log.append({"method": response.request.method, "path": response.request.url.path, "status": response.status_code})
            client.event_hooks["response"].append(record_http)
            other_client.event_hooks["response"].append(record_http)
            anonymous = client.get("/api/hr/agent/configuration").status_code
            client.cookies.set(auth.cookie_name, session.cookie_token)
            client.cookies.set(auth.csrf_cookie_name, session.csrf_token)
            other_client.cookies.set(auth.cookie_name, outsider.cookie_token)
            headers = {"Origin": "https://localhost", "X-CSRF-Token": session.csrf_token}
            yield {"client": client, "other": other_client, "headers": headers, "repo": repo, "resources": resources,
                   "owner": owner, "database": db, "materials": materials, "store": store, "anonymous": anonymous, "http_log": http_log}


def replay_case(corpus_root, case_id, runtime_dir, output_dir, *, profile=None, port=None):
    from app.hr_agent.runtime import run_work
    from app.hr_agent.worker import run_worker
    from tests.test_hr_agent_b_public_model import _stage_evidence
    from tests.test_hr_agent_d_journey import upload_text
    from tests.test_hr_agent_runtime import ScriptModel, answer, tool

    if case_id not in CASE_IDS:
        raise ValueError("unreviewed replay case")
    output_dir.mkdir(parents=True, exist_ok=False)
    corpus = json.loads((corpus_root / "corpus.json").read_text())
    manifest = json.loads((corpus_root / "manifest.json").read_text())
    case = next(case for case in corpus["cases"] if case["id"] == case_id)
    report = {"case_id": case_id, "status": "started", "model_boundary": "real_configured" if port else "ScriptModel_engineering_only", "turns": [],
              "corpus_sha256": digest((corpus_root / "corpus.json").read_bytes()),
              "manifest_sha256": digest((corpus_root / "manifest.json").read_bytes()),
              "source": {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                         "files": {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted((ROOT / "backend/app/hr_agent").glob("*.py"))}},
              "limitations": ["synthetic adaptation, not verbatim history", "no quality claim from ScriptModel", "external login exchange and object store substituted"]}
    write_json(output_dir / "report.json", report)
    try:
        with environment(runtime_dir, profile) as env:
            client, repo, resources = env["client"], env["repo"], env["resources"]
            report["knowledge"] = resources.knowledge.metadata()
            report["budget"] = repo.settings.budget_profile
            response = client.get("/api/hr/agent/configuration")
            assert response.status_code == 200, "configuration HTTP failed"
            configuration = response.json()
            report["configuration_http"] = {"status": response.status_code, "body": configuration}
            completed, thread, retained_refs = {}, None, []
            for turn in case["turns"]:
                record = {"number": turn["number"], "http": {}, "materials": [], "status": "started"}
                report["turns"].append(record)
                work = None
                http_start = len(env["http_log"])
                try:
                    prior_refs = previous_refs(turn, completed, required=case_id == "H03")
                    refs = list({json.dumps(ref, sort_keys=True): ref for ref in retained_refs + prior_refs}.values())
                    if turn["allowed_source_urls"]:
                        raise ValueError("selected replay cannot enable external source URLs")
                    raw = asset_bytes(corpus_root, manifest, turn["context_fixture"])
                    context = raw.decode("utf8")
                    text = turn["text"]
                    attach = case_id in {"H06", "H13"} or (case_id == "H01" and turn["number"] in {2, 3}) or (case_id == "H03" and turn["number"] in {1, 4})
                    assets = list(turn["attach_assets"])
                    if attach:
                        assets.insert(0, turn["context_fixture"])
                    else:
                        text += "\n\n【本轮新创合成文字背景；非历史原件】\n" + context
                        record["inline_material"] = {"path": turn["context_fixture"], "sha256": digest(raw), "version": "synthetic-v2.1"}
                    for name in assets:
                        if not isinstance(name, str):
                            raise ValueError("unexpected attachment recipe")
                        data = asset_bytes(corpus_root, manifest, name)
                        upload_tuple = (client, env["headers"], repo, env["owner"], env["materials"], None, None, env["store"])
                        aid, ref = upload_text(upload_tuple, env["database"], data.decode("utf8"), Path(name).name)
                        material = client.get("/api/hr/agent/materials/" + str(aid))
                        assert material.status_code == 200 and material.json()["text_ref"] == ref
                        refs.append(ref)
                        record["materials"].append({"path": name, "sha256": digest(data), "version": "synthetic-v2.1", "attachment_id": str(aid), "ref": ref, "http": material.status_code})
                    previous = completed.get(turn["depends_on_turn"])
                    endpoint = "/api/hr/agent/works"
                    expected_status = 201
                    request = {"thread_id": None, "text": text, "objects": [], "references": refs, "budget_profile": configuration["budget_profile"]}
                    if previous is not None:
                        endpoint += "/" + previous["work_id"] + "/inputs"
                        expected_status = 202
                        request = {"expected_input_revision": previous["input_revision"], "question_id": previous["pending_question_id"] if previous["state"] == "waiting_user" else None,
                                   "text": text, "objects": [], "references": refs}
                    record["endpoint"] = endpoint
                    key = str(uuid4())
                    record.update(request=request, idempotency_key=key)
                    headers = {**env["headers"], "Idempotency-Key": key}
                    if not completed:
                        csrf = client.post("/api/hr/agent/works", json=request, headers={**headers, "X-CSRF-Token": "wrong"}).status_code
                        origin = client.post("/api/hr/agent/works", json=request, headers={**headers, "Origin": "https://wrong.invalid"}).status_code
                        report["security"] = {"anonymous": env["anonymous"], "csrf": csrf, "origin": origin}
                    response = client.post(endpoint, json=request, headers=headers)
                    record["http"]["submit"] = response.status_code
                    assert response.status_code == expected_status, response.text
                    work = response.json()
                    thread = work["thread_id"]
                    replay = client.post(endpoint, json=request, headers=headers)
                    record["http"]["replay"] = replay.status_code
                    assert replay.status_code == (200 if previous is None else 202) and replay.json()["work_id"] == work["work_id"]
                    report["security"]["wrong_owner"] = env["other"].get("/api/hr/agent/works/" + work["work_id"]).status_code
                    assert report["security"] == {"anonymous": 401, "csrf": 403, "origin": 403, "wrong_owner": 404}
                    write_json(output_dir / f"turn-{turn['number']:02d}.json", record)
                    model = port
                    if model is None:
                        scripts = [tool("read_resource", {"ref": ref}) for ref in refs]
                        if not (case_id == "H01" and turn["number"] < 3):
                            kinds = ("jd", "requirements") if case_id in {"H01", "H03"} else ("candidate_assessment" if case_id == "H06" else "research",)
                            old_results = report["turns"][-2].get("persisted", {}).get("results", []) if previous else []
                            for kind in kinds:
                                old_ref = next((item["ref"] for item in old_results if item["kind"] == kind), None)
                                args = {"kind": kind, "title": "工程回放合成成果 " + kind, "body": "仅验证真实保存与精确引用，不表示专业质量合格。",
                                        "objects": [], "source_refs": refs, "preceding_refs": prior_refs,
                                        "result_id": old_ref["id"] if old_ref else None, "expected_revision": old_ref["revision"] if old_ref else None,
                                        "base_standard_ref": None, "changes": [], "basis": [{"kind": "user_temporary", "input_revision": work["input_revision"], "ref": None}]}
                                scripts.append(tool("save_result", args))
                        scripts.append(answer("工程回放已接收本轮材料。" if case_id == "H01" and turn["number"] < 3 else "工程回放保存完成。"))
                        model = ScriptModel(scripts)
                    stop = threading.Event()
                    def run_one(repository, current_model, current_resources, fence, **kwargs):
                        assert str(fence.work_id) == work["work_id"]
                        try:
                            return run_work(repository, current_model, current_resources, fence, **kwargs)
                        finally:
                            stop.set()
                    run_worker(repo, model, resources, stop_event=stop, worker_id="history-owned", runner=run_one)
                    response = client.get("/api/hr/agent/works/" + work["work_id"])
                    record["http"]["work"] = response.status_code
                    assert response.status_code == 200
                    work = response.json()
                    record["persisted"] = _stage_evidence(client, repo, work)
                    completed[turn["number"]] = work
                    receiving = case_id == "H01" and turn["number"] < 3
                    if work["state"] not in ({"completed", "waiting_user"} if receiving else {"completed"}):
                        raise ValueError("replayed work did not complete")
                    if not receiving and not work["result_refs"]:
                        raise ValueError("required saved result absent")
                    if case_id in {"H01", "H03"} and not receiving:
                        if not {item["kind"] for item in record["persisted"]["results"]} >= {"jd", "requirements"}:
                            raise ValueError("required JD/JR saved result kinds absent")
                    if case_id == "H03" and previous:
                        current_by_id = {ref["id"]: ref for ref in work["result_refs"]}
                        if any(ref["id"] not in current_by_id or current_by_id[ref["id"]]["revision"] == ref["revision"] for ref in prior_refs):
                            raise ValueError("previous saved results were not exactly revised")
                    retained_refs = [ref for ref in refs if ref["kind"] in {"material", "method", "intelligence"}]
                    for call in record["persisted"]["tools"]:
                        data = (call.get("receipt") or {}).get("data") or {}
                        ref = data.get("ref") if isinstance(data, dict) else None
                        if isinstance(ref, dict) and ref.get("kind") in {"method", "intelligence", "material"}:
                            retained_refs.append(ref)
                    record["status"] = "completed"
                except Exception as error:  # noqa: BLE001 - preserve every failed turn, never fabricate success
                    record.update(status="failed", failure_type=type(error).__name__)
                    if isinstance(error, ValueError):
                        record["failure_code"] = str(error)
                    if work is not None and "persisted" not in record:
                        record["persisted"] = _stage_evidence(client, repo, repo.get_work(env["owner"], work["work_id"]))
                    raise
                finally:
                    record["http_trace"] = env["http_log"][http_start:]
                    if port is not None:
                        record["model_observations"] = list(port.observations)
                    write_json(output_dir / f"turn-{turn['number']:02d}.json", record)
                    write_json(output_dir / "report.json", report)
            report["knowledge_final"] = resources.knowledge.metadata()
            assert report["knowledge_final"] == report["knowledge"]
            report["status"] = "completed"
    except Exception as error:  # noqa: BLE001 - caller receives durable failure report
        report.update(status="failed", failure_type=type(error).__name__)
    finally:
        write_json(output_dir / "report.json", report)
    return report
