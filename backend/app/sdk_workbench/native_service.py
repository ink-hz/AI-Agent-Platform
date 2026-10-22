"""Small SDK read model over the employee's native ZenTao API session."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

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


def _priority(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        raise HTTPException(502, "sdk_zentao_response_invalid") from None


def _edit_token(story: dict) -> str:
    """Track mutable fields because ZenTao does not advance story.version on edits."""
    fields = ("title", "spec", "verify", "pri", "reviewer", "assignedTo",
              "assignedDate", "status", "lastEditedDate")
    snapshot = {field: story.get(field) for field in fields}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                                     default=str, separators=(",", ":")).encode()).hexdigest()


class NativeSdkService:
    def __init__(self, personal_client, repository, *, product_id: int,
                 sso_service=None) -> None:
        if product_id < 1:
            raise ValueError("sdk_product_invalid")
        self.client = personal_client
        self.repository = repository
        self.product_id = product_id
        self.sso_service = sso_service

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
                                view: str = "q4", query: str = "", status: str = "",
                                owner: str = "") -> dict:
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
        if owner:
            selected = [story for story in selected if story.get("assignedTo") == owner]
        total = len(selected)
        selected = selected[(page - 1) * _PAGE_SIZE:page * _PAGE_SIZE]
        sources = await run_in_threadpool(self.repository.sources_for,
                                          [_positive_id(story.get("id")) for story in selected])
        accounts = [_text(story.get(key)) for story in selected for key in ("openedBy", "assignedTo")]
        names = await run_in_threadpool(self.repository.names_for, accounts)
        return {"items": [self._summary(story, sources.get(_positive_id(story.get("id"))), names)
                          for story in selected], "page": page, "total": total}

    def _summary(self, story: dict, source: dict | None, names: dict[str, str]) -> dict:
        opened_by = _text(story.get("openedBy"))
        assigned_to = _text(story.get("assignedTo"))
        return {
            "id": _positive_id(story.get("id")), "title": _text(story.get("title")),
            "status": _text(story.get("status")), "priority": _priority(story.get("pri")),
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
        source = await run_in_threadpool(self.repository.source_for, story_id)
        accounts = [_text(story.get(key)) for key in ("openedBy", "assignedTo")]
        accounts.extend(_text(action.get("actor")) for action in actions if isinstance(action, dict))
        names = await run_in_threadpool(self.repository.names_for, accounts)
        return {**self._summary(story, source, names), "edit_token": _edit_token(story),
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
        account = await self.client.account(actor)
        all_tasks: list[dict] = []
        source_page = 1
        while True:
            body = await self.client.request(actor, "GET", "my/tasks",
                                             params={"pageID": source_page, "recPerPage": _PAGE_SIZE})
            tasks = body.get("tasks")
            pager = body.get("pager")
            if not isinstance(tasks, list) or not isinstance(pager, dict):
                raise HTTPException(502, "sdk_zentao_response_invalid")
            all_tasks.extend(task for task in tasks if isinstance(task, dict))
            if len(all_tasks) > _MAX_STORIES:
                raise HTTPException(503, "sdk_task_catalog_too_large")
            try:
                page_total = int(pager["pageTotal"])
            except (KeyError, TypeError, ValueError):
                raise HTTPException(502, "sdk_zentao_response_invalid") from None
            if source_page >= page_total:
                break
            source_page += 1

        execution_products: dict[int, set[int]] = {}
        for execution_id in {_positive_id(task["execution"]) for task in all_tasks
                             if task.get("execution")}:
            execution = await self.client.request(actor, "GET", f"executions/{execution_id}", api_version=1)
            products = execution.get("products")
            if not isinstance(products, list):
                raise HTTPException(502, "sdk_zentao_response_invalid")
            execution_products[execution_id] = {_positive_id(product.get("id"))
                                                 for product in products if isinstance(product, dict)}
        story_scope: dict[int, bool] = {}
        selected = []
        for task in all_tasks:
            if task.get("assignedTo") != account or not task.get("execution"):
                continue
            products = execution_products.get(_positive_id(task["execution"]), set())
            if self.product_id not in products:
                continue
            story_id = int(task.get("story") or task.get("storyID") or 0)
            if story_id:
                if story_id not in story_scope:
                    try:
                        await self._current_story(actor, story_id)
                        story_scope[story_id] = True
                    except HTTPException as error:
                        if error.status_code != 404:
                            raise
                        story_scope[story_id] = False
                    except ZenTaoRequestRejected:
                        story_scope[story_id] = False
                if not story_scope[story_id]:
                    continue
            elif products != {self.product_id}:
                # A storyless task in a multi-product execution has no SDK ownership proof.
                continue
            selected.append(task)
        total = len(selected)
        selected = selected[(page - 1) * _PAGE_SIZE:page * _PAGE_SIZE]
        names = await run_in_threadpool(self.repository.names_for,
                                        [_text(task.get("assignedTo")) for task in selected])
        return {"items": [{"id": _positive_id(task.get("id")), "name": _text(task.get("name")),
                           "status": _text(task.get("status")), "assignee": _text(task.get("assignedTo")),
                           "assignee_name": names.get(_text(task.get("assignedTo")), _text(task.get("assignedTo"))),
                           "story_id": int(task.get("story") or task.get("storyID") or 0),
                           "opened_date": _text(task.get("openedDate")),
                           "planned_start": _text(task.get("estStarted")), "deadline": _text(task.get("deadline"))}
                          for task in selected], "page": page, "total": total}

    async def task(self, actor: AuthContext, task_id: int) -> dict:
        if task_id < 1:
            raise HTTPException(404, "sdk_task_not_found")
        account = await self.client.account(actor)
        body = await self.client.request(actor, "GET", f"tasks/{task_id}")
        task = body.get("task")
        if not isinstance(task, dict) or _positive_id(task.get("id")) != task_id:
            raise HTTPException(502, "sdk_zentao_response_invalid")
        if task.get("deleted") in {"1", 1, True} or task.get("assignedTo") != account:
            raise HTTPException(404, "sdk_task_not_found")
        execution_id = _positive_id(task.get("execution"))
        execution = await self.client.request(actor, "GET", f"executions/{execution_id}", api_version=1)
        if (not isinstance(execution, dict) or _positive_id(execution.get("id")) != execution_id
                or not isinstance(execution.get("products"), list)):
            raise HTTPException(502, "sdk_zentao_response_invalid")
        product_ids = {_positive_id(product.get("id")) for product in execution["products"]
                       if isinstance(product, dict)}
        if self.product_id not in product_ids:
            raise HTTPException(404, "sdk_task_not_found")
        story_id = int(task.get("story") or task.get("storyID") or 0)
        if story_id:
            try:
                await self._current_story(actor, story_id)
            except HTTPException as error:
                if error.status_code == 404:
                    raise HTTPException(404, "sdk_task_not_found") from None
                raise
        elif product_ids != {self.product_id}:
            raise HTTPException(404, "sdk_task_not_found")
        actions = body.get("actions", [])
        if not isinstance(actions, list):
            raise HTTPException(502, "sdk_zentao_response_invalid")
        names = await run_in_threadpool(self.repository.names_for,
                                        [account, *[_text(action.get("actor"))
                                                    for action in actions if isinstance(action, dict)]])
        return {"id": task_id, "name": _text(task.get("name")),
                "status": _text(task.get("status")), "assignee": account,
                "assignee_name": names.get(account, account), "story_id": story_id,
                "opened_date": _text(task.get("openedDate")),
                "planned_start": _text(task.get("estStarted")),
                "deadline": _text(task.get("deadline")),
                "description": _text(task.get("desc")), "execution_id": execution_id,
                "execution_name": _text(execution.get("name")),
                "actions": [{"id": action.get("id"), "action": _text(action.get("action")),
                             "actor": _text(action.get("actor")),
                             "actor_name": names.get(_text(action.get("actor")), _text(action.get("actor"))),
                             "date": _text(action.get("date")), "comment": _text(action.get("comment"))}
                            for action in actions if isinstance(action, dict)][:100]}

    async def members(self, actor: AuthContext) -> dict:
        # The personal transport verifies SDK eligibility before exposing the selector.
        await self.client.account(actor)
        return {"items": await run_in_threadpool(self.repository.members)}

    async def pending_create(self, actor: AuthContext) -> dict:
        await self.client.account(actor)
        intent = await run_in_threadpool(self.repository.pending_create, actor.internal_user_id)
        if intent is None:
            return {"status": "none"}
        return await self._reconcile_create(actor, intent, {
            "request_id": intent["request_id"],
            "assigned_to": intent["requested_assignee"],
            "business_proposer": intent["proposer_name"],
            "device_platform": intent["device_platform"],
        })

    async def _member(self, account: str | None) -> None:
        if account is None:
            return
        if self.sso_service is not None:
            await run_in_threadpool(self.repository.ensure_member, account, self.sso_service)
        elif not await run_in_threadpool(self.repository.member_account, account):
            raise HTTPException(422, "sdk_member_not_bound")

    async def _current_story(self, actor: AuthContext, story_id: int,
                             expected_version: int | None = None,
                             expected_token: str | None = None) -> dict:
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
        if expected_token is not None and _edit_token(story) != expected_token:
            raise HTTPException(409, "sdk_requirement_version_conflict")
        return story

    @staticmethod
    def _write_digest(body: dict) -> str:
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                         default=str, separators=(",", ":")).encode()).hexdigest()

    async def _reconcile_create(self, actor: AuthContext, intent: dict, body: dict) -> dict:
        account = await self.client.account(actor)
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
        if story.get("keywords") != intent["marker"] or story.get("openedBy") != account:
            return {"status": "reconciliation_required", "request_id": str(body["request_id"])}
        assignment_warning = bool(body.get("assigned_to") and story.get("assignedTo") != body["assigned_to"])
        await run_in_threadpool(self.repository.complete_create,
                                actor.internal_user_id, body["request_id"], story_id,
                                body.get("business_proposer"), body["device_platform"])
        return {"status": "created", "story_id": story_id,
                "assignment_warning": assignment_warning,
                "requirement": await self.requirement(actor, story_id)}

    async def create_requirement(self, actor: AuthContext, body: dict) -> dict:
        if not body.get("reviewer"):
            raise HTTPException(422, "sdk_reviewer_required")
        account = await self.client.account(actor)
        request_id = body["request_id"]
        if not isinstance(request_id, UUID):
            request_id = UUID(str(request_id))
            body = {**body, "request_id": request_id}
        marker = f"sdk-native:{actor.internal_user_id.hex}:{request_id.hex}"
        intent, fresh = await run_in_threadpool(self.repository.reserve_create,
            actor.internal_user_id, request_id, self._write_digest(body), marker,
            requested_assignee=body.get("assigned_to"),
            proposer=body.get("business_proposer"), device=body["device_platform"])
        if not fresh:
            if intent["state"] == "created":
                story_id = int(intent["story_id"])
                requirement = await self.requirement(actor, story_id)
                return {"status": "created", "story_id": story_id,
                        "assignment_warning": bool(intent["requested_assignee"]
                                                   and requirement["assigned_to"] != intent["requested_assignee"]),
                        "requirement": requirement}
            if intent["state"] == "rejected":
                raise HTTPException(intent["rejection_status"], intent["rejection_detail"])
            return await self._reconcile_create(actor, intent, body)

        try:
            for target in {body.get("assigned_to"), body["reviewer"]} - {None}:
                await self._member(target)
        except HTTPException as error:
            # No story request has been sent, so the intent can be rejected safely.
            await run_in_threadpool(self.repository.reject_create,
                                    actor.internal_user_id, request_id,
                                    error.status_code, "sdk_create_rejected")
            raise HTTPException(error.status_code, "sdk_create_rejected") from None

        reviewer = body["reviewer"]
        payload = {"product": self.product_id, "title": body["title"],
                   "spec": body["scenario"], "verify": body["acceptance"],
                   "pri": body["priority"], "category": "feature",
                   "reviewer": [reviewer],
                   "status": "active",
                   "source": "user", "sourceNote": "由 SDK 开发管理平台录入", "keywords": marker}
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
            await run_in_threadpool(self.repository.reject_create,
                                    actor.internal_user_id, request_id,
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
            return await self._reconcile_create(actor, intent, body)
        await run_in_threadpool(self.repository.complete_create,
                                actor.internal_user_id, request_id, story_id,
                                body.get("business_proposer"), body["device_platform"])
        return {"status": "created", "story_id": story_id,
                "requirement": await self.requirement(actor, story_id)}

    async def edit_requirement(self, actor: AuthContext, story_id: int, *, expected_version: int,
                               expected_token: str,
                               title: str, scenario: str, device_platform: str | None,
                               acceptance: str, priority: int,
                               business_proposer: str | None) -> dict:
        before = await self._current_story(actor, story_id, expected_version, expected_token)
        if before.get("status") not in {"draft", "reviewing", "active"}:
            raise HTTPException(409, "sdk_requirement_status_conflict")
        payload = {"title": title, "spec": scenario, "verify": acceptance, "pri": priority,
                   "lastEditedDate": before.get("lastEditedDate") or ""}
        if before.get("reviewer"):
            payload["reviewer"] = before["reviewer"]
        await self.client.request(actor, "PUT", f"stories/{story_id}", payload)
        after = await self._current_story(actor, story_id)
        if (any(after.get(key) != value for key, value in (("title", title), ("spec", scenario),
                ("verify", acceptance))) or _priority(after.get("pri")) != priority):
            raise HTTPException(502, "sdk_requirement_write_unconfirmed")
        await run_in_threadpool(self.repository.save_supplement, story_id,
                                business_proposer, device_platform)
        return await self.requirement(actor, story_id)

    async def assign_requirement(self, actor: AuthContext, story_id: int, *,
                                 expected_version: int, expected_token: str,
                                 assigned_to: str) -> dict:
        await self.client.account(actor)
        await self._member(assigned_to)
        before = await self._current_story(actor, story_id, expected_version, expected_token)
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
                                 expected_version: int, expected_token: str,
                                 decision: str, comment: str = "",
                                 closed_reason: str | None = None) -> dict:
        before = await self._current_story(actor, story_id, expected_version, expected_token)
        if before.get("status") != "reviewing":
            raise HTTPException(409, "sdk_requirement_status_conflict")
        account = await self.client.account(actor)
        if account not in (before.get("reviewer") or []):
            raise HTTPException(403, "sdk_requirement_reviewer_required")
        pending_reviewers = before.get("notReview")
        if isinstance(pending_reviewers, list) and account not in pending_reviewers:
            raise HTTPException(409, "sdk_requirement_review_not_pending")
        prior_priority = _priority(before.get("pri"))
        prior_owner = _text(before.get("assignedTo"))
        payload = {"result": decision, "comment": comment, "pri": prior_priority}
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
        if after.get("status") == "reviewing":
            after_pending = after.get("notReview")
            if (not isinstance(pending_reviewers, list)
                    or not isinstance(after_pending, list)
                    or account not in pending_reviewers or account in after_pending):
                raise HTTPException(502, "sdk_requirement_write_unconfirmed")
        if _priority(after.get("pri")) != prior_priority:
            raise HTTPException(502, "sdk_requirement_write_unconfirmed")
        assignment_warning = False
        owner_changed_warning = False
        after_owner = _text(after.get("assignedTo"))
        if after.get("status") in {"active", "reviewing"} and prior_owner and after_owner and after_owner != prior_owner:
            owner_changed_warning = True
        if after.get("status") in {"active", "reviewing"} and prior_owner and not after_owner:
            try:
                await self.client.request(actor, "POST", f"stories/{story_id}/assign",
                                          {"assignedTo": prior_owner, "comment": ""}, api_version=1)
                after = await self._current_story(actor, story_id)
                assignment_warning = after.get("assignedTo") != prior_owner
            except (ZenTaoUnavailable, ZenTaoProtocolError, ZenTaoRejected):
                assignment_warning = True
        detail = await self.requirement(actor, story_id)
        if assignment_warning:
            detail["assignment_warning"] = True
        if owner_changed_warning:
            detail["owner_changed_warning"] = True
        return detail
