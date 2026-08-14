from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import sys
from typing import Callable, Iterable
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.local_secrets import SecretFileUnavailable, read_secret_file

from .crypto import (
    IdentityCryptoError,
    IdentityKeyring,
    ProtectedProviderId,
    ProviderIdentityCodec,
)
from .dingtalk import DingTalkClient, DingTalkMember, DingTalkProviderError
from .identity import IdentityResolutionError, IdentityResolver


_MAX_USERID_BYTES = 512
_MAX_FILE_BYTES = 4 * (_MAX_USERID_BYTES + 1)
_EXPECTED_DATABASE = "agent_platform_control_preview"
_EXPECTED_ROLE = "platform_directory_worker_preview"


class DemoBootstrapError(RuntimeError):
    """A stable preview-bootstrap failure containing no provider identifiers."""


@dataclass(frozen=True)
class DemoBootstrapResult:
    generation_id: UUID
    member_count: int
    digest_hex: str


@dataclass(frozen=True)
class _PreparedMember:
    member: DingTalkMember
    corporate: ProtectedProviderId
    union: ProtectedProviderId


def _validated_userids(values: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(values)
    if not 1 <= len(selected) <= 3 or len(set(selected)) != len(selected):
        raise DemoBootstrapError("demo userid file invalid")
    for value in selected:
        try:
            encoded = value.encode("utf-8")
        except (AttributeError, UnicodeError):
            raise DemoBootstrapError("demo userid file invalid") from None
        if (
            not value
            or value != value.strip()
            or "\0" in value
            or not 1 <= len(encoded) <= _MAX_USERID_BYTES
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        ):
            raise DemoBootstrapError("demo userid file invalid")
    return selected


def read_demo_userids(path: str | Path) -> tuple[str, ...]:
    """Read an allowlist without following links or accepting shared files."""
    descriptor = -1
    try:
        selected_path = Path(path)
        if not selected_path.is_absolute():
            raise ValueError
        metadata = selected_path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
        ):
            raise ValueError
        descriptor = os.open(
            selected_path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) not in {0o400, 0o600}
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise ValueError
        payload = os.read(descriptor, _MAX_FILE_BYTES + 1)
        if len(payload) > _MAX_FILE_BYTES:
            raise ValueError
        text = payload.decode("utf-8")
    except (OSError, TypeError, UnicodeError, ValueError):
        raise DemoBootstrapError("demo userid file unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    lines = text.splitlines()
    if not text or len(lines) == 0:
        raise DemoBootstrapError("demo userid file invalid")
    return _validated_userids(lines)


def _validate_preview_worker_dsn(database_url: str) -> None:
    message = "exact preview directory-worker DSN required"
    try:
        values = conninfo_to_dict(database_url)
    except (TypeError, ValueError, psycopg.Error):
        raise ValueError(message) from None
    if (
        values.get("dbname") != _EXPECTED_DATABASE
        or values.get("user") != _EXPECTED_ROLE
    ):
        raise ValueError(message)


def _digest_field(hasher: "hashlib._Hash", value: bytes) -> None:
    hasher.update(len(value).to_bytes(8, "big"))
    hasher.update(value)


def _directory_digest(members: tuple[_PreparedMember, ...]) -> bytes:
    hasher = hashlib.sha256()
    _digest_field(hasher, b"orbbec-demo-directory-v1")
    for prepared in members:
        for protected in (prepared.corporate, prepared.union):
            _digest_field(hasher, protected.subject_kind.encode("ascii"))
            _digest_field(
                hasher, protected.lookup_key_version.to_bytes(8, "big")
            )
            _digest_field(hasher, protected.lookup_hmac)
            _digest_field(
                hasher, protected.encryption_key_version.to_bytes(8, "big")
            )
            _digest_field(hasher, protected.ciphertext)
        _digest_field(hasher, b"1" if prepared.member.active else b"0")
        _digest_field(hasher, prepared.member.display_name.encode("utf-8"))
    return hasher.digest()


class DemoDirectoryBootstrap:
    def __init__(
        self,
        control_database_url: str,
        *,
        corp_id: str,
        client: DingTalkClient,
        identity_codec: ProviderIdentityCodec,
        connection_factory: Callable[..., object] = psycopg.connect,
        generation_id_factory: Callable[[], UUID] = uuid4,
        validate_connected_role: bool = True,
    ) -> None:
        _validate_preview_worker_dsn(control_database_url)
        if (
            not isinstance(corp_id, str)
            or not corp_id
            or corp_id != corp_id.strip()
            or "\0" in corp_id
        ):
            raise DemoBootstrapError("organization mismatch")
        if getattr(client, "_corp_id", None) != corp_id:
            raise DemoBootstrapError("organization mismatch")
        if not isinstance(identity_codec, ProviderIdentityCodec):
            raise DemoBootstrapError("demo_identity_keys_invalid")
        self._database_url = control_database_url
        self._corp_id = corp_id
        self._client = client
        self._codec = identity_codec
        self._connect = connection_factory
        self._generation_id_factory = generation_id_factory
        self._validate_connected_role = validate_connected_role

    def __repr__(self) -> str:
        return (
            "DemoDirectoryBootstrap(control_database_url=<redacted>, "
            "corp_id=<redacted>, client=<redacted>, identity_codec=<redacted>)"
        )

    async def _prepare(self, userids: tuple[str, ...]) -> tuple[_PreparedMember, ...]:
        members: list[DingTalkMember] = []
        try:
            for userid in userids:
                member = await self._client.get_member(userid)
                if member.userid != userid:
                    raise DemoBootstrapError("demo_member_invalid")
                if not member.active:
                    raise DemoBootstrapError("demo_member_inactive")
                if (
                    not member.unionid
                    or member.unionid != member.unionid.strip()
                    or "\0" in member.unionid
                    or not member.display_name
                    or member.display_name != member.display_name.strip()
                    or len(member.display_name) > 256
                    or any(
                        ord(character) < 0x20 or ord(character) == 0x7F
                        for character in member.display_name
                    )
                ):
                    raise DemoBootstrapError("demo_member_invalid")
                members.append(member)
        except DemoBootstrapError:
            raise
        except (DingTalkProviderError, KeyError, TypeError, ValueError):
            raise DemoBootstrapError("demo_provider_unavailable") from None

        try:
            return tuple(
                _PreparedMember(
                    member=member,
                    corporate=self._codec.seal(
                        IdentityResolver.CORPORATE_SUBJECT_KIND,
                        IdentityResolver.corporate_provider_id(
                            self._corp_id, member.userid
                        ),
                    ),
                    union=self._codec.seal(
                        IdentityResolver.UNION_SUBJECT_KIND, member.unionid
                    ),
                )
                for member in members
            )
        except (IdentityCryptoError, IdentityResolutionError):
            raise DemoBootstrapError("demo_identity_protection_failed") from None

    def _check_connection(self, connection: object) -> None:
        if not self._validate_connected_role:
            return
        row = connection.execute(
            "select current_database(), session_user"
        ).fetchone()
        if row != (_EXPECTED_DATABASE, _EXPECTED_ROLE):
            raise DemoBootstrapError("demo_database_identity_mismatch")

    def _check_key_policy(self, connection: object) -> None:
        row = connection.execute(
            "select lookup_transition_versions from "
            "platform_control.provider_identity_key_policies "
            "where provider='dingtalk'"
        ).fetchone()
        configured = tuple(row[0]) if row is not None else ()
        expected = tuple(self._codec.hmac.transition_versions or ())
        if configured != expected:
            raise DemoBootstrapError("demo_key_policy_mismatch")

    def _stage_member(
        self,
        connection: object,
        generation_id: UUID,
        prepared: _PreparedMember,
    ) -> None:
        connection.execute(
            "select platform_control.stage_verified_directory_member("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                generation_id,
                uuid4(),
                prepared.corporate.subject_kind,
                prepared.corporate.lookup_hmac,
                prepared.corporate.lookup_key_version,
                prepared.corporate.ciphertext,
                prepared.corporate.encryption_key_version,
                prepared.union.lookup_hmac,
                prepared.union.lookup_key_version,
                prepared.member.display_name,
                "active",
            ),
        )

    def _authoritative_promotion(self, generation_id: UUID) -> bool:
        connection = None
        try:
            connection = self._connect(
                self._database_url,
                connect_timeout=3,
                options="-c statement_timeout=10000",
            )
            self._check_connection(connection)
            row = connection.execute(
                "select state.active_generation_id,generation.status "
                "from platform_control.directory_state state left join "
                "platform_control.directory_generations generation on "
                "generation.generation_id=state.active_generation_id "
                "where state.singleton"
            ).fetchone()
            return row == (generation_id, "complete")
        except Exception:
            return False
        finally:
            if connection is not None:
                connection.close()

    async def run(self, userids: Iterable[str]) -> DemoBootstrapResult:
        selected_userids = _validated_userids(userids)
        prepared = await self._prepare(selected_userids)
        digest = _directory_digest(prepared)
        generation_id = self._generation_id_factory()
        connection = None
        promotion_invoked = False
        try:
            connection = self._connect(
                self._database_url,
                connect_timeout=3,
                options="-c statement_timeout=10000",
            )
            self._check_connection(connection)
            self._check_key_policy(connection)
            connection.execute(
                "select platform_control.begin_demo_directory_generation(%s,%s,%s)",
                (generation_id, len(prepared), digest),
            )
            for member in prepared:
                self._stage_member(connection, generation_id, member)
            connection.execute(
                "select platform_control.promote_verified_directory_generation(%s)",
                (generation_id,),
            )
            promotion_invoked = True
            connection.commit()
        except DemoBootstrapError:
            if connection is not None:
                connection.rollback()
            raise
        except psycopg.Error:
            if connection is not None:
                try:
                    connection.rollback()
                except psycopg.Error:
                    pass
            if promotion_invoked:
                if self._authoritative_promotion(generation_id):
                    return DemoBootstrapResult(
                        generation_id, len(prepared), digest.hex()
                    )
                raise DemoBootstrapError("demo_promotion_indeterminate") from None
            raise DemoBootstrapError("demo_database_unavailable") from None
        finally:
            if connection is not None:
                connection.close()
        return DemoBootstrapResult(generation_id, len(prepared), digest.hex())


