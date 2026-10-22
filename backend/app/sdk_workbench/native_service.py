"""Small SDK read model over the employee's native ZenTao API session."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from fastapi import HTTPException

from app.control_plane.models import AuthContext
from .zentao import (ZenTaoAuthenticationError, ZenTaoProtocolError,
                     ZenTaoRejected, ZenTaoRequestRejected, ZenTaoUnavailable)


_Q4_SOURCE = "2026年Q4上线版本"
_PAGE_SIZE = 50
_MAX_STORIES = 1000


def _positive_id(value: object) -> int:
    try:
        selected = int(value)
    except (TypeError, ValueError):
        raise HTTPException(502, "sdk_zentao_response_invalid") from None
    if selected < 1:
        raise HTTPException(502, "sdk_zentao_response_invalid")
    return selected


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


class NativeSdkService:
    def __init__(self, personal_client, repository, *, product_id: int) -> None:
        if product_id < 1:
            raise ValueError("sdk_product_invalid")
        self.client = personal_client
        self.repository = repository
        self.product_id = product_id

    async def _all_stories(self, actor: AuthContext) -> list[dict]:
        result: list[dict] = []
        page = 1
        while True:
            body = await self.client.request(actor, "GET", f"products/{self.product_id}/stories",
                                             params={"browseType": "allstory", "recPerPage": _PAGE_SIZE,
                                                     "pageID": page})
            stories = body.get("stories")
            pager = body.get("pager")
            if not isinstance(stories, list) or not isinstance(pager, dict):
                raise HTTPException(502, "sdk_zentao_response_invalid")
            result.extend(item for item in stories if isinstance(item, dict))
            if len(result) > _MAX_STORIES:
                raise HTTPException(503, "sdk_requirement_catalog_too_large")
            try:
                page_total = int(pager["pageTotal"])
            except (KeyError, TypeError, ValueError):
                raise HTTPException(502, "sdk_zentao_response_invalid") from None
            if page >= page_total:
                return result
            page += 1

    async def list_requirements(self, actor: AuthContext, *, page: int = 1,
                                view: str = "q4", query: str = "", status: str = "") -> dict:
        stories = await self._all_stories(actor)
        selected = [story for story in stories
                    if int(story.get("product", self.product_id)) == self.product_id
                    and story.get("deleted") not in {"1", 1, True}]
        if view == "q4":
            selected = [story for story in selected if _text(story.get("sourceNote")).startswith(_Q4_SOURCE)]
        if query:
            selected = [story for story in selected if query.casefold() in _text(story.get("title")).casefold()]
        if status:
            selected = [story for story in selected if story.get("status") == status]
        total = len(selected)
        selected = selected[(page - 1) * _PAGE_SIZE:page * _PAGE_SIZE]
        sources = self.repository.sources_for([_positive_id(story.get("id")) for story in selected])
        accounts = [_text(story.get(key)) for story in selected for key in ("openedBy", "assignedTo")]
        names = self.repository.names_for(accounts)
        return {"items": [self._summary(story, sources.get(_positive_id(story.get("id"))), names)
                          for story in selected], "page": page, "total": total}

    def _summary(self, story: dict, source: dict | None, names: dict[str, str]) -> dict:
        opened_by = _text(story.get("openedBy"))
        assigned_to = _text(story.get("assignedTo"))
        return {
            "id": _positive_id(story.get("id")), "title": _text(story.get("title")),
            "status": _text(story.get("status")), "priority": story.get("pri"),
            "version": story.get("version"), "opened_by": opened_by,
            "opened_by_name": names.get(opened_by, opened_by),
            "assigned_to": assigned_to, "assigned_to_name": names.get(assigned_to, assigned_to),
            "opened_date": _text(story.get("openedDate")),
            "assigned_date": _text(story.get("assignedDate")),
            "business_proposer": source.get("proposer_name") if source else None,
            "device_platform": source.get("device_platform") if source else None,
            "source_module": source.get("module") if source else None,
            "q4_source": _text(story.get("sourceNote")).startswith(_Q4_SOURCE),
        }

    async def requirement(self, actor: AuthContext, story_id: int) -> dict:
        body = await self.client.request(actor, "GET", f"stories/{story_id}")
        story = body.get("story")
        if not isinstance(story, dict) or _positive_id(story.get("id")) != story_id:
            raise HTTPException(502, "sdk_zentao_response_invalid")
        if int(story.get("product", 0)) != self.product_id or story.get("deleted") in {"1", 1, True}:
            raise HTTPException(404, "sdk_requirement_not_found")
        actions = body.get("actions", [])
        if not isinstance(actions, list):
            raise HTTPException(502, "sdk_zentao_response_invalid")
        source = self.repository.source_for(story_id)
        accounts = [_text(story.get(key)) for key in ("openedBy", "assignedTo")]
        accounts.extend(_text(action.get("actor")) for action in actions if isinstance(action, dict))
        names = self.repository.names_for(accounts)
        return {**self._summary(story, source, names),
                "spec": _text(story.get("spec")), "verify": _text(story.get("verify")),
                "source_note": _text(story.get("sourceNote")), "source": source,
                "reviewer": story.get("reviewer") if isinstance(story.get("reviewer"), list) else [],
                "tasks": story.get("tasks") if isinstance(story.get("tasks"), list) else [],
                "actions": [{"id": action.get("id"), "action": _text(action.get("action")),
                             "actor": _text(action.get("actor")),
                             "actor_name": names.get(_text(action.get("actor")), _text(action.get("actor"))),
                             "date": _text(action.get("date")), "comment": _text(action.get("comment"))}
                            for action in actions if isinstance(action, dict)][:100]}

    async def my_tasks(self, actor: AuthContext, *, page: int = 1) -> dict:
        body = await self.client.request(actor, "GET", "my/tasks", params={"pageID": page, "recPerPage": _PAGE_SIZE})
        tasks = body.get("tasks")
        pager = body.get("pager")
        if not isinstance(tasks, list) or not isinstance(pager, dict):
            raise HTTPException(502, "sdk_zentao_response_invalid")
        names = self.repository.names_for([_text(task.get("assignedTo")) for task in tasks if isinstance(task, dict)])
        return {"items": [{"id": _positive_id(task.get("id")), "name": _text(task.get("name")),
                           "status": _text(task.get("status")), "assignee": _text(task.get("assignedTo")),
                           "assignee_name": names.get(_text(task.get("assignedTo")), _text(task.get("assignedTo"))),
                           "story_id": int(task.get("story") or task.get("storyID") or 0),
                           "opened_date": _text(task.get("openedDate")),
                           "planned_start": _text(task.get("estStarted")), "deadline": _text(task.get("deadline"))}
                          for task in tasks if isinstance(task, dict)],
                "page": page, "total": int(pager.get("recTotal", 0))}

    async def members(self, actor: AuthContext) -> dict:
        # The personal transport verifies SDK eligibility before exposing the selector.
        await self.client.account(actor)
        return {"items": self.repository.members()}

    async def pending_create(self, actor: AuthContext) -> dict:
        await self.client.account(actor)
        intent = self.repository.pending_create(actor.internal_user_id)
        if intent is None:
            return {"status": "none"}
        return await self._reconcile_create(actor, intent, {
            "request_id": intent["request_id"],
            "assigned_to": intent["requested_assignee"],
            "business_proposer": intent["proposer_name"],
            "device_platform": intent["device_platform"],
        })

    def _member(self, account: str | None) -> None:
        if account is not None and not self.repository.member_account(account):
            raise HTTPException(422, "sdk_member_not_bound")

    async def _current_story(self, actor: AuthContext, story_id: int,
                             expected_version: int | None = None) -> dict:
        if story_id < 1:
            raise HTTPException(404, "sdk_requirement_not_found")
        body = await self.client.request(actor, "GET", f"stories/{story_id}")
        story = body.get("story")
        if not isinstance(story, dict) or _positive_id(story.get("id")) != story_id:
            raise HTTPException(502, "sdk_zentao_response_invalid")
        if int(story.get("product", 0)) != self.product_id or story.get("deleted") in {"1", 1, True}:
            raise HTTPException(404, "sdk_requirement_not_found")
        if expected_version is not None and int(story.get("version", 0)) != expected_version:
            raise HTTPException(409, "sdk_requirement_version_conflict")
        return story

    @staticmethod
    def _write_digest(body: dict) -> str:
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                         default=str, separators=(",", ":")).encode()).hexdigest()

    async def _reconcile_create(self, actor: AuthContext, intent: dict, body: dict) -> dict:
        try:
            stories = await self._all_stories(actor)
        except (ZenTaoUnavailable, ZenTaoProtocolError):
            return {"status": "reconciliation_required", "request_id": str(body["request_id"])}
        matches = [story for story in stories if story.get("keywords") == intent["marker"]
                   and int(story.get("product", 0)) == self.product_id]
        if len(matches) != 1:
            return {"status": "reconciliation_required", "request_id": str(body["request_id"])}
        story_id = _positive_id(matches[0].get("id"))
        try:
            story = await self._current_story(actor, story_id)
        except (ZenTaoUnavailable, ZenTaoProtocolError):
            return {"status": "reconciliation_required", "request_id": str(body["request_id"])}
        if (story.get("keywords") != intent["marker"]
                or (body.get("assigned_to") and story.get("assignedTo") != body["assigned_to"])):
            return {"status": "reconciliation_required", "request_id": str(body["request_id"])}
        self.repository.complete_create(actor.internal_user_id, body["request_id"], story_id,
                                        body.get("business_proposer"), body["device_platform"])
        return {"status": "created", "story_id": story_id,
                "requirement": await self.requirement(actor, story_id)}

    async def create_requirement(self, actor: AuthContext, body: dict) -> dict:
        account = await self.client.account(actor)
        self._member(body.get("assigned_to"))
        self._member(body.get("reviewer"))
        request_id = body["request_id"]
        if not isinstance(request_id, UUID):
            request_id = UUID(str(request_id))
            body = {**body, "request_id": request_id}
        marker = f"sdk-native:{actor.internal_user_id.hex}:{request_id.hex}"
        intent, fresh = self.repository.reserve_create(
            actor.internal_user_id, request_id, self._write_digest(body), marker,
            requested_assignee=body.get("assigned_to"),
            proposer=body.get("business_proposer"), device=body["device_platform"])
        if not fresh:
            if intent["state"] == "created":
                story_id = int(intent["story_id"])
                return {"status": "created", "story_id": story_id,
                        "requirement": await self.requirement(actor, story_id)}
            if intent["state"] == "rejected":
                raise HTTPException(intent["rejection_status"], intent["rejection_detail"])
            return await self._reconcile_create(actor, intent, body)

        reviewer = body.get("reviewer")
        source = "由 SDK 开发管理平台录入"
        if body.get("business_proposer"):
            source += f"；业务提出人：{body['business_proposer']}"
        if body.get("device_platform"):
            source += f"；设备与平台：{body['device_platform']}"
        payload = {"product": self.product_id, "title": body["title"],
                   "spec": body["scenario"], "verify": body["acceptance"],
                   "pri": body["priority"], "category": "feature",
                   "reviewer": [reviewer] if reviewer else [],
                   "status": "active" if reviewer else "draft",
                   "source": "user", "sourceNote": source, "keywords": marker}
        try:
            created = await self.client.request(actor, "POST", "stories", payload,
                                                api_version=1)
        except ZenTaoRejected as error:
            if isinstance(error, ZenTaoAuthenticationError):
                rejection_status = 503
            elif isinstance(error, ZenTaoRequestRejected):
                rejection_status = 403
            else:
                rejection_status = 502
            rejection_detail = "sdk_create_rejected"
            self.repository.reject_create(actor.internal_user_id, request_id,
                                          rejection_status, rejection_detail)
            raise HTTPException(rejection_status, rejection_detail) from None
        except (ZenTaoUnavailable, ZenTaoProtocolError):
            return {"status": "reconciliation_required", "request_id": str(request_id)}
        try:
            story_id = _positive_id(created.get("id"))
            story = await self._current_story(actor, story_id)
            if story.get("keywords") != marker or story.get("openedBy") != account:
                raise ZenTaoProtocolError("zentao_create_readback_mismatch")
            if body.get("assigned_to"):
                await self.client.request(actor, "POST", f"stories/{story_id}/assign",
                                          {"assignedTo": body["assigned_to"], "comment": ""},
                                          api_version=1)
                story = await self._current_story(actor, story_id)
                if story.get("assignedTo") != body["assigned_to"]:
                    raise ZenTaoProtocolError("zentao_assignment_unconfirmed")
        except (ZenTaoUnavailable, ZenTaoProtocolError, ZenTaoRejected):
            return {"status": "reconciliation_required", "request_id": str(request_id)}
        self.repository.complete_create(actor.internal_user_id, request_id, story_id,
                                        body.get("business_proposer"), body["device_platform"])
        return {"status": "created", "story_id": story_id,
                "requirement": await self.requirement(actor, story_id)}

    async def edit_requirement(self, actor: AuthContext, story_id: int, *, expected_version: int,
                               title: str, scenario: str, device_platform: str | None,
                               acceptance: str, priority: int,
                               business_proposer: str | None) -> dict:
        before = await self._current_story(actor, story_id, expected_version)
        if before.get("status") not in {"draft", "reviewing", "active"}:
            raise HTTPException(409, "sdk_requirement_status_conflict")
        payload = {"title": title, "spec": scenario, "verify": acceptance, "pri": priority,
                   "lastEditedDate": before.get("lastEditedDate") or ""}
        if before.get("reviewer"):
            payload["reviewer"] = before["reviewer"]
        await self.client.request(actor, "PUT", f"stories/{story_id}", payload)
        after = await self._current_story(actor, story_id)
        if any((after.get(key) != value for key, value in (("title", title), ("spec", scenario),
                       ("verify", acceptance), ("pri", priority)))):
            raise HTTPException(502, "sdk_requirement_write_unconfirmed")
        self.repository.save_supplement(story_id, business_proposer, device_platform)
        return await self.requirement(actor, story_id)

    async def assign_requirement(self, actor: AuthContext, story_id: int, *,
                                 expected_version: int, assigned_to: str) -> dict:
        await self.client.account(actor)
        self._member(assigned_to)
        before = await self._current_story(actor, story_id, expected_version)
        if before.get("status") not in {"draft", "reviewing", "active"}:
            raise HTTPException(409, "sdk_requirement_status_conflict")
        if before.get("assignedTo") != assigned_to:
            await self.client.request(actor, "POST", f"stories/{story_id}/assign",
                                      {"assignedTo": assigned_to, "comment": ""}, api_version=1)
        after = await self._current_story(actor, story_id)
        if after.get("assignedTo") != assigned_to:
            raise HTTPException(502, "sdk_requirement_write_unconfirmed")
        return await self.requirement(actor, story_id)

    async def review_requirement(self, actor: AuthContext, story_id: int, *,
                                 expected_version: int, decision: str, comment: str = "",
                                 closed_reason: str | None = None) -> dict:
        before = await self._current_story(actor, story_id, expected_version)
        if before.get("status") != "reviewing":
            raise HTTPException(409, "sdk_requirement_status_conflict")
        account = await self.client.account(actor)
        if account not in (before.get("reviewer") or []):
            raise HTTPException(403, "sdk_requirement_reviewer_required")
        payload = {"result": decision, "comment": comment}
        if decision == "reject":
            if not closed_reason:
                raise HTTPException(422, "sdk_review_reason_required")
            payload["closedReason"] = closed_reason
        await self.client.request(actor, "POST", f"stories/{story_id}/review", payload,
                                  api_version=1)
        after = await self._current_story(actor, story_id)
        if decision == "pass" and after.get("status") not in {"active", "reviewing"}:
            raise HTTPException(502, "sdk_requirement_write_unconfirmed")
        if decision == "reject" and after.get("status") not in {"closed", "reviewing"}:
            raise HTTPException(502, "sdk_requirement_write_unconfirmed")
        return await self.requirement(actor, story_id)
