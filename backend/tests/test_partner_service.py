from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.control_plane.crypto import IdentityKeyring
from app.control_plane.partner_identity_crypto import PartnerProviderIdentityCodec
from app.control_plane.partner_models import (
    PartnerBindingRequest,
    PartnerIdentityError,
    PartnerIdentityResolution,
    PartnerOperator,
    PartnerOrganization,
    PartnerStatus,
    VerifiedProviderSubject,
)
from app.control_plane.partner_repository import PartnerRepositoryError
from app.control_plane.partner_service import PartnerService
from app.execution_relay.content_crypto import ContentCodec

NOW = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)
OWNER_ID = UUID("10000000-0000-4000-8000-000000000001")
REQUEST_ID = UUID("20000000-0000-4000-8000-000000000001")


class _Audit:
    def __init__(self) -> None:
        self.fail = False
        self.events: list[dict[str, object]] = []

    def append(self, **event: object) -> None:
        if self.fail:
            raise PartnerRepositoryError("required_audit_unavailable")
        self.events.append(event)


class _MemoryPartnerRepository:
    """Model the repository's database-atomic mutation/audit contract."""

    def __init__(self, audit: _Audit) -> None:
        self.audit = audit
        self.organizations: dict[UUID, PartnerOrganization] = {}
        self.operators: dict[UUID, PartnerOperator] = {}
        self.subject_statuses: dict[UUID, PartnerStatus] = {}
        self.granted_subjects: set[UUID] = set()
        self.binding_requests: dict[UUID, PartnerBindingRequest] = {}
        self.binding_protected: dict[UUID, object] = {}
        self.identities: dict[tuple[str, bytes, int], PartnerIdentityResolution] = {}
        self.login_attempts: set[UUID] = set()
        self.launch_codes: set[UUID] = set()

    @staticmethod
    def _identity_key(protected) -> tuple[str, bytes, int]:
        return (
            protected.provider_kind,
            protected.provider_subject_lookup_hmac,
            protected.lookup_key_version,
        )

    def list_organizations(self) -> tuple[PartnerOrganization, ...]:
        return tuple(
            sorted(
                self.organizations.values(),
                key=lambda item: item.partner_organization_id.hex,
            )
        )

    def create_organization(self, **values) -> PartnerOrganization:
        record = PartnerOrganization(
            values["partner_organization_id"], PartnerStatus.ACTIVE
        )
        self.audit.append(
            event_type="partner_organization_created",
            actor_id=values["actor_id"],
            target_id=record.partner_organization_id,
            request_id=values["request_id"],
            reason=values["reason"],
        )
        self.organizations[record.partner_organization_id] = record
        return record

    def create_operator(self, **values) -> PartnerOperator:
        organization = self.organizations[values["partner_organization_id"]]
        if organization.status is not PartnerStatus.ACTIVE:
            raise PartnerRepositoryError("organization_inactive", 409)
        record = PartnerOperator(
            partner_operator_id=values["partner_operator_id"],
            subject_id=values["subject_id"],
            partner_organization_id=values["partner_organization_id"],
            status=PartnerStatus.ACTIVE,
        )
        self.audit.append(
            event_type="partner_operator_created",
            actor_id=values["actor_id"],
            target_id=record.partner_operator_id,
            request_id=values["request_id"],
            reason=values["reason"],
        )
        self.operators[record.partner_operator_id] = record
        self.subject_statuses[record.subject_id] = PartnerStatus.ACTIVE
        self.last_operator_values = values
        return record

    def set_organization_status(self, **values) -> PartnerOrganization:
        current = self.organizations[values["partner_organization_id"]]
        updated = replace(current, status=PartnerStatus(values["status"]))
        self.audit.append(
            event_type="partner_organization_status_changed",
            actor_id=values["actor_id"],
            target_id=current.partner_organization_id,
            request_id=values["request_id"],
            reason=values["reason"],
        )
        self.organizations[current.partner_organization_id] = updated
        return updated

    def set_operator_status(self, **values) -> PartnerOperator:
        current = self.operators[values["partner_operator_id"]]
        updated = replace(current, status=PartnerStatus(values["status"]))
        self.audit.append(
            event_type="partner_operator_status_changed",
            actor_id=values["actor_id"],
            target_id=current.partner_operator_id,
            request_id=values["request_id"],
            reason=values["reason"],
        )
        self.operators[current.partner_operator_id] = updated
        return updated

    def grant_fae(self, **values) -> None:
        operator = self.operators[values["partner_operator_id"]]
        self.audit.append(
            event_type="partner_fae_granted",
            actor_id=values["actor_id"],
            target_id=operator.subject_id,
            request_id=values["request_id"],
            reason=values["reason"],
        )
        self.granted_subjects.add(operator.subject_id)

    def revoke_fae(self, **values) -> None:
        operator = self.operators[values["partner_operator_id"]]
        self.audit.append(
            event_type="partner_fae_revoked",
            actor_id=values["actor_id"],
            target_id=operator.subject_id,
            request_id=values["request_id"],
            reason=values["reason"],
        )
        self.granted_subjects.discard(operator.subject_id)

    def decide_fae_access(self, subject_id: UUID):
        subject_status = self.subject_statuses.get(subject_id)
        if subject_status is not PartnerStatus.ACTIVE:
            return SimpleNamespace(reason="subject_inactive")
        operator = next(
            (item for item in self.operators.values() if item.subject_id == subject_id),
            None,
        )
        if operator is None:
            return SimpleNamespace(reason="fae_access_denied")
        organization = self.organizations[operator.partner_organization_id]
        if organization.status is not PartnerStatus.ACTIVE:
            return SimpleNamespace(reason="organization_inactive")
        if operator.status is not PartnerStatus.ACTIVE:
            return SimpleNamespace(reason="operator_inactive")
        if subject_id not in self.granted_subjects:
            return SimpleNamespace(reason="fae_access_denied")
        return SimpleNamespace(reason="active")

    def resolve_provider_identity(self, protected):
        return self.identities.get(self._identity_key(protected))

    def record_binding_request(self, **values) -> PartnerBindingRequest:
        protected = values["protected_identity"]
        key = self._identity_key(protected)
        for request_id, stored in self.binding_protected.items():
            if self._identity_key(stored) == key:
                return self.binding_requests[request_id]
        record = PartnerBindingRequest(
            binding_request_id=values["binding_request_id"],
            status="pending",
            expires_at=NOW + timedelta(hours=24),
        )
        self.binding_requests[record.binding_request_id] = record
        self.binding_protected[record.binding_request_id] = protected
        return record

    def link_binding_request(self, **values) -> PartnerIdentityResolution:
        request = self.binding_requests[values["binding_request_id"]]
        operator = self.operators[values["partner_operator_id"]]
        organization = self.organizations[operator.partner_organization_id]
        if (
            request.status != "pending"
            or organization.status is not PartnerStatus.ACTIVE
        ):
            raise PartnerRepositoryError("binding_request_unavailable", 409)
        self.audit.append(
            event_type="partner_identity_linked",
            actor_id=values["actor_id"],
            target_id=request.binding_request_id,
            request_id=values["request_id"],
            reason=values["reason"],
        )
        self.binding_requests[request.binding_request_id] = replace(
            request, status="linked"
        )
        resolved = PartnerIdentityResolution(
            subject_id=operator.subject_id,
            partner_operator_id=operator.partner_operator_id,
            partner_organization_id=operator.partner_organization_id,
            binding_request_id=request.binding_request_id,
            status="linked",
        )
        protected = self.binding_protected[request.binding_request_id]
        self.identities[self._identity_key(protected)] = resolved
        return resolved

    def reject_binding_request(self, **values) -> PartnerBindingRequest:
        request = self.binding_requests[values["binding_request_id"]]
        if request.status != "pending":
            raise PartnerRepositoryError("binding_request_unavailable", 409)
        self.audit.append(
            event_type="partner_identity_rejected",
            actor_id=values["actor_id"],
            target_id=request.binding_request_id,
            request_id=values["request_id"],
            reason=values["reason"],
        )
        rejected = replace(request, status="rejected")
        self.binding_requests[request.binding_request_id] = rejected
        return rejected


