from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Annotated, Any
from urllib.parse import parse_qsl
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator
from starlette.responses import JSONResponse, RedirectResponse

from .agent_launch import AgentLaunchError
from .models import AuthContext, Role
from .partner_models import PartnerIdentityError, PartnerStatus
from .partner_provider import PartnerAuthenticationBroker, validate_partner_callback
from .partner_service import PartnerService
from .routes_manage import authenticated_context, csrf_protection

router = APIRouter(
    prefix="/api/v1/manage/partners",
    tags=["partner-identity-management"],
)

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _strict_json_uuid(value: object) -> UUID:
    if not isinstance(value, str):
        if isinstance(value, UUID):
            return value
        raise ValueError("UUID string required")
    try:
        selected = UUID(value)
    except ValueError:
        raise ValueError("UUID invalid") from None
    if str(selected) != value:
        raise ValueError("canonical UUID required")
    return selected


def _strict_partner_status(value: object) -> PartnerStatus:
    if isinstance(value, PartnerStatus):
        return value
    if not isinstance(value, str) or value not in {
        status.value for status in PartnerStatus
    }:
        raise ValueError("partner status invalid")
    return PartnerStatus(value)


StrictUUID = Annotated[UUID, BeforeValidator(_strict_json_uuid)]
StrictPartnerStatus = Annotated[PartnerStatus, BeforeValidator(_strict_partner_status)]


class _MutationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: str = Field(min_length=3, max_length=500)
    request_id: StrictUUID

    @field_validator("reason")
    @classmethod
    def _valid_reason(cls, value: str) -> str:
        selected = value.strip()
        if len(selected) < 3 or len(selected) > 500 or "\0" in selected:
            raise ValueError("reason invalid")
        return selected


class CreateOrganizationBody(_MutationBody):
    display_name: str = Field(min_length=1, max_length=512)

    @field_validator("display_name")
    @classmethod
    def _valid_display_name(cls, value: str) -> str:
        selected = value.strip()
        if not selected or "\0" in selected:
            raise ValueError("display name invalid")
        return selected


class CreateOperatorBody(CreateOrganizationBody):
    partner_organization_id: StrictUUID


class StatusBody(_MutationBody):
    status: StrictPartnerStatus


class LinkBindingBody(_MutationBody):
    partner_operator_id: StrictUUID


Auth = Annotated[AuthContext, Depends(authenticated_context)]


def _owner(context: AuthContext) -> None:
    if not isinstance(context, AuthContext):
        raise HTTPException(status_code=401, detail="authentication required")
    if context.role is not Role.PLATFORM_OWNER:
        raise HTTPException(status_code=403, detail="platform owner required")


def owner_context(context: Auth) -> AuthContext:
    _owner(context)
    return context


OwnerAuth = Annotated[AuthContext, Depends(owner_context)]


def partner_service(request: Request, _context: OwnerAuth) -> PartnerService:
    service = getattr(request.app.state, "partner_service", None)
    if not isinstance(service, PartnerService):
        raise HTTPException(status_code=503, detail="partner management unavailable")
    return service


def verified_csrf(verified: Annotated[bool, Depends(csrf_protection)]) -> bool:
    if not verified:
        raise HTTPException(status_code=403, detail="CSRF verification failed")
    return True


Service = Annotated[PartnerService, Depends(partner_service)]
Csrf = Annotated[bool, Depends(verified_csrf)]


def _value(record: object, name: str) -> Any:
    if isinstance(record, Mapping):
        return record[name]
    return getattr(record, name)


def _organization(record: object) -> dict[str, object]:
    return {
        "partner_organization_id": _value(record, "partner_organization_id"),
        "display_name": _value(record, "display_name"),
        "status": _value(record, "status"),
        "created_at": _value(record, "created_at"),
        "updated_at": _value(record, "updated_at"),
        "invalidated_at": _value(record, "invalidated_at"),
    }


