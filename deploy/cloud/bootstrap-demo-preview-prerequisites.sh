#!/bin/bash
set -euo pipefail
umask 077

fail() {
  /usr/bin/printf '%s\n' 'DEMO_PREVIEW_PREREQUISITES_FAILED' >&2
  exit 1
}

[[ "${EUID:-$(/usr/bin/id -u)}" -eq 0 && $# -eq 1 ]] || fail
postgres_container="$1"
[[ "$postgres_container" =~ ^[0-9a-f]{12,64}$ ]] || fail

private_path=/opt/orbbec-agent-platform/private/demo-preview
state_path=/opt/orbbec-agent-platform/private/.demo-preview-prerequisite-state
preview_database=agent_platform_control_preview
operator_files=(
  dingtalk-app-key
  dingtalk-agent-id
  dingtalk-corp-id
  dingtalk-app-secret
  demo-userids
)
generated_files=(
  preview-control-database-url
  preview-control-audit-database-url
  preview-control-directory-worker-database-url
  preview-control-migrator-database-url
  preview-identity-hmac-keyring
  preview-identity-encryption-keyring
  preview-rate-limit-hmac-keyring
)
[[ "${#operator_files[@]}" -eq 5 && "${#generated_files[@]}" -eq 7 ]] || fail

exec 8> /run/lock/orbbec-demo-preview-prerequisites.lock
/usr/bin/flock -n 8 || fail

# `docker exec -u postgres platform-postgres psql -U platform_owner` is the
# credential boundary. The actual immutable container ID is required so a
# same-name replacement cannot redirect the bootstrap.
[[ "$(/usr/bin/docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' \
  "$postgres_container" 2>/dev/null)" == platform-postgres ]] || fail
[[ "$(/usr/bin/docker inspect --format '{{.State.Running}}' "$postgres_container")" == \
  true ]] || fail
/usr/bin/docker exec -u postgres "$postgres_container" \
  test -s /run/secrets/postgres-owner-password >/dev/null 2>&1 || fail

catalog_signature() {
  /usr/bin/docker exec -u postgres "$postgres_container" \
    psql -X -A -t -v ON_ERROR_STOP=1 -U platform_owner -d postgres -c \
    "select concat(
      (select count(*) from pg_database where datname = 'agent_platform_control_preview'), ':',
      (select count(*) from pg_roles where rolname = any(array[
        'platform_control_owner_preview','platform_control_migrator_preview',
        'platform_control_app_preview','platform_directory_worker_preview',
        'platform_audit_append_preview','platform_stream_ingest_preview',
        'platform_control_maintenance_preview']))
    )" 2>/dev/null
}

state_existed=0
[[ ! -e "$state_path" ]] || state_existed=1
catalog_before="$(catalog_signature)" || fail
[[ "$catalog_before" =~ ^[01]:[0-7]$ ]] || fail
if [[ "$state_existed" -eq 0 ]]; then
  /usr/bin/python3 - "$private_path" <<'PY' || fail
import os
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
operator = {
    "dingtalk-app-key", "dingtalk-agent-id", "dingtalk-corp-id",
    "dingtalk-app-secret", "demo-userids",
}
generated = {
    "preview-control-database-url", "preview-control-audit-database-url",
    "preview-control-directory-worker-database-url",
    "preview-control-migrator-database-url",
    "preview-identity-hmac-keyring", "preview-identity-encryption-keyring",
    "preview-rate-limit-hmac-keyring",
}
metadata = root.lstat()
if root.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
    raise SystemExit(1)
if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
    raise SystemExit(1)
actual = {path.name for path in root.iterdir()}
if actual not in (operator, operator | generated):
    raise SystemExit(1)
for name in actual:
    path = root / name
    item = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(item.st_mode):
        raise SystemExit(1)
    if item.st_uid != 0 or stat.S_IMODE(item.st_mode) != 0o600:
        raise SystemExit(1)
PY
  if [[ "$catalog_before" != 0:0 ]]; then
    # Existing catalog objects without a complete final credential set or a
    # resumable state are ambiguous; never rotate them by guessing.
    final_count=0
    for name in "${generated_files[@]}"; do
      [[ ! -e "$private_path/$name" ]] || final_count=$((final_count + 1))
    done
    [[ "$final_count" -eq 7 ]] || fail
  fi
fi

/usr/bin/python3 - "$private_path" "$state_path" <<'PY' || fail
import base64
import json
import os
import pathlib
import secrets
import stat
import sys

root = pathlib.Path(sys.argv[1])
state = pathlib.Path(sys.argv[2])
operator_files = {
    "dingtalk-app-key", "dingtalk-agent-id", "dingtalk-corp-id",
    "dingtalk-app-secret", "demo-userids",
}
keyring_purposes = {
    "preview-identity-encryption-keyring": "provider-encryption",
    "preview-identity-hmac-keyring": "provider-lookup-hmac",
    "preview-rate-limit-hmac-keyring": "rate-limit-hmac",
}
dsn_roles = {
    "preview-control-database-url": "platform_control_app_preview",
    "preview-control-audit-database-url": "platform_audit_append_preview",
    "preview-control-directory-worker-database-url":
        "platform_directory_worker_preview",
    "preview-control-migrator-database-url":
        "platform_control_migrator_preview",
}
generated_files = set(keyring_purposes) | set(dsn_roles)

def checked(path: pathlib.Path, mode: int) -> None:
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValueError
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != mode:
        raise ValueError

try:
    root_metadata = root.lstat()
    if root.is_symlink() or not stat.S_ISDIR(root_metadata.st_mode):
        raise ValueError
    if root_metadata.st_uid != 0 or stat.S_IMODE(root_metadata.st_mode) != 0o700:
        raise ValueError
    actual = {path.name for path in root.iterdir()}
    if not operator_files.issubset(actual) or not actual.issubset(
        operator_files | generated_files
    ):
        raise ValueError
    for name in operator_files:
        checked(root / name, 0o600)
    userids = (root / "demo-userids").read_text(encoding="utf-8").splitlines()
    if not 1 <= len(userids) <= 3 or len(userids) != len(set(userids)):
        raise ValueError
    if any(not value or value != value.strip() for value in userids):
        raise ValueError

    missing = generated_files - actual
    if missing:
        if state.exists():
            state_metadata = state.lstat()
            if state.is_symlink() or not stat.S_ISDIR(state_metadata.st_mode):
                raise ValueError
            if state_metadata.st_uid != 0 or stat.S_IMODE(state_metadata.st_mode) != 0o700:
                raise ValueError
        else:
            state.mkdir(mode=0o700)
        state_actual = {path.name for path in state.iterdir()}
        if not state_actual.issubset(generated_files):
            raise ValueError
        for name in state_actual:
            checked(state / name, 0o600)
        if not state_actual:
            for name, purpose in keyring_purposes.items():
                # Independent 32-byte values are generated once and retained
                # in the resumable root-only state until publication.
                key = base64.b64encode(os.urandom(32)).decode("ascii")
                document = {
                    "purpose": purpose,
                    "active_version": 1,
                    "keys": {"1": key},
                }
                if purpose == "provider-lookup-hmac":
                    document["transition_versions"] = [1]
                target = state / name
                with target.open("x", encoding="utf-8") as writer:
                    json.dump(document, writer, separators=(",", ":"))
                    writer.write("\n")
                target.chmod(0o600)
            for name, role in dsn_roles.items():
                password = secrets.token_hex(32)
                value = (
                    f"postgresql://{role}:{password}@platform-postgres:5432/"
                    "agent_platform_control_preview\n"
                )
                target = state / name
                with target.open("x", encoding="utf-8") as writer:
                    writer.write(value)
                target.chmod(0o600)
        elif state_actual != generated_files - (generated_files & actual):
            # A crash may have moved only a prefix into the final directory.
            if state_actual | (generated_files & actual) != generated_files:
                raise ValueError
    else:
        for name in generated_files:
            checked(root / name, 0o600)
except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1) from None
PY