@pytest.fixture
def audit() -> _Audit:
    return _Audit()


@pytest.fixture
def codec() -> PartnerProviderIdentityCodec:
    return PartnerProviderIdentityCodec(
        IdentityKeyring(1, "partner-provider-encryption", {1: b"e" * 32}),
        IdentityKeyring(
            1,
            "partner-provider-lookup-hmac",
            {1: b"h" * 32},
            transition_versions=(1,),
        ),
    )


@pytest.fixture
def content_codec() -> ContentCodec:
    return ContentCodec(
        IdentityKeyring(1, "platform-content-encryption", {1: b"c" * 32})
    )


@pytest.fixture
def repository(audit: _Audit) -> _MemoryPartnerRepository:
    return _MemoryPartnerRepository(audit)


@pytest.fixture
def service(repository, codec, content_codec) -> PartnerService:
    return PartnerService(
        repository,
        identity_codec=codec,
        content_codec=content_codec,
        now=lambda: NOW,
    )


def _seed_partner(service: PartnerService):
    organization = service.create_organization(
        actor_id=OWNER_ID,
        display_name="合作方甲",
        reason="客服试点",
        request_id=uuid4(),
    )
    operator = service.create_operator(
        actor_id=OWNER_ID,
        partner_organization_id=organization.partner_organization_id,
        display_name="坐席甲",
        reason="客服试点",
        request_id=uuid4(),
    )
    return organization, operator