def _operator(record: object) -> dict[str, object]:
    return {
        "partner_operator_id": _value(record, "partner_operator_id"),
        "subject_id": _value(record, "subject_id"),
        "partner_organization_id": _value(record, "partner_organization_id"),
        "display_name": _value(record, "display_name"),
        "status": _value(record, "status"),
        "fae_grant_active": _value(record, "fae_grant_active"),
        "fae_granted_at": _value(record, "fae_granted_at"),
        "created_at": _value(record, "created_at"),
        "updated_at": _value(record, "updated_at"),
        "invalidated_at": _value(record, "invalidated_at"),
    }


def _binding_request(record: object) -> dict[str, object]:
    return {
        "binding_request_id": _value(record, "binding_request_id"),
        "provider_kind": _value(record, "provider_kind"),
        "display_name": _value(record, "display_name"),
        "status": _value(record, "status"),
        "verified_at": _value(record, "verified_at"),
        "requested_at": _value(record, "requested_at"),
        "expires_at": _value(record, "expires_at"),
        "resolved_at": _value(record, "resolved_at"),
        "linked_partner_operator_id": _value(record, "linked_partner_operator_id"),
    }


def _read(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except PartnerIdentityError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code},
        ) from None


def _mutate(request_id: UUID, call: Callable[[], Any]) -> Any:
    try:
        return call()
    except PartnerIdentityError as error:
        if error.status_code >= 500:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "partner_mutation_indeterminate",
                    "request_id": str(request_id),
                },
            ) from None
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code},
        ) from None
    except Exception:  # noqa: BLE001 - unexpected mutation outcomes are indeterminate
        raise HTTPException(
            status_code=503,
            detail={
                "code": "partner_mutation_indeterminate",
                "request_id": str(request_id),
            },
        ) from None


def _matching(records: tuple[object, ...], field: str, selected: UUID) -> object:
    for record in records:
        if _value(record, field) == selected:
            return record
    raise PartnerIdentityError("partner_identity_unavailable")


@router.get("/organizations")
def list_partner_organizations(
    context: OwnerAuth, service: Service
) -> dict[str, object]:
    _owner(context)
    return {
        "organizations": _read(
            lambda: [_organization(item) for item in service.list_organizations()]
        )
    }


@router.post("/organizations")
def create_partner_organization(
    body: CreateOrganizationBody,
    context: OwnerAuth,
    service: Service,
    _csrf_verified: Csrf,
) -> dict[str, object]:
    _owner(context)

    def apply() -> dict[str, object]:
        created = service.create_organization(
            actor_id=context.internal_user_id,
            display_name=body.display_name,
            reason=body.reason,
            request_id=body.request_id,
        )
        projection = _matching(
            service.list_organizations(),
            "partner_organization_id",
            _value(created, "partner_organization_id"),
        )
        return {
            "request_id": body.request_id,
            "organization": _organization(projection),
        }

    return _mutate(body.request_id, apply)


@router.patch("/organizations/{organization_id}/status")
def set_partner_organization_status(
    organization_id: StrictUUID,
    body: StatusBody,
    context: OwnerAuth,
    service: Service,
    _csrf_verified: Csrf,
) -> dict[str, object]:
    _owner(context)

    def apply() -> dict[str, object]:
        service.set_organization_status(
            actor_id=context.internal_user_id,
            organization_id=organization_id,
            status=body.status,
            reason=body.reason,
            request_id=body.request_id,
        )
        projection = _matching(
            service.list_organizations(),
            "partner_organization_id",
            organization_id,
        )
        return {
            "request_id": body.request_id,
            "organization": _organization(projection),
        }

    return _mutate(body.request_id, apply)


@router.get("/operators")
def list_partner_operators(context: OwnerAuth, service: Service) -> dict[str, object]:
    _owner(context)
    return {
        "operators": _read(
            lambda: [_operator(item) for item in service.list_operators()]
        )
    }


