"""Small SDK read model over the employee's native ZenTao API session."""

from __future__ import annotations

from fastapi import HTTPException

from app.control_plane.models import AuthContext


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