def test_partner_mutation_fails_closed_when_atomic_audit_append_fails(
    service: PartnerService,
    audit: _Audit,
) -> None:
    audit.fail = True

    with pytest.raises(PartnerIdentityError, match="^required_audit_unavailable$"):
        service.create_organization(
            actor_id=OWNER_ID,
            display_name="合作方甲",
            reason="客服试点",
            request_id=REQUEST_ID,
        )

    assert service.list_organizations() == ()


def test_service_decrypts_only_safe_partner_management_projections(
    codec: PartnerProviderIdentityCodec,
    content_codec: ContentCodec,
) -> None:
    organization_id = uuid4()
    operator_id = uuid4()
    subject_id = uuid4()
    binding_request_id = uuid4()
    organization_name = content_codec.seal_json(
        f"partner-organization-display:{organization_id}",
        {"display_name": "合作方甲"},
    )
    operator_name = content_codec.seal_json(
        f"agent-subject-display:{subject_id}",
        {"display_name": "合作方客服"},
    )
    request_name = content_codec.seal_json(
        f"partner-binding-display:{binding_request_id}",
        {"display_name": "待绑定坐席"},
    )
    repository = SimpleNamespace(
        list_organizations=lambda: (
            {
                "partner_organization_id": organization_id,
                "status": "active",
                "name_ciphertext": organization_name.ciphertext,
                "name_key_version": organization_name.key_version,
                "created_at": NOW,
                "updated_at": NOW,
                "invalidated_at": None,
            },
        ),
        list_operators=lambda: (
            {
                "partner_operator_id": operator_id,
                "subject_id": subject_id,
                "partner_organization_id": organization_id,
                "status": "active",
                "display_name_ciphertext": operator_name.ciphertext,
                "display_name_key_version": operator_name.key_version,
                "fae_granted_at": NOW,
                "created_at": NOW,
                "updated_at": NOW,
                "invalidated_at": None,
            },
        ),
        list_binding_requests=lambda: (
            {
                "binding_request_id": binding_request_id,
                "provider_kind": "qianniu",
                "display_name_ciphertext": request_name.ciphertext,
                "display_name_key_version": request_name.key_version,
                "status": "pending",
                "verified_at": NOW,
                "requested_at": NOW,
                "expires_at": NOW + timedelta(hours=24),
                "resolved_at": None,
                "linked_partner_operator_id": None,
            },
        ),
    )
    service = PartnerService(
        repository,
        identity_codec=codec,
        content_codec=content_codec,
        now=lambda: NOW,
    )

    organization = service.list_organizations()[0]
    operator = service.list_operators()[0]
    request = service.list_binding_requests()[0]

    assert organization.display_name == "合作方甲"
    assert operator.display_name == "合作方客服"
    assert operator.fae_grant_active is True
    assert request.display_name == "待绑定坐席"
    assert request.provider_kind == "qianniu"
    serialized_names = {field for field in vars(request)}
    assert not any(
        forbidden in field
        for field in serialized_names
        for forbidden in (
            "provider_subject",
            "ciphertext",
            "lookup_hmac",
            "token",
            "secret",
        )
    )