def _required_secret_path(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise DemoBootstrapError("demo_configuration_unavailable")
    return value


async def _run_command(userid_file: str) -> DemoBootstrapResult:
    try:
        database_url = read_secret_file(
            _required_secret_path("PLATFORM_CONTROL_DIRECTORY_DATABASE_URL_FILE")
        )
        app_key = read_secret_file(
            _required_secret_path("PLATFORM_DINGTALK_APP_KEY_FILE")
        )
        app_secret = read_secret_file(
            _required_secret_path("PLATFORM_DINGTALK_APP_SECRET_FILE")
        )
        corp_id = read_secret_file(
            _required_secret_path("PLATFORM_DINGTALK_CORP_ID_FILE")
        )
        codec = ProviderIdentityCodec(
            IdentityKeyring.from_file(
                _required_secret_path("PLATFORM_IDENTITY_ENCRYPTION_KEYRING_FILE"),
                expected_purpose="provider-encryption",
                expected_key_length=32,
            ),
            IdentityKeyring.from_file(
                _required_secret_path("PLATFORM_IDENTITY_HMAC_KEYRING_FILE"),
                expected_purpose="provider-lookup-hmac",
                expected_key_length=32,
            ),
        )
    except (SecretFileUnavailable, IdentityCryptoError, ValueError):
        raise DemoBootstrapError("demo_configuration_unavailable") from None
    client = DingTalkClient(
        app_key=app_key,
        app_secret=app_secret,
        corp_id=corp_id,
        login_flow="qr",
    )
    try:
        return await DemoDirectoryBootstrap(
            database_url,
            corp_id=corp_id,
            client=client,
            identity_codec=codec,
        ).run(read_demo_userids(Path(userid_file)))
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--userid-file", required=True)
    namespace = parser.parse_args(argv)
    try:
        result = asyncio.run(_run_command(namespace.userid_file))
    except DemoBootstrapError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        f"DEMO_DIRECTORY_READY generation={result.generation_id} "
        f"members={result.member_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
