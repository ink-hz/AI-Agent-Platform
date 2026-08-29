from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class PartnerStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISABLED = "disabled"


class PartnerBindingStatus(StrEnum):
    PENDING = "pending"
    LINKED = "linked"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True)
class PartnerOrganization:
    partner_organization_id: UUID
    status: PartnerStatus


@dataclass(frozen=True)
class PartnerOperator:
    partner_operator_id: UUID
    subject_id: UUID
    partner_organization_id: UUID
    status: PartnerStatus


@dataclass(frozen=True)
class PartnerBindingRequest:
    binding_request_id: UUID
    status: PartnerBindingStatus | str
    expires_at: datetime


@dataclass(frozen=True)
class PartnerIdentityResolution:
    subject_id: UUID | None
    partner_operator_id: UUID | None
    partner_organization_id: UUID | None
    binding_request_id: UUID | None
    status: str


@dataclass(frozen=True)
class PartnerAccessDecision:
    allowed: bool
    reason: str
    subject_id: UUID | None = None


@dataclass(frozen=True)
class VerifiedProviderSubject:
    provider_kind: str
    provider_subject: str = field(repr=False)
    verified_at: datetime
    display_name: str | None = field(default=None, repr=False)


class PartnerIdentityError(RuntimeError):
    def __init__(self, code: str, status_code: int = 503) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)