@router.post("/operators")
def create_partner_operator(
    body: CreateOperatorBody,
    context: OwnerAuth,
    service: Service,
    _csrf_verified: Csrf,
) -> dict[str, object]:
    _owner(context)

    def apply() -> dict[str, object]:
        created = service.create_operator(
            actor_id=context.internal_user_id,
            partner_organization_id=body.partner_organization_id,
            display_name=body.display_name,
            reason=body.reason,
            request_id=body.request_id,
        )
        projection = _matching(
            service.list_operators(),
            "partner_operator_id",
            _value(created, "partner_operator_id"),
        )
        return {
            "request_id": body.request_id,
            "operator": _operator(projection),
        }

    return _mutate(body.request_id, apply)


@router.patch("/operators/{operator_id}/status")
def set_partner_operator_status(
    operator_id: StrictUUID,
    body: StatusBody,
    context: OwnerAuth,
    service: Service,
    _csrf_verified: Csrf,
) -> dict[str, object]:
    _owner(context)

    def apply() -> dict[str, object]:
        service.set_operator_status(
            actor_id=context.internal_user_id,
            operator_id=operator_id,
            status=body.status,
            reason=body.reason,
            request_id=body.request_id,
        )
        projection = _matching(
            service.list_operators(), "partner_operator_id", operator_id
        )
        return {
            "request_id": body.request_id,
            "operator": _operator(projection),
        }

    return _mutate(body.request_id, apply)


def _change_fae_grant(
    *,
    operator_id: StrictUUID,
    body: _MutationBody,
    context: AuthContext,
    service: PartnerService,
    revoke: bool,
) -> dict[str, object]:
    _owner(context)

    def apply() -> dict[str, object]:
        method = service.revoke_fae if revoke else service.grant_fae
        method(
            actor_id=context.internal_user_id,
            operator_id=operator_id,
            reason=body.reason,
            request_id=body.request_id,
        )
        projection = _matching(
            service.list_operators(), "partner_operator_id", operator_id
        )
        return {
            "request_id": body.request_id,
            "operator": _operator(projection),
        }

    return _mutate(body.request_id, apply)


@router.put("/operators/{operator_id}/fae-grant")
def grant_partner_fae(
    operator_id: StrictUUID,
    body: _MutationBody,
    context: OwnerAuth,
    service: Service,
    _csrf_verified: Csrf,
) -> dict[str, object]:
    return _change_fae_grant(
        operator_id=operator_id,
        body=body,
        context=context,
        service=service,
        revoke=False,
    )


@router.delete("/operators/{operator_id}/fae-grant")
def revoke_partner_fae(
    operator_id: StrictUUID,
    body: _MutationBody,
    context: OwnerAuth,
    service: Service,
    _csrf_verified: Csrf,
) -> dict[str, object]:
    return _change_fae_grant(
        operator_id=operator_id,
        body=body,
        context=context,
        service=service,
        revoke=True,
    )


@router.get("/binding-requests")
def list_partner_binding_requests(
    context: OwnerAuth, service: Service
) -> dict[str, object]:
    _owner(context)
    return {
        "binding_requests": _read(
            lambda: [_binding_request(item) for item in service.list_binding_requests()]
        )
    }


@router.post("/binding-requests/{request_id}/link")
def link_partner_binding_request(
    request_id: StrictUUID,
    body: LinkBindingBody,
    context: OwnerAuth,
    service: Service,
    _csrf_verified: Csrf,
) -> dict[str, object]:
    _owner(context)

    def apply() -> dict[str, object]:
        service.link_binding_request(
            actor_id=context.internal_user_id,
            binding_request_id=request_id,
            operator_id=body.partner_operator_id,
            reason=body.reason,
            request_id=body.request_id,
        )
        projection = _matching(
            service.list_binding_requests(), "binding_request_id", request_id
        )
        return {
            "request_id": body.request_id,
            "binding_request": _binding_request(projection),
        }

    return _mutate(body.request_id, apply)