def test_management_projection_decryption_fails_closed(
    service: PartnerService,
    repository: _MemoryPartnerRepository,
) -> None:
    repository.list_organizations = lambda: (
        {
            "partner_organization_id": uuid4(),
            "status": "active",
            "name_ciphertext": b"not-valid-ciphertext-but-long-enough",
            "name_key_version": 1,
            "created_at": NOW,
            "updated_at": NOW,
            "invalidated_at": None,
        },
    )

    with pytest.raises(PartnerIdentityError, match="^partner_identity_unavailable$"):
        service.list_organizations()


@pytest.mark.parametrize("provider_kind", ["   ", " partner-sso "])
def test_binding_projection_validation_fails_closed_as_unavailable(
    provider_kind: str,
    codec: PartnerProviderIdentityCodec,
    content_codec: ContentCodec,
) -> None:
    repository = SimpleNamespace(
        list_binding_requests=lambda: (
            {
                "binding_request_id": uuid4(),
                "provider_kind": provider_kind,
                "display_name_ciphertext": None,
                "display_name_key_version": None,
                "status": "pending",
                "verified_at": NOW,
                "requested_at": NOW,
                "expires_at": NOW + timedelta(hours=24),
                "resolved_at": None,
                "linked_partner_operator_id": None,
            },
        )
    )
    service = PartnerService(
        repository,
        identity_codec=codec,
        content_codec=content_codec,
        now=lambda: NOW,
    )

    with pytest.raises(
        PartnerIdentityError, match="^partner_identity_unavailable$"
    ) as captured:
        service.list_binding_requests()

    assert captured.value.status_code == 503


def test_reject_binding_request_is_audited_and_fails_closed(
    service: PartnerService,
    repository: _MemoryPartnerRepository,
    audit: _Audit,
) -> None:
    pending = service.resolve_verified_identity(
        VerifiedProviderSubject(
            provider_kind="partner-sso",
            provider_subject="synthetic-rejected-seat",
            verified_at=NOW,
        )
    )
    audit.fail = True

    with pytest.raises(PartnerIdentityError, match="^required_audit_unavailable$"):
        service.reject_binding_request(
            actor_id=OWNER_ID,
            binding_request_id=pending.binding_request_id,
            reason="not on pilot roster",
            request_id=REQUEST_ID,
        )

    assert repository.binding_requests[pending.binding_request_id].status == "pending"
    audit.fail = False
    rejected = service.reject_binding_request(
        actor_id=OWNER_ID,
        binding_request_id=pending.binding_request_id,
        reason="not on pilot roster",
        request_id=REQUEST_ID,
    )
    assert rejected.status == "rejected"


def test_create_operator_seals_display_name_and_does_not_auto_grant(
    service: PartnerService,
    repository: _MemoryPartnerRepository,
) -> None:
    _organization, operator = _seed_partner(service)

    values = repository.last_operator_values
    assert values["display_name_ciphertext"] != b"\xe5\x9d\x90\xe5\xb8\xad\xe7\x94\xb2"
    assert (
        b"\xe5\x9d\x90\xe5\xb8\xad\xe7\x94\xb2" not in values["display_name_ciphertext"]
    )
    assert values["display_name_key_version"] == 1
    assert operator.subject_id not in repository.granted_subjects
    assert service.decide_fae_access(operator.subject_id).reason == "fae_access_denied"


