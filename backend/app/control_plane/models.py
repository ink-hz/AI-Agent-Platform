from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID


class Role(StrEnum):
    MEMBER = "member"
    MANAGEMENT_VIEWER = "management_viewer"
    PLATFORM_ADMIN = "platform_admin"
    PLATFORM_OWNER = "platform_owner"


@dataclass(frozen=True)
class AuthContext:
    internal_user_id: UUID
    role: Role
    session_id: UUID
    hard_stale_read_only: bool


@dataclass(frozen=True)
class IssuedWebSession:
    session_id: UUID
    cookie_token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    idle_expires_at: datetime
    absolute_expires_at: datetime


class IdentityMode(StrEnum):
    DISABLED = "disabled"
    PREVIEW = "preview"
    PRODUCTION = "production"


class DirectoryFreshness(StrEnum):
    FRESH = "fresh"
    WARNING = "warning"
    HARD_STALE = "hard_stale"


@dataclass(frozen=True)
class ControlUser:
    internal_user_id: UUID
    role: Role
    status: str
    last_confirmed_active: bool
    locally_invalidated_at: datetime | None


@dataclass(frozen=True)
class DirectoryState:
    active_generation_id: UUID | None
    last_complete_at: datetime | None
    freshness: DirectoryFreshness


@dataclass(frozen=True)
class StaleAccessDecision:
    allowed: bool
    read_only: bool
    reason: Literal[
        "fresh",
        "warning",
        "member_hard_stale",
        "privileged_last_generation",
        "locally_inactive",
        "unbound_identity",
    ]


@dataclass(frozen=True)
class ResolvedLoginIdentity:
    internal_user_id: UUID
    hard_stale_read_only: bool
    reason: str


@dataclass(frozen=True)
class ControlPlaneConfig:
    mode: IdentityMode
    control_database_url_file: str
    audit_database_url_file: str
    public_base_url: str
    route_prefix: str
    cookie_name: str
    dingtalk_app_key: str
    dingtalk_agent_id: str
    dingtalk_corp_id: str
    dingtalk_app_secret_file: str
    encryption_keyring_file: str
    hmac_keyring_file: str
    rate_limit_hmac_keyring_file: str = ""
    reconcile_interval_seconds: int = 21_600
    warning_after_seconds: int = 28_800
    hard_stale_after_seconds: int = 86_400
    trusted_proxy_cidrs: tuple[str, ...] = ("127.0.0.1/32", "::1/128")
    login_starts_per_challenge: int = 5
    login_challenge_window_seconds: int = 600
    active_login_attempts: int = 3
    oauth_state_ttl_seconds: int = 300
    edge_login_starts_per_minute: int = 600
    edge_login_burst: int = 1_200
    edge_callbacks_per_minute: int = 1_200
    oauth_exchange_concurrency: int = 100
    oauth_exchanges_per_minute: int = 3_000
    authenticated_reads_per_minute: int = 300
    authenticated_mutations_per_minute: int = 60
    dingtalk_in_client_apps_file: str = ""
