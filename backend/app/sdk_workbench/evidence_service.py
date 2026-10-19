"""SDK candidate validation and server-calculated release gate."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException

from app.control_plane.models import AuthContext

from .service import SdkService


MODULE_PATHS = frozenset({
    "FrameProcessor", "OBEnhancedDepthFilter", "OBFilterLib", "OBSDKPrivFilterLib",
    "OpenOrbbecSDK", "OpenOrbbecSDKExt", "OpenOrbbecViewer",
    "OpenOrbbecViewer/third_party/OpenOrbbecSDK",
})
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def _digest(kind: str, payload: dict) -> str:
    canonical = json.dumps({"kind": kind, "payload": payload}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def gate_reasons(item: dict) -> list[str]:
    reasons = []
    for target in item["targets"]:
        label = f"{target['os']}/{target['arch']}"
        matching = [build for build in item["builds"] if build["os"] == target["os"] and build["arch"] == target["arch"]]
        if not matching:
            reasons.append(f"{label}:build_missing")
            continue
        latest = max(matching, key=lambda build: build["id"])
        if latest["status"] != "success" or not latest.get("artifact_sha256"):
            reasons.append(f"{label}:build_not_successful")
            continue
        for suite in ("unit", "hardware"):
            runs = [run for run in item["tests"] if run["build_id"] == latest["id"] and run["suite"] == suite]
            latest_run = max(runs, key=lambda run: run["id"]) if runs else None
            if not latest_run or latest_run["status"] != "passed" or latest_run["passed"] < 1 or latest_run["failed"] or latest_run["skipped"]:
                reasons.append(f"{label}:{suite}_not_passed")
    return reasons


class EvidenceService:
    def __init__(self, repository, zentao, evidence_repository, *, product_id: int) -> None:
        self.sdk = SdkService(repository, zentao, product_id=product_id, execution_id=1)
        self.repository = repository
        self.evidence = evidence_repository

    def _item(self, candidate_id: int) -> dict:
        item = self.evidence.get_candidate(candidate_id)
        if item is None:
            raise HTTPException(404, "sdk_candidate_not_found")
        return item

    def list_candidates(self, actor: AuthContext, page: int) -> dict:
        self.sdk._role(actor)
        return self.evidence.list_candidates(page)

    def candidate(self, actor: AuthContext, candidate_id: int) -> dict:
        self.sdk._role(actor)
        return self._item(candidate_id)

    async def create_candidate(self, actor: AuthContext, request_id: UUID, story_id: int, task_ids: list[int],
                               manifest: dict, targets: list[dict]) -> dict:
        self.sdk.require(actor, frozenset({"contributor"}), write=True)
        payload = {"story_id": story_id, "task_ids": task_ids, "manifest": manifest, "targets": targets}
        digest = _digest("candidate", payload)
        replay = self.evidence.replay(actor.internal_user_id, request_id, digest)
        if replay is not None:
            return replay
        if (set(manifest.get("modules", {})) != MODULE_PATHS or not _SHA40.fullmatch(manifest.get("packaging_sha", ""))
                or any(not isinstance(sha, str) or not _SHA40.fullmatch(sha) for sha in manifest["modules"].values())):
            raise HTTPException(422, "sdk_manifest_incomplete")
        if not task_ids or len(task_ids) != len(set(task_ids)) or not targets or len({(t["os"], t["arch"]) for t in targets}) != len(targets):
            raise HTTPException(422, "sdk_candidate_scope_invalid")
        story = await self.sdk._story(story_id)
        reviews = self.repository.list_reviews(story_id)
        if not reviews or reviews[-1]["decision"] != "approved" or reviews[-1]["story_version"] != str(story["version"]):
            raise HTTPException(409, "sdk_requirement_review_required")
        linked = {task["task_id"] for task in self.repository.list_tasks(story_id)}
        if not set(task_ids).issubset(linked):
            raise HTTPException(409, "sdk_task_link_required")
        return self.evidence.create_candidate(actor.internal_user_id, request_id, digest,
                                              story_id, str(story["version"]), task_ids, manifest, targets)

    def add_build(self, actor: AuthContext, candidate_id: int, request_id: UUID, body: dict) -> dict:
        self.sdk.require(actor, frozenset({"contributor"}), write=True)
        digest = _digest("build", {"candidate_id": candidate_id, **body})
        replay = self.evidence.replay(actor.internal_user_id, request_id, digest)
        if replay is not None:
            return replay
        item = self._item(candidate_id)
        if {"os": body["os"], "arch": body["arch"]} not in item["targets"]:
            raise HTTPException(409, "sdk_target_not_planned")
        if body["status"] == "success" and not all(body.get(key) for key in ("artifact_name", "artifact_sha256", "artifact_size", "artifact_uri")):
            raise HTTPException(422, "sdk_artifact_required")
        if body["status"] != "success" and any(body.get(key) for key in ("artifact_name", "artifact_sha256", "artifact_size", "artifact_uri")):
            raise HTTPException(422, "sdk_artifact_unexpected")
        return self.evidence.add_build(actor.internal_user_id, request_id, digest, candidate_id, body)

    def add_test(self, actor: AuthContext, build_id: int, request_id: UUID, body: dict) -> dict:
        self.sdk.require(actor, frozenset({"reviewer"}), write=True)
        digest = _digest("test", {"build_id": build_id, **body})
        replay = self.evidence.replay(actor.internal_user_id, request_id, digest)
        if replay is not None:
            return replay
        build = self.evidence.get_build(build_id)
        if not build or build["status"] != "success":
            raise HTTPException(409, "sdk_artifact_required")
        if body["suite"] == "hardware" and (not body["device"] or not body["firmware"]):
            raise HTTPException(422, "sdk_hardware_identity_required")
        if body["status"] == "passed" and (body["passed"] < 1 or body["failed"] or body["skipped"]):
            raise HTTPException(422, "sdk_test_counts_invalid")
        return self.evidence.add_test(actor.internal_user_id, request_id, digest, build_id, body)

    async def gate(self, actor: AuthContext, candidate_id: int) -> dict:
        self.sdk._role(actor)
        item = self._item(candidate_id)
        reasons = gate_reasons(item)
        story = await self.sdk._story(item["story_id"])
        reviews = self.repository.list_reviews(item["story_id"])
        if str(story["version"]) != item["story_version"] or not reviews or reviews[-1]["decision"] != "approved" or reviews[-1]["story_version"] != item["story_version"]:
            reasons.append("requirement_review_outdated")
        latest_approval = item["approvals"][-1] if item["approvals"] else None
        approved = bool(latest_approval and latest_approval["evidence_version"] == item["evidence_version"] and not reasons)
        return {"ready": not reasons, "reasons": reasons, "evidence_version": item["evidence_version"],
                "approved": approved, "approval_id": latest_approval["id"] if approved else None}

    async def approve(self, actor: AuthContext, candidate_id: int, request_id: UUID, note: str) -> dict:
        self.sdk.require(actor, frozenset({"release_manager"}), write=True)
        digest = _digest("approval", {"candidate_id": candidate_id, "note": note})
        replay = self.evidence.replay(actor.internal_user_id, request_id, digest)
        if replay is not None:
            return replay
        item = self._item(candidate_id)
        if item["creator_id"] == str(actor.internal_user_id):
            raise HTTPException(409, "sdk_approval_actor_separation")
        gate = await self.gate(actor, candidate_id)
        if not gate["ready"]:
            raise HTTPException(409, "sdk_gate_not_ready")
        return self.evidence.add_approval(actor.internal_user_id, request_id, digest,
                                          candidate_id, gate["evidence_version"], note)

    async def release(self, actor: AuthContext, candidate_id: int, request_id: UUID, channel: str,
                      rollback_uri: str, released_at: datetime) -> dict:
        self.sdk.require(actor, frozenset({"release_manager"}), write=True)
        payload = {"candidate_id": candidate_id, "channel": channel, "rollback_uri": rollback_uri, "released_at": released_at.isoformat()}
        digest = _digest("release", payload)
        replay = self.evidence.replay(actor.internal_user_id, request_id, digest)
        if replay is not None:
            return replay
        gate = await self.gate(actor, candidate_id)
        if not gate["ready"] or not gate["approved"]:
            raise HTTPException(409, "sdk_approval_required")
        item = self._item(candidate_id)
        latest = {}
        for build in item["builds"]:
            latest[(build["os"], build["arch"])] = build
        sha256s = [latest[(target["os"], target["arch"])]["artifact_sha256"] for target in item["targets"]]
        return self.evidence.add_release(actor.internal_user_id, request_id, digest, candidate_id,
                                         gate["approval_id"], gate["evidence_version"], channel, rollback_uri, released_at, sha256s)
