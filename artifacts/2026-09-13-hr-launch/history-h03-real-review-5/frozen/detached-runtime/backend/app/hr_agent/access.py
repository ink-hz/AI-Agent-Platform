"""Server-authoritative HR identity and current scope checks."""

from uuid import UUID

from app.control_plane.models import AuthContext

from .types import AuthorizedScope, problem, validate_contract


class HrAccess:
    def __init__(
        self,
        agent_use_authorization,
        *,
        object_authorizer=None,
        reference_authorizer=None,
    ):
        self.agent_use_authorization = agent_use_authorization
        self.object_authorizer = object_authorizer
        self.reference_authorizer = reference_authorizer

    def authorize_user(self, auth: AuthContext, *, writable: bool) -> UUID:
        if not isinstance(auth, AuthContext):
            raise problem("scope_denied", http_status=401)
        if writable and auth.hard_stale_read_only:
            raise problem("temporarily_unavailable", http_status=503)
        try:
            decision = self.agent_use_authorization.decide_for_user_id(
                auth.internal_user_id, "hr-bot"
            )
        except Exception:  # noqa: BLE001 - authorization provider errors deny safely
            raise problem(
                "temporarily_unavailable", retryable=True, http_status=503
            ) from None
        if not getattr(decision, "allowed", False):
            raise problem("scope_denied", http_status=403)
        return auth.internal_user_id

    def authorize_scope(
        self, owner_id: UUID, objects: tuple, refs: tuple, *, work_id: UUID | None
    ) -> AuthorizedScope:
        if not isinstance(owner_id, UUID):
            raise problem("scope_denied", http_status=401)
        for obj in objects:
            validate_contract("ObjectRef", obj)
            if self.object_authorizer is None or not self.object_authorizer(
                owner_id, obj
            ):
                raise problem("not_found", http_status=404)
        for ref in refs:
            validate_contract("ExactRef", ref)
            if self.reference_authorizer is None or not self.reference_authorizer(
                owner_id, ref, objects, work_id
            ):
                raise problem("reference_unavailable", http_status=410)
        return AuthorizedScope(owner_id, work_id, tuple(objects), tuple(refs))