def test_partner_scope_requires_all_four_active_layers(
    service: PartnerService,
    repository: _MemoryPartnerRepository,
) -> None:
    organization, operator = _seed_partner(service)
    service.grant_fae(
        actor_id=OWNER_ID,
        operator_id=operator.partner_operator_id,
        reason="客服试点",
        request_id=uuid4(),
    )

    allowed = service.decide_fae_access(operator.subject_id)
    assert allowed.allowed is True
    assert allowed.subject_id == operator.subject_id
    assert allowed.reason == "active"

    service.set_operator_status(
        actor_id=OWNER_ID,
        operator_id=operator.partner_operator_id,
        status="suspended",
        reason="contract ended",
        request_id=uuid4(),
    )
    assert service.decide_fae_access(operator.subject_id).reason == "operator_inactive"
    service.set_operator_status(
        actor_id=OWNER_ID,
        operator_id=operator.partner_operator_id,
        status="active",
        reason="contract restored",
        request_id=uuid4(),
    )
    service.set_organization_status(
        actor_id=OWNER_ID,
        organization_id=organization.partner_organization_id,
        status="disabled",
        reason="partner disabled",
        request_id=uuid4(),
    )
    assert (
        service.decide_fae_access(operator.subject_id).reason == "organization_inactive"
    )
    service.set_organization_status(
        actor_id=OWNER_ID,
        organization_id=organization.partner_organization_id,
        status="active",
        reason="partner restored",
        request_id=uuid4(),
    )
    repository.subject_statuses[operator.subject_id] = PartnerStatus.SUSPENDED
    assert service.decide_fae_access(operator.subject_id).reason == "subject_inactive"
    repository.subject_statuses[operator.subject_id] = PartnerStatus.ACTIVE
    service.revoke_fae(
        actor_id=OWNER_ID,
        operator_id=operator.partner_operator_id,
        reason="access revoked",
        request_id=uuid4(),
    )
    assert service.decide_fae_access(operator.subject_id).reason == "fae_access_denied"


def test_access_decision_fails_closed_on_unrecognized_repository_reason(
    service: PartnerService,
    repository: _MemoryPartnerRepository,
) -> None:
    repository.decide_fae_access = lambda _subject_id: SimpleNamespace(
        reason="unexpected_reason"
    )

    with pytest.raises(PartnerIdentityError, match="^partner_identity_unavailable$"):
        service.decide_fae_access(uuid4())


def test_unknown_verified_identity_only_creates_one_pending_binding_request(
    service: PartnerService,
    repository: _MemoryPartnerRepository,
) -> None:
    verified = VerifiedProviderSubject(
        provider_kind="qianniu",
        provider_subject="raw-seat-42",
        verified_at=NOW,
        display_name="坐席甲",
    )

    first = service.resolve_verified_identity(verified)
    second = service.resolve_verified_identity(verified)

    assert first == second
    assert first.subject_id is None
    assert first.status == "pending"
    assert len(repository.binding_requests) == 1
    assert repository.operators == {}
    assert repository.subject_statuses == {}
    assert repository.granted_subjects == set()
    assert repository.login_attempts == set()
    assert repository.launch_codes == set()
    assert "raw-seat-42" not in repr(verified)
    assert "\u5750\u5e2d\u7532" not in repr(verified)


def test_resolve_retries_when_identity_links_before_pending_record(
    service: PartnerService,
    repository: _MemoryPartnerRepository,
) -> None:
    linked = PartnerIdentityResolution(
        subject_id=uuid4(),
        partner_operator_id=uuid4(),
        partner_organization_id=uuid4(),
        binding_request_id=None,
        status="linked",
    )
    resolve_calls = 0

    def resolve_after_race(_protected):
        nonlocal resolve_calls
        resolve_calls += 1
        return None if resolve_calls == 1 else linked

    def reject_stale_pending(**_values):
        raise PartnerRepositoryError("partner_identity_already_linked", 409)

    repository.resolve_provider_identity = resolve_after_race
    repository.record_binding_request = reject_stale_pending

    resolved = service.resolve_verified_identity(
        VerifiedProviderSubject(
            provider_kind="partner-sso",
            provider_subject="linked-during-resolution",
            verified_at=NOW,
        )
    )

    assert resolved == linked
    assert resolve_calls == 2


def test_owner_links_pending_identity_to_existing_active_operator(
    service: PartnerService,
) -> None:
    _organization, operator = _seed_partner(service)
    verified = VerifiedProviderSubject(
        provider_kind="partner-sso",
        provider_subject="synthetic-seat-7",
        verified_at=NOW,
    )
    pending = service.resolve_verified_identity(verified)

    linked = service.link_binding_request(
        actor_id=OWNER_ID,
        binding_request_id=pending.binding_request_id,
        operator_id=operator.partner_operator_id,
        reason="verified pilot roster",
        request_id=uuid4(),
    )
    resolved = service.resolve_verified_identity(verified)

    assert linked.subject_id == operator.subject_id
    assert linked.status == "linked"
    assert resolved.subject_id == operator.subject_id
    assert resolved.partner_operator_id == operator.partner_operator_id