@router.post("/binding-requests/{request_id}/reject")
def reject_partner_binding_request(
    request_id: StrictUUID,
    body: _MutationBody,
    context: OwnerAuth,
    service: Service,
    _csrf_verified: Csrf,
) -> dict[str, object]:
    _owner(context)

    def apply() -> dict[str, object]:
        service.reject_binding_request(
            actor_id=context.internal_user_id,
            binding_request_id=request_id,
            reason=body.reason,
            request_id=body.request_id,
        )
        projection = _matching(
            service.list_binding_requests(), "binding_request_id", request_id
        )
        return {
            "request_id": body.request_id,
            "binding_request": _binding_request(projection),
        }

    return _mutate(body.request_id, apply)


def _partner_auth_error(error: PartnerIdentityError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code},
        headers=_NO_STORE,
    )


def build_partner_auth_router(
    broker: PartnerAuthenticationBroker,
    *,
    agent_launch_service=None,
    callback_method: str = "GET",
    callback_path: str = "/partner-auth/callback",
) -> APIRouter:
    selected_method, callback_path = validate_partner_callback(
        callback_method, callback_path
    )

    auth_router = APIRouter(tags=["partner-authentication"])

    @auth_router.get("/partner-auth/start")
    def start_partner_authentication(request: Request):
        if request.url.query:
            raise HTTPException(
                status_code=400,
                detail={"code": "partner_return_path_invalid"},
                headers=_NO_STORE,
            )
        try:
            started = broker.begin_auth()
        except PartnerIdentityError as error:
            raise _partner_auth_error(error) from None
        return RedirectResponse(
            started.authorization_url,
            status_code=302,
            headers=_NO_STORE,
        )

    async def finish_partner_authentication(request: Request):
        callback: dict[str, str] = {}
        values: list[tuple[str, str]] = list(request.query_params.multi_items())
        if selected_method == "POST":
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 16_384:
                    raise HTTPException(
                        status_code=401,
                        detail={"code": "partner_auth_invalid"},
                        headers=_NO_STORE,
                    )
                body.extend(chunk)
            content_type = request.headers.get("content-type", "").split(";", 1)[0]
            if body:
                if content_type != "application/x-www-form-urlencoded":
                    raise HTTPException(
                        status_code=401,
                        detail={"code": "partner_auth_invalid"},
                        headers=_NO_STORE,
                    )
                try:
                    values.extend(
                        parse_qsl(
                            body.decode("utf-8"),
                            keep_blank_values=True,
                            strict_parsing=True,
                            max_num_fields=64,
                        )
                    )
                except (UnicodeError, ValueError):
                    raise HTTPException(
                        status_code=401,
                        detail={"code": "partner_auth_invalid"},
                        headers=_NO_STORE,
                    ) from None
        for key, value in values:
            if key in callback:
                raise HTTPException(
                    status_code=401,
                    detail={"code": "partner_auth_invalid"},
                    headers=_NO_STORE,
                )
            callback[key] = value
        try:
            completed = await broker.finish_auth(callback)
        except PartnerIdentityError as error:
            raise _partner_auth_error(error) from None
        if agent_launch_service is not None:
            try:
                issued = await agent_launch_service.issue_partner(
                    completed.subject_id
                )
            except AgentLaunchError as error:
                raise HTTPException(
                    status_code=error.status_code,
                    detail={"code": error.code},
                    headers=_NO_STORE,
                ) from None
            return RedirectResponse(
                issued.launch_url,
                status_code=302,
                headers=_NO_STORE,
            )
        return JSONResponse(
            {
                "status": completed.status,
                "return_path": completed.return_path,
            },
            headers=_NO_STORE,
        )

    auth_router.add_api_route(
        callback_path,
        finish_partner_authentication,
        methods=[selected_method],
    )
    return auth_router