provisioning=0
[[ ! -d "$state_path" || -L "$state_path" ]] || provisioning=1
if [[ "$provisioning" -eq 0 ]]; then
  [[ "$catalog_before" == 1:7 ]] || fail
fi

read_password() {
  local file="$1" role="$2" source="$private_path/$1" value
  if [[ -f "$state_path/$file" && ! -L "$state_path/$file" ]]; then
    source="$state_path/$file"
  fi
  value="$(/usr/bin/tr -d '\n' < "$source")" || fail
  [[ "$value" =~ ^postgresql://${role}:([0-9a-f]{64})@platform-postgres:5432/agent_platform_control_preview$ ]] || fail
  REPLY="${BASH_REMATCH[1]}"
}

read_password preview-control-migrator-database-url platform_control_migrator_preview
migrator_password="$REPLY"
read_password preview-control-database-url platform_control_app_preview
app_password="$REPLY"
read_password preview-control-directory-worker-database-url platform_directory_worker_preview
directory_password="$REPLY"
read_password preview-control-audit-database-url platform_audit_append_preview
audit_password="$REPLY"

/usr/bin/docker exec -u postgres -i "$postgres_container" \
  psql -X -v ON_ERROR_STOP=1 -U platform_owner -d postgres >/dev/null 2>&1 <<SQL
select 'create role platform_control_owner_preview nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit'
where not exists (select 1 from pg_roles where rolname = 'platform_control_owner_preview') \gexec
select 'create role platform_stream_ingest_preview nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit'
where not exists (select 1 from pg_roles where rolname = 'platform_stream_ingest_preview') \gexec
select 'create role platform_control_maintenance_preview nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit'
where not exists (select 1 from pg_roles where rolname = 'platform_control_maintenance_preview') \gexec
alter role platform_control_owner_preview nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit;
alter role platform_stream_ingest_preview nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit;
alter role platform_control_maintenance_preview nologin nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit;
SQL

if [[ "$provisioning" -eq 1 ]]; then
  /usr/bin/docker exec -u postgres -i "$postgres_container" \
    psql -X -v ON_ERROR_STOP=1 -U platform_owner -d postgres >/dev/null 2>&1 <<SQL
select format('create role %I login password %L nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit', role_name, role_password)
from (values
  ('platform_control_migrator_preview', '$migrator_password'),
  ('platform_control_app_preview', '$app_password'),
  ('platform_directory_worker_preview', '$directory_password'),
  ('platform_audit_append_preview', '$audit_password')
) configured(role_name, role_password)
where not exists (select 1 from pg_roles where rolname = role_name) \gexec
select format('alter role %I login password %L nosuperuser nocreatedb nocreaterole noreplication nobypassrls noinherit', role_name, role_password)
from (values
  ('platform_control_migrator_preview', '$migrator_password'),
  ('platform_control_app_preview', '$app_password'),
  ('platform_directory_worker_preview', '$directory_password'),
  ('platform_audit_append_preview', '$audit_password')
) configured(role_name, role_password) \gexec
SQL
fi

/usr/bin/docker exec -u postgres "$postgres_container" \
  psql -X -v ON_ERROR_STOP=1 -U platform_owner -d postgres -c \
  'grant platform_control_owner_preview to platform_control_migrator_preview' \
  >/dev/null 2>&1 || fail

database_exists="$(/usr/bin/docker exec -u postgres "$postgres_container" \
  psql -X -A -t -v ON_ERROR_STOP=1 -U platform_owner -d postgres -c \
  "select count(*) from pg_database where datname = 'agent_platform_control_preview'" \
  2>/dev/null)" || fail
[[ "$database_exists" == 0 || "$database_exists" == 1 ]] || fail
if [[ "$database_exists" == 0 ]]; then
  /usr/bin/docker exec -u postgres "$postgres_container" \
    createdb -U platform_owner -O platform_control_owner_preview -T template0 \
    agent_platform_control_preview >/dev/null 2>&1 || fail
fi

/usr/bin/docker exec -u postgres -i "$postgres_container" \
  psql -X -v ON_ERROR_STOP=1 -U platform_owner -d postgres >/dev/null 2>&1 <<'SQL'
alter database agent_platform_control_preview owner to platform_control_owner_preview;
revoke connect on database agent_platform_control_preview from public,
  platform_control_owner_preview, platform_control_migrator_preview,
  platform_control_app_preview, platform_directory_worker_preview,
  platform_audit_append_preview, platform_stream_ingest_preview,
  platform_control_maintenance_preview;
grant connect on database agent_platform_control_preview to
  platform_control_migrator_preview, platform_control_app_preview,
  platform_directory_worker_preview, platform_audit_append_preview;
select format(
  'revoke connect on database %I from platform_control_owner_preview, platform_control_migrator_preview, platform_control_app_preview, platform_directory_worker_preview, platform_audit_append_preview, platform_stream_ingest_preview, platform_control_maintenance_preview',
  datname
)
from pg_database
where datname <> 'agent_platform_control_preview' \gexec
SQL

role_signature="$(/usr/bin/docker exec -u postgres "$postgres_container" \
  psql -X -A -t -v ON_ERROR_STOP=1 -U platform_owner -d postgres -c \
  "select concat(
    (select count(*) from pg_roles where rolname = 'platform_control_owner_preview' and not rolcanlogin and not rolinherit and not rolsuper and not rolcreatedb and not rolcreaterole and not rolreplication and not rolbypassrls), ':',
    (select count(*) from pg_roles where rolname = any(array['platform_control_migrator_preview','platform_control_app_preview','platform_directory_worker_preview','platform_audit_append_preview']) and rolcanlogin and not rolinherit and not rolsuper and not rolcreatedb and not rolcreaterole and not rolreplication and not rolbypassrls), ':',
    (select count(*) from pg_roles where rolname = any(array['platform_stream_ingest_preview','platform_control_maintenance_preview']) and not rolcanlogin and not rolinherit), ':',
    (select count(*) from pg_database database_entry join pg_roles owner_role on owner_role.oid = database_entry.datdba where database_entry.datname = 'agent_platform_control_preview' and owner_role.rolname = 'platform_control_owner_preview'), ':',
    (select count(*) from pg_auth_members membership join pg_roles granted on granted.oid = membership.roleid join pg_roles member on member.oid = membership.member where granted.rolname = 'platform_control_owner_preview' and member.rolname = 'platform_control_migrator_preview'), ':',
    (select count(*) from pg_auth_members membership join pg_roles member on member.oid = membership.member where member.rolname = any(array['platform_control_migrator_preview','platform_control_app_preview','platform_directory_worker_preview','platform_audit_append_preview','platform_stream_ingest_preview','platform_control_maintenance_preview']) and not exists (select 1 from pg_roles granted where granted.oid = membership.roleid and granted.rolname = 'platform_control_owner_preview' and member.rolname = 'platform_control_migrator_preview')))" \
  2>/dev/null)" || fail
[[ "$role_signature" == 1:4:2:1:1:0 ]] || fail

# PostgreSQL grants CONNECT to PUBLIC on ordinary databases by default and has
# no per-role DENY ACL. This demo does not revoke production PUBLIC access.
# It instead proves that none of the preview roles has any direct grant on
# schemas, relations, routines or types in another database; every runtime DSN
# remains independently pinned to the preview database.
while IFS= read -r database_name; do
  [[ -n "$database_name" ]] || continue
  direct_grants="$(/usr/bin/docker exec -u postgres "$postgres_container" \
    psql -X -A -t -v ON_ERROR_STOP=1 -U platform_owner -d "$database_name" -c \
    "with target_roles as (
       select oid from pg_roles where rolname = any(array[
         'platform_control_owner_preview','platform_control_migrator_preview',
         'platform_control_app_preview','platform_directory_worker_preview',
         'platform_audit_append_preview','platform_stream_ingest_preview',
         'platform_control_maintenance_preview'])
     )
     select
       (select count(*) from pg_namespace object cross join lateral aclexplode(object.nspacl) acl where acl.grantee in (select oid from target_roles)) +
       (select count(*) from pg_class object cross join lateral aclexplode(object.relacl) acl where acl.grantee in (select oid from target_roles)) +
       (select count(*) from pg_proc object cross join lateral aclexplode(object.proacl) acl where acl.grantee in (select oid from target_roles)) +
       (select count(*) from pg_type object cross join lateral aclexplode(object.typacl) acl where acl.grantee in (select oid from target_roles))" \
    2>/dev/null)" || fail
  [[ "$direct_grants" == 0 ]] || fail
done < <(/usr/bin/docker exec -u postgres "$postgres_container" \
  psql -X -A -t -v ON_ERROR_STOP=1 -U platform_owner -d postgres -c \
  "select datname from pg_database where datallowconn and datname <> 'agent_platform_control_preview' order by datname" \
  2>/dev/null)

/usr/bin/python3 - "$private_path" "$state_path" <<'PY' || fail
import os
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
state = pathlib.Path(sys.argv[2])
generated = {
    "preview-control-database-url", "preview-control-audit-database-url",
    "preview-control-directory-worker-database-url",
    "preview-control-migrator-database-url",
    "preview-identity-hmac-keyring", "preview-identity-encryption-keyring",
    "preview-rate-limit-hmac-keyring",
}
try:
    if state.exists():
        metadata = state.lstat()
        if state.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError
        if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ValueError
        for name in sorted(generated):
            source = state / name
            target = root / name
            if source.exists():
                os.replace(source, target)
                target.chmod(0o600)
        state.rmdir()
    actual = {path.name for path in root.iterdir()}
    if len(actual) != 12 or not generated.issubset(actual):
        raise ValueError
    for path in root.iterdir():
        item = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(item.st_mode):
            raise ValueError
        if item.st_uid != 0 or stat.S_IMODE(item.st_mode) != 0o600:
            raise ValueError
except (OSError, ValueError):
    raise SystemExit(1) from None
PY

unset migrator_password app_password directory_password audit_password REPLY
/usr/bin/printf '%s\n' 'DEMO_PREVIEW_PREREQUISITES_READY files=12'
