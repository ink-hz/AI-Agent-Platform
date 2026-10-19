"""SDK requirement workflow with explicit local intent state."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException

from app.control_plane.models import AuthContext, Role

from .zentao import ZenTaoProtocolError, ZenTaoRejected, ZenTaoUnavailable


_READ_ROLES = frozenset({"viewer", "contributor", "reviewer", "release_manager"})


class SdkService:
    def __init__(self, repository, zentao, *, product_id: int, execution_id: int) -> None:
        if product_id < 1 or execution_id < 1:
            raise ValueError("sdk_product_execution_required")
        self.repository = repository
        self.zentao = zentao
        self.product_id = product_id
        self.execution_id = execution_id

    def _role(self, actor: AuthContext, *, write: bool = False) -> str:
        role = self.repository.role_for(actor.internal_user_id)
        if role not in _READ_ROLES:
            raise HTTPException(403, "sdk_access_required")
        if write and actor.hard_stale_read_only:
            raise HTTPException(503, "hard_stale_read_only")
        return role

    def require(self, actor: AuthContext, allowed: frozenset[str], *, write: bool = False) -> str:
        role = self._role(actor, write=write)
        if role not in allowed:
            raise HTTPException(403, "sdk_role_required")
        return role

    def me(self, actor: AuthContext) -> dict:
        role = self.repository.role_for(actor.internal_user_id)
        return {
            "role": role if role in _READ_ROLES else None,
            "can_manage": actor.role is Role.PLATFORM_OWNER,
            "read_only": actor.hard_stale_read_only,
        }

    def list_members(self, actor: AuthContext) -> dict:
        if actor.role is not Role.PLATFORM_OWNER:
            raise HTTPException(403, "sdk_owner_required")
        return {"items": self.repository.members()}

    def grant_member(self, actor: AuthContext, target: UUID, role: str, request_id: UUID) -> dict:
        if actor.role is not Role.PLATFORM_OWNER:
            raise HTTPException(403, "sdk_owner_required")
        if actor.hard_stale_read_only:
            raise HTTPException(503, "hard_stale_read_only")
        return self.repository.grant_member(actor.internal_user_id, target, role, request_id)

    def reconciliation(self, actor: AuthContext) -> dict:
        if actor.role is not Role.PLATFORM_OWNER:
            raise HTTPException(403, "sdk_owner_required")
        return {"items": self.repository.list_reconciliation()}

    async def bind_reconciliation(self, actor: AuthContext, target_actor: UUID, target_request_id: UUID, admin_request_id: UUID, external_id: int) -> dict:
        if actor.role is not Role.PLATFORM_OWNER:
            raise HTTPException(403, "sdk_owner_required")
        if actor.hard_stale_read_only:
            raise HTTPException(503, "hard_stale_read_only")
        intent = self.repository.get_intent(target_actor, target_request_id)
        if intent is None:
            raise HTTPException(404, "sdk_intent_not_found")
        if intent["state"] == "succeeded" and intent["result"] == {"id": external_id}:
            return {"id": external_id}
        stale_pending = (intent["state"] == "pending" and isinstance(intent.get("created_at"), datetime)
                         and intent["created_at"] <= datetime.now(timezone.utc) - timedelta(seconds=60))
        if intent["state"] != "reconcile_required" and not stale_pending:
            raise HTTPException(409, "sdk_reconcile_required")
        try:
            if intent["kind"] == "create_requirement":
                await self._story(external_id)
            elif intent["kind"] == "create_task":
                task = await self.zentao.get_task(external_id)
                if str(task.get("story")) != str(intent["story_id"]) or str(task.get("execution")) != str(self.execution_id):
                    raise HTTPException(409, "sdk_external_mismatch")
                await self._story(int(intent["story_id"]))
            else:
                raise HTTPException(409, "sdk_reconciliation_not_supported")
        except (ZenTaoUnavailable, ZenTaoRejected, ZenTaoProtocolError) as error:
            raise self._translate(error) from None
        return self.repository.bind_reconciliation(actor.internal_user_id, admin_request_id, target_actor, target_request_id, external_id)

    @staticmethod
    def _translate(error: Exception) -> HTTPException:
        if isinstance(error, ZenTaoUnavailable):
            return HTTPException(503, "sdk_zentao_unavailable")
        if isinstance(error, ZenTaoRejected):
            return HTTPException(502, "sdk_zentao_rejected")
        return HTTPException(502, "sdk_zentao_protocol_error")

    async def list_requirements(self, actor: AuthContext, page: int) -> dict:
        self._role(actor)
        try:
            body = await self.zentao.list_stories(self.product_id, page)
        except (ZenTaoUnavailable, ZenTaoRejected, ZenTaoProtocolError) as error:
            raise self._translate(error) from None
        items = []
        for raw in body["stories"]:
            if not isinstance(raw, dict) or not str(raw.get("id", "")).isdigit():
                raise HTTPException(502, "sdk_zentao_protocol_error")
            items.append({
                "id": int(raw["id"]),
                "title": str(raw.get("title") or ""),
                "status": str(raw.get("status") or ""),
            })
        return {"items": items, "page": page, "total": body.get("total")}

    async def _story(self, story_id: int) -> dict:
        try:
            story = await self.zentao.get_story(story_id)
        except (ZenTaoUnavailable, ZenTaoRejected, ZenTaoProtocolError) as error:
            raise self._translate(error) from None
        if str(story.get("product")) != str(self.product_id):
            raise HTTPException(404, "sdk_requirement_not_found")
        if not str(story.get("version", "")).isdigit() or int(story["version"]) < 1:
            raise HTTPException(502, "sdk_zentao_protocol_error")
        return story

    async def requirement(self, actor: AuthContext, story_id: int) -> dict:
        self._role(actor)
        story = await self._story(story_id)
        return {"story": story, "reviews": self.repository.list_reviews(story_id), "tasks": self.repository.list_tasks(story_id)}

    def _intent(self, actor: AuthContext, request_id: UUID, kind: str, payload: dict) -> dict | None:
        canonical = json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        intent, fresh = self.repository.reserve(actor.internal_user_id, request_id, digest, kind, payload.get("story_id"))
        if fresh:
            return None
        if intent["digest"] != digest:
            raise HTTPException(409, "sdk_request_id_collision")
        if intent["state"] == "succeeded" and isinstance(intent["result"], dict):
            return intent["result"]
        raise HTTPException(409, "sdk_reconcile_required")

    async def create_requirement(self, actor: AuthContext, request_id: UUID, title: str, scenario: str, device_platform: str, acceptance: str) -> dict:
        self.require(actor, frozenset({"contributor"}), write=True)
        payload = {"title": title, "scenario": scenario, "device_platform": device_platform, "acceptance": acceptance}
        replay = self._intent(actor, request_id, "create_requirement", payload)
        if replay is not None:
            return replay
        spec = f"场景：{scenario}\n设备/平台：{device_platform}"
        try:
            story_id = await self.zentao.create_story(self.product_id, title, spec, acceptance)
        except (ZenTaoUnavailable, ZenTaoRejected, ZenTaoProtocolError):
            self.repository.reconcile(actor.internal_user_id, request_id)
            raise HTTPException(503, "sdk_reconcile_required") from None
        result = {"id": story_id}
        try:
            self.repository.complete(actor.internal_user_id, request_id, result)
        except Exception:
            raise HTTPException(503, "sdk_reconcile_required") from None
        return result

    async def create_task(self, actor: AuthContext, story_id: int, request_id: UUID, name: str, description: str) -> dict:
        self.require(actor, frozenset({"contributor"}), write=True)
        story = await self._story(story_id)
        reviews = self.repository.list_reviews(story_id)
        if not reviews or reviews[-1]["decision"] != "approved" or reviews[-1]["story_version"] != str(story["version"]):
            raise HTTPException(409, "sdk_requirement_review_required")
        payload = {"story_id": story_id, "name": name, "description": description}
        replay = self._intent(actor, request_id, "create_task", payload)
        if replay is not None:
            return replay
        try:
            task_id = await self.zentao.create_task(self.execution_id, story_id, name, description)
        except (ZenTaoUnavailable, ZenTaoRejected, ZenTaoProtocolError):
            self.repository.reconcile(actor.internal_user_id, request_id)
            raise HTTPException(503, "sdk_reconcile_required") from None
        result = {"id": task_id}
        try:
            self.repository.complete_task(actor.internal_user_id, story_id, task_id, request_id)
        except Exception:
            raise HTTPException(503, "sdk_reconcile_required") from None
        return result

    async def review(self, actor: AuthContext, story_id: int, request_id: UUID, decision: str, reason: str) -> dict:
        self.require(actor, frozenset({"reviewer"}), write=True)
        story = await self._story(story_id)
        story_version = str(story["version"])
        replay = self._intent(actor, request_id, "review_requirement", {"story_id": story_id, "story_version": story_version, "decision": decision, "reason": reason})
        if replay is not None:
            return replay
        result = self.repository.add_review(actor.internal_user_id, story_id, story_version, decision, reason, request_id)
        return result
