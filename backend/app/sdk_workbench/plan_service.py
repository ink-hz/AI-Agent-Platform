"""Enterprise access boundary for the SDK annual plan."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException

from app.control_plane.models import AuthContext, Role

from .plan_csv import PlanCsvError, parse_plan_csv


_READ_ROLES = frozenset({"viewer", "contributor", "reviewer", "release_manager"})


class PlanService:
    def __init__(self, members, plans) -> None:
        self.members = members
        self.plans = plans

    def _role(self, actor: AuthContext, *, write: bool = False) -> str:
        role = self.members.role_for(actor.internal_user_id)
        if role not in _READ_ROLES:
            raise HTTPException(403, "sdk_access_required")
        if write and actor.hard_stale_read_only:
            raise HTTPException(503, "hard_stale_read_only")
        return role

    def me(self, actor: AuthContext) -> dict:
        role = self.members.role_for(actor.internal_user_id)
        return {"role": role if role in _READ_ROLES else None,
                "can_manage": actor.role is Role.PLATFORM_OWNER,
                "read_only": actor.hard_stale_read_only,
                "requirements_enabled": False}

    def list_members(self, actor: AuthContext) -> dict:
        if actor.role is not Role.PLATFORM_OWNER:
            raise HTTPException(403, "sdk_owner_required")
        return {"items": self.members.members()}

    def grant_member(self, actor: AuthContext, target: UUID, role: str, request_id: UUID) -> dict:
        if actor.role is not Role.PLATFORM_OWNER:
            raise HTTPException(403, "sdk_owner_required")
        if actor.hard_stale_read_only:
            raise HTTPException(503, "hard_stale_read_only")
        return self.members.grant_member(actor.internal_user_id, target, role, request_id)

    def import_plan(self, actor: AuthContext, filename: str, payload: bytes) -> dict:
        role = self._role(actor, write=True)
        if role != "contributor":
            raise HTTPException(403, "sdk_role_required")
        if "/" in filename or "\\" in filename or not filename.lower().endswith(".csv"):
            raise HTTPException(422, "sdk_plan_filename_invalid")
        try:
            parsed = parse_plan_csv(payload)
        except PlanCsvError as error:
            raise HTTPException(422, str(error)) from None
        return self.plans.import_plan(actor.internal_user_id, filename, parsed)

    def list_batches(self, actor: AuthContext, page: int) -> dict:
        self._role(actor)
        return self.plans.list_batches(page=page)

    def list_rows(self, actor: AuthContext, import_id: int, page: int,
                  module: str | None, priority: str | None, owner: str | None) -> dict:
        self._role(actor)
        return self.plans.list_rows(import_id, page=page, module=module, priority=priority, owner=owner)

    def row(self, actor: AuthContext, row_id: int) -> dict:
        self._role(actor)
        return self.plans.row(row_id)

    def conversion_row(self, actor: AuthContext, row_id: int, request_id: UUID) -> dict:
        role = self._role(actor, write=True)
        if role != "contributor":
            raise HTTPException(403, "sdk_role_required")
        row = self.plans.row(row_id)
        if row["kind"] != "task":
            raise HTTPException(409, "sdk_plan_heading_not_convertible")
        if row["conversion_state"] != "rejected" and row["request_id"] is not None and row["request_id"] != str(request_id):
            raise HTTPException(409, "sdk_plan_row_already_converting")
        return row
