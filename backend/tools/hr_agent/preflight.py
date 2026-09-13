"""Read-only HR cloud runtime preflight; never starts services or calls a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from app.hr_agent.config import (
    CANDIDATE_INTAKE_MIGRATION_SHA256,
    CUTOVER_MIGRATION_SHA256,
    ERASURE_ACCESS_MIGRATION_SHA256,
    INTERVIEW_RECORD_MIGRATION_SHA256,
    MATERIAL_AUTHORITY_MIGRATION_SHA256,
    MIGRATION_SHA256,
    PARSING_MIGRATION_SHA256,
    check_schema_ready,
    load_hr_agent_settings,
)
from app.hr_agent.knowledge import KnowledgeReleases

EXPECTED_MIGRATIONS = {
    96: MIGRATION_SHA256,
    97: PARSING_MIGRATION_SHA256,
    98: MATERIAL_AUTHORITY_MIGRATION_SHA256,
    99: CANDIDATE_INTAKE_MIGRATION_SHA256,
    100: ERASURE_ACCESS_MIGRATION_SHA256,
    101: INTERVIEW_RECORD_MIGRATION_SHA256,
}
CUTOVER_MIGRATION = (
    Path(__file__).parents[2] / "control_migrations" / ("102_hr_execution_cutover.sql")
)
DRAIN_OCCUPANCY_MIGRATION = (
    Path(__file__).parents[2] / "control_migrations" / "103_hr_execution_drain_occupancy.sql"
)
DRAIN_TERMINAL_MIGRATION = (
    Path(__file__).parents[2] / "control_migrations" / "104_hr_execution_drain_terminal_contract.sql"
)

CLOUD_RESUME_MIGRATION = Path(__file__).parents[2] / "control_migrations" / "105_hr_cloud_resume.sql"

ATTACHMENT_KEYS = (
    "PLATFORM_ATTACHMENT_S3_ENDPOINT",
    "PLATFORM_ATTACHMENT_S3_BUCKET",
    "PLATFORM_ATTACHMENT_S3_ACCESS_KEY_FILE",
    "PLATFORM_ATTACHMENT_S3_SECRET_KEY_FILE",
    "PLATFORM_ATTACHMENT_CONTROL_DATABASE_URL_FILE",
    "PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE",
)


def _fingerprint(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_fingerprint(value: str) -> str:
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError
    return _fingerprint(path.read_bytes())


def _protected_file_present(value: str) -> bool:
    path = Path(value)
    try:
        metadata = path.stat()
        return (
            path.is_absolute()
            and not path.is_symlink()
            and stat.S_ISREG(metadata.st_mode)
            and stat.S_IMODE(metadata.st_mode) == 0o600
        )
    except Exception:  # noqa: BLE001 - protected-path failures collapse to absence
        return False


def read_environment_file(path_value) -> dict[str, str]:
    """Read a literal protected KEY=VALUE snapshot without shell evaluation."""

    try:
        path = Path(path_value)
        metadata = path.stat()
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
        ):
            raise ValueError
        result: dict[str, str] = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw or raw.startswith("#"):
                continue
            name, separator, value = raw.partition("=")
            if (
                separator != "="
                or not name
                or not name.replace("_", "A").isalnum()
                or not (name[0].isalpha() or name[0] == "_")
            ):
                raise ValueError
            result[name] = value
        return result
    except Exception:  # noqa: BLE001 - never expose protected input errors
        raise ValueError("runtime environment unavailable") from None


def _attachment_report(environment: dict[str, str]) -> dict:
    enabled = environment.get("PLATFORM_CONVERSATION_ATTACHMENT_ENABLED", "0")
    if enabled not in {"0", "1"}:
        return {"enabled": False, "wiring_present": False, "fingerprint": None}
    if enabled == "0":
        return {"enabled": False, "wiring_present": False, "fingerprint": None}
    try:
        if any(not environment.get(name) for name in ATTACHMENT_KEYS):
            raise ValueError
        file_keys = [name for name in ATTACHMENT_KEYS if name.endswith("_FILE")]
        if not all(_protected_file_present(environment[name]) for name in file_keys):
            raise ValueError
        identity = json.dumps(
            {
                "endpoint_hash": _fingerprint(
                    environment["PLATFORM_ATTACHMENT_S3_ENDPOINT"].encode()
                ),
                "bucket_hash": _fingerprint(
                    environment["PLATFORM_ATTACHMENT_S3_BUCKET"].encode()
                ),
                # Credential and DSN bytes are deliberately excluded. The
                # content keyring is an operational identity, while other
                # protected files contribute presence only.
                "content_keyring_fingerprint": _file_fingerprint(
                    environment["PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE"]
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return {
            "enabled": True,
            "wiring_present": True,
            "fingerprint": _fingerprint(identity),
        }
    except Exception:  # noqa: BLE001 - attachment failures are a boolean gate
        return {"enabled": True, "wiring_present": False, "fingerprint": None}


def _runtime_report(environment: dict[str, str]) -> tuple[dict | None, str | None]:
    try:
        settings = load_hr_agent_settings(environment)
        if not settings.enabled:
            raise ValueError
        knowledge = KnowledgeReleases(settings.knowledge_dir).current()
        knowledge.check()
        profiles = {}
        for label, key in (
            ("provider", "PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE"),
            ("budget", "PLATFORM_HR_AGENT_BUDGET_PROFILE_FILE"),
            ("diagnostic", "PLATFORM_HR_AGENT_DIAGNOSTIC_PROFILE_FILE"),
        ):
            profiles[label] = _file_fingerprint(environment[key])
        if settings.release_policy_file is not None:
            profiles["release_policy"] = _file_fingerprint(str(settings.release_policy_file))
        report = {
            "d7_product_approved": bool(settings.release_policy),
            "configuration_fingerprint": settings.configuration_revision,
            "hr_content_keyring_fingerprint": _file_fingerprint(
                environment["PLATFORM_HR_AGENT_CONTENT_KEYRING_FILE"]
            ),
            "profile_fingerprints": profiles,
            "provider": {
                "id": settings.provider_profile["id"],
                "revision": settings.provider_profile["revision"],
            },
            # Budget metadata is protected configuration. Its optional id was
            # historically unconstrained, so render only the file fingerprint.
            "budget": {"fingerprint": profiles["budget"]},
            "knowledge": {
                "release_id": knowledge.release_id,
                "manifest_fingerprint": knowledge.manifest_sha,
            },
            "attachments": _attachment_report(environment),
        }
        return report, None
    except Exception:  # noqa: BLE001 - runtime failures are deliberately scrubbed
        # Distinguish immutable knowledge corruption without exposing raw errors.
        try:
            settings = load_hr_agent_settings(environment)
        except Exception:  # noqa: BLE001 - configuration errors are scrubbed
            return None, "configuration_invalid"
        try:
            KnowledgeReleases(settings.knowledge_dir).current().check()
        except Exception:  # noqa: BLE001 - knowledge errors are scrubbed
            return None, "knowledge_invalid"
        return None, "configuration_invalid"


def _database_report(connection_factory) -> tuple[dict, list[str]]:
    blockers: list[str] = []
    migrations = []
    cutover = {"available": False, "initialized": False, "phase": None}
    permissions = {
        "app_gate_select": False,
        "app_gate_write": False,
        "app_admin_execute": False,
        "maintenance_admin_execute": False,
    }
    permissions_complete = False
    try:
        expected_migrations = dict(EXPECTED_MIGRATIONS)
        expected_migrations.update(CUTOVER_MIGRATION_SHA256)
        image_digests = {}
        for version, path in ((102, CUTOVER_MIGRATION), (103, DRAIN_OCCUPANCY_MIGRATION),
                              (104, DRAIN_TERMINAL_MIGRATION), (105, CLOUD_RESUME_MIGRATION)):
            try:
                image_digests[version] = _fingerprint(path.read_bytes())
            except OSError:
                image_digests[version] = None
        with connection_factory() as connection:
            rows = connection.execute(
                "select version,sha256 from platform_control.schema_migrations "
                "where version=any(%s) order by version",
                (list(expected_migrations),),
            ).fetchall()
            found = {int(row[0]): row[1] for row in rows}
            for version, expected in expected_migrations.items():
                actual = found.get(version)
                safe_actual = (
                    actual
                    if isinstance(actual, str) and re.fullmatch(r"[0-9a-f]{64}", actual)
                    else None
                )
                migrations.append(
                    {
                        "version": version,
                        "present": actual is not None,
                        "match": safe_actual == expected,
                        "expected_sha256": expected,
                        "actual_sha256": safe_actual,
                        **({"image_match": image_digests[version] == expected,
                            "image_sha256": image_digests[version]} if version in image_digests else {}),
                    }
                )
            table = connection.execute(
                "select to_regclass('platform_control.hr_execution_cutover')"
            ).fetchone()
            if table and table[0] is not None:
                cutover["available"] = True
                rows = connection.execute(
                    "select singleton,phase,epoch,transitioned_at,row_version "
                    "from platform_control.hr_execution_cutover"
                ).fetchall()
                if len(rows) == 1 and rows[0][0] is True:
                    cutover.update(
                        {
                            "initialized": True,
                            "phase": rows[0][1],
                            "epoch": rows[0][2],
                            "row_version": rows[0][4],
                        }
                    )
            roles = connection.execute(
                "select current_user,case current_user "
                "when 'platform_control_app' then 'platform_control_maintenance' "
                "when 'platform_control_app_preview' then "
                "'platform_control_maintenance_preview' end"
            ).fetchone()
            if roles and roles[1] is not None:
                maintenance_role = roles[1]
                privilege_row = connection.execute(
                    "select "
                    "has_table_privilege(current_user,"
                    "'platform_control.hr_execution_cutover','SELECT'),"
                    "has_table_privilege(current_user,"
                    "'platform_control.hr_execution_cutover','INSERT'),"
                    "has_table_privilege(current_user,"
                    "'platform_control.hr_execution_cutover','UPDATE'),"
                    "has_table_privilege(current_user,"
                    "'platform_control.hr_execution_cutover','DELETE'),"
                    "has_function_privilege(current_user,"
                    "'platform_control.hr_execution_cutover_counts_v102()',"
                    "'EXECUTE'),"
                    "has_function_privilege(current_user,"
                    "'platform_control.initialize_hr_execution_cutover_v102(uuid)',"
                    "'EXECUTE'),"
                    "has_function_privilege(current_user,"
                    "'platform_control.transition_hr_execution_cutover_v102(text,uuid)',"
                    "'EXECUTE'),"
                    "has_function_privilege(%s,"
                    "'platform_control.hr_execution_cutover_counts_v102()',"
                    "'EXECUTE'),"
                    "has_function_privilege(%s,"
                    "'platform_control.initialize_hr_execution_cutover_v102(uuid)',"
                    "'EXECUTE'),"
                    "has_function_privilege(%s,"
                    "'platform_control.transition_hr_execution_cutover_v102(text,uuid)',"
                    "'EXECUTE')",
                    (maintenance_role, maintenance_role, maintenance_role),
                ).fetchone()
                if privilege_row is not None:
                    permissions = {
                        "app_gate_select": privilege_row[0] is True,
                        "app_gate_write": any(
                            value is True for value in privilege_row[1:4]
                        ),
                        "app_admin_execute": any(
                            value is True for value in privilege_row[4:7]
                        ),
                        "maintenance_admin_execute": all(
                            value is True for value in privilege_row[7:10]
                        ),
                    }
                    permissions_complete = permissions == {
                        "app_gate_select": True,
                        "app_gate_write": False,
                        "app_admin_execute": False,
                        "maintenance_admin_execute": True,
                    }
        schema_ready = check_schema_ready(connection_factory)
        if not schema_ready:
            blockers.append("schema_not_ready")
        if not all(item["match"] for item in migrations):
            blockers.append("migration_identity_mismatch")
        if not all(item.get("image_match", True) for item in migrations):
            blockers.append("migration_image_identity_mismatch")
        if not cutover["initialized"]:
            blockers.append("cutover_gate_not_initialized")
        if not permissions_complete:
            blockers.append("cutover_permissions_incomplete")
        return {
            "checked": True,
            "schema_ready": schema_ready,
            "migrations": migrations,
            "cutover": cutover,
            "permissions": permissions,
            "permissions_complete": permissions_complete,
        }, blockers
    except Exception:  # noqa: BLE001 - database errors are never rendered raw
        return {
            "checked": False,
            "schema_ready": False,
            "migrations": migrations,
            "cutover": cutover,
            "permissions": permissions,
            "permissions_complete": False,
        }, ["database_read_unavailable"]


def build_report(
    api_environment: dict[str, str],
    worker_environment: dict[str, str],
    *,
    connection_factory=None,
    launch_scope="full-candidate",
) -> dict:
    if launch_scope not in {"public-only", "full-candidate"}:
        raise ValueError("unsupported launch scope")
    blockers: list[str] = []
    api, api_error = _runtime_report(dict(api_environment))
    worker, worker_error = _runtime_report(dict(worker_environment))
    for error in (api_error, worker_error):
        if error and error not in blockers:
            blockers.append(error)

    runtime_match = False
    if api is not None and worker is not None:
        comparable = (
            "configuration_fingerprint",
            "hr_content_keyring_fingerprint",
            "profile_fingerprints",
            "provider",
            "budget",
            "knowledge",
        )
        runtime_match = all(api[key] == worker[key] for key in comparable)
        if not runtime_match:
            blockers.append("runtime_identity_mismatch")
        api_attachment = api["attachments"]
        worker_attachment = worker["attachments"]
        if (
            api_attachment["enabled"] != worker_attachment["enabled"]
            or api_attachment["wiring_present"] != worker_attachment["wiring_present"]
            or (
                api_attachment["enabled"]
                and api_attachment["fingerprint"] != worker_attachment["fingerprint"]
            )
        ):
            blockers.append("attachment_wiring_asymmetric")
        elif api_attachment["enabled"] and not api_attachment["wiring_present"]:
            blockers.append("attachment_wiring_incomplete")

    if connection_factory is None:
        database = {
            "checked": False,
            "schema_ready": False,
            "migrations": [],
            "cutover": {"available": False, "initialized": False, "phase": None},
            "permissions": {
                "app_gate_select": False,
                "app_gate_write": False,
                "app_admin_execute": False,
                "maintenance_admin_execute": False,
            },
            "permissions_complete": False,
        }
        blockers.append("database_not_checked")
    else:
        database, database_blockers = _database_report(connection_factory)
        blockers.extend(database_blockers)

    # A protected, configuration-bound operator review grants public work.
    # A caller boolean or arbitrary provider metadata never grants authority.
    if launch_scope == "full-candidate":
        blockers.append("personal_processing_authorizer_absent")
    d7_approved = bool(api and worker and runtime_match and api["d7_product_approved"] and worker["d7_product_approved"])
    if not d7_approved:
        blockers.append("d7_product_approval_absent")
    blockers = list(dict.fromkeys(blockers))
    return {
        "schema_version": 1,
        "ok": not blockers,
        "production_certified": False,
        "launch_scope": launch_scope,
        "full_candidate_ready": False,
        "runtime_match": runtime_match,
        "api": api,
        "worker": worker,
        "database": database,
        "blockers": blockers,
        "limitations": {
            "process_check": "not_checked_by_tool",
            "process_existence_is_functionality_evidence": False,
            "process_functionality_verified": False,
            "model_or_network_called": False,
            "personal_processing_authorizer_present": False,
            "d7_product_approved": d7_approved,
            "browser_acceptance_completed": False,
            "release_window_known": False,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only HR runtime preflight")
    parser.add_argument(
        "--scope", choices=("public-only", "full-candidate"), required=True
    )
    parser.add_argument("--api-env-file", type=Path)
    parser.add_argument("--worker-env-file", type=Path)
    parser.add_argument("--database-url-file", type=Path)
    return parser


def main(argv=None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        api = (
            read_environment_file(arguments.api_env_file)
            if arguments.api_env_file
            else dict(os.environ)
        )
        worker = (
            read_environment_file(arguments.worker_env_file)
            if arguments.worker_env_file
            else dict(os.environ)
        )
        connection_factory = None
        if arguments.database_url_file:
            import psycopg
            from app.control_plane.dsn import validate_control_dsn
            from app.local_secrets import read_secret_file

            dsn = read_secret_file(arguments.database_url_file)
            validate_control_dsn(dsn, purpose="app")

            def connection_factory():
                return psycopg.connect(
                    dsn,
                    connect_timeout=3,
                    options="-c statement_timeout=10000 -c default_transaction_read_only=on",
                )

        report = build_report(
            api,
            worker,
            connection_factory=connection_factory,
            launch_scope=arguments.scope,
        )
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception:  # noqa: BLE001 - CLI must emit only a generic safe error
        report = {
            "schema_version": 1,
            "ok": False,
            "production_certified": False,
            "blockers": ["preflight_input_unavailable"],
        }
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
