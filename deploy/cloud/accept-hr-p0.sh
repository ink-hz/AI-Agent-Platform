#!/bin/bash
set -eEuo pipefail
umask 077

fail() {
  /usr/bin/printf '%s\n' HR_P0_ACCEPTANCE_FAILED >&2
  exit 1
}

required_user=agentops
required_config=/Users/agentops/AgentRuntime/private/acceptance-config.json
required_known_hosts=/Users/agentops/AgentRuntime/private/cloud-known-hosts
cloud_admin_host=root@47.106.112.69
cloud_admin_key=/Users/agentops/AgentRuntime/private/cloud-admin-ed25519
ssh_bin=/usr/bin/ssh
python_bin=/usr/bin/python3

[[ $# -eq 1 && "$1" == "$required_config" ]] || fail
[[ "$(/usr/bin/id -un)" == "$required_user" ]] || fail
[[ -f "$required_config" && ! -L "$required_config" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp %Su' "$required_config")" == "600 $required_user" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp %Su' "$(/usr/bin/dirname "$required_config")")" == "700 $required_user" ]] || fail
config_size="$(/usr/bin/stat -f '%z' "$required_config")"
[[ "$config_size" =~ ^[0-9]+$ && "$config_size" -gt 0 && "$config_size" -le 65536 ]] || fail

"$python_bin" - "$required_config" "$cloud_admin_host" "$cloud_admin_key" <<'PY' || fail
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
expected_host = sys.argv[2]
expected_key = sys.argv[3]
value = json.loads(path.read_bytes())
expected_keys = {"schema_version", "cloud_admin_host", "cloud_admin_key"}
if not isinstance(value, dict) or not set(value) == expected_keys:
    raise SystemExit(1)
if value != {
    "schema_version": 1,
    "cloud_admin_host": expected_host,
    "cloud_admin_key": expected_key,
}:
    raise SystemExit(1)
PY

for private_file in "$cloud_admin_key" "$required_known_hosts"; do
  [[ -f "$private_file" && ! -L "$private_file" ]] || fail
  [[ "$(/usr/bin/stat -f '%Lp %Su' "$private_file")" == "600 $required_user" ]] || fail
  private_size="$(/usr/bin/stat -f '%z' "$private_file")"
  [[ "$private_size" =~ ^[0-9]+$ && "$private_size" -gt 0 && "$private_size" -le 65536 ]] || fail
done

ssh_options=(
  -i "$cloud_admin_key"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o ConnectTimeout=8
  -o ServerAliveInterval=10
  -o ServerAliveCountMax=3
  -o StrictHostKeyChecking=yes
  -o UserKnownHostsFile="$required_known_hosts"
)

result="$($ssh_bin "${ssh_options[@]}" "$cloud_admin_host" /usr/bin/timeout -k 10 1200 /bin/bash -s <<'REMOTE'
set -eEuo pipefail
umask 077

remote_config=/opt/orbbec-agent-platform/private/hr-p0-acceptance.json
remote_egress_evidence=/opt/orbbec-agent-platform/private/hr-provider-egress.evidence.json
remote_fixture_root=/opt/orbbec-agent-platform/current/backend/tests/fixtures/hr_p0
container_name=orbbec-agent-platform-platform-api-1
container_root=/tmp/hr-p0-acceptance
container_config=$container_root/config.json
container_fixture_root=$container_root/fixtures
container_cleanup_manifest=$container_root/cleanup.json
host_cleanup_root=/run/hr-p0-acceptance
host_cleanup_manifest=$host_cleanup_root/cleanup.json
postgres_container=orbbec-agent-platform-platform-postgres-1
python_bin=/usr/bin/python3
target_agent=hr-bot

remote_fail() {
  /usr/bin/printf '%s\n' HR_P0_ACCEPTANCE_FAILED >&2
  exit 1
}

cleanup() {
  /bin/rm -f -- "$host_cleanup_manifest" >/dev/null 2>&1 || true
  /bin/rmdir -- "$host_cleanup_root" >/dev/null 2>&1 || true
  /usr/bin/docker exec "$container_name" /bin/rm -f -- "$container_cleanup_manifest" >/dev/null 2>&1 || true
  /usr/bin/docker exec "$container_name" /bin/rm -f -- "$container_config" >/dev/null 2>&1 || true
  /usr/bin/docker exec "$container_name" /bin/rm -f -- "$container_fixture_root/resume-strong.md" >/dev/null 2>&1 || true
  /usr/bin/docker exec "$container_name" /bin/rm -f -- "$container_fixture_root/resume-adjacent.md" >/dev/null 2>&1 || true
  /usr/bin/docker exec "$container_name" /bin/rm -f -- "$container_fixture_root/resume-invalid.txt" >/dev/null 2>&1 || true
  /usr/bin/docker exec "$container_name" /bin/rm -f -- "$container_fixture_root/panorama-result.json" >/dev/null 2>&1 || true
  /usr/bin/docker exec "$container_name" /bin/rm -f -- "$container_fixture_root/recruiting-results.json" >/dev/null 2>&1 || true
  /usr/bin/docker exec "$container_name" /bin/rmdir -- "$container_fixture_root" >/dev/null 2>&1 || true
  /usr/bin/docker exec "$container_name" /bin/rmdir -- "$container_root" >/dev/null 2>&1 || true
}
trap cleanup EXIT
[[ ! -e "$host_cleanup_root" && ! -L "$host_cleanup_root" ]] || remote_fail
/bin/mkdir -m 700 -- "$host_cleanup_root" || remote_fail

for remote_file in "$remote_config" "$remote_egress_evidence"; do
  [[ -f "$remote_file" && ! -L "$remote_file" ]] || remote_fail
  [[ "$(/usr/bin/stat -c '%a %U' "$remote_file")" == "600 root" ]] || remote_fail
  remote_size="$(/usr/bin/stat -c '%s' "$remote_file")"
  [[ "$remote_size" =~ ^[0-9]+$ && "$remote_size" -gt 0 && "$remote_size" -le 65536 ]] || remote_fail
done
[[ "$(/usr/bin/stat -c '%a %U' "$(/usr/bin/dirname "$remote_config")")" == "700 root" ]] || remote_fail

evidence_digest="$(/usr/bin/sha256sum "$remote_egress_evidence")"
evidence_digest="${evidence_digest%% *}"
"$python_bin" - "$remote_config" "$remote_egress_evidence" "$evidence_digest" "$target_agent" <<'PY' || remote_fail
import json
import pathlib
import sys
from urllib.parse import urlsplit

config = json.loads(pathlib.Path(sys.argv[1]).read_bytes())
evidence = json.loads(pathlib.Path(sys.argv[2]).read_bytes())
digest, target_agent = sys.argv[3:]
config_keys = {
    "schema_version", "agent_id", "api_base_url", "public_origin", "owner_id",
    "session_cookie", "csrf_token", "companies", "connect_timeout_seconds",
    "request_timeout_seconds", "run_timeout_seconds", "poll_interval_seconds",
    "deployment_egress_evidence_sha256",
}
evidence_keys = {
    "schema_version", "policy", "direct_target_egress", "allowed_authorities",
}
if not isinstance(config, dict) or set(config) != config_keys:
    raise SystemExit(1)
if not isinstance(evidence, dict) or set(evidence) != evidence_keys:
    raise SystemExit(1)
if config.get("schema_version") != 1 or config.get("agent_id") != target_agent:
    raise SystemExit(1)
if config.get("deployment_egress_evidence_sha256") != digest:
    raise SystemExit(1)
if evidence != {
    "schema_version": 1,
    "policy": "provider-only",
    "direct_target_egress": False,
    "allowed_authorities": evidence.get("allowed_authorities"),
}:
    raise SystemExit(1)
authorities = evidence["allowed_authorities"]
if not isinstance(authorities, list) or not authorities:
    raise SystemExit(1)
expected_authorities = {
    (urlsplit(url).hostname.lower(), urlsplit(url).port or 443)
    for company in config.get("companies", [])
    for url in company.get("approved_urls", [])
}
if any(not isinstance(value, str) for value in authorities):
    raise SystemExit(1)
provider_authorities = []
for authority in authorities:
    parsed = urlsplit("//" + authority)
    try:
        port = parsed.port or 443
    except ValueError:
        raise SystemExit(1)
    if (
        parsed.username is not None or parsed.password is not None
        or not parsed.hostname or parsed.hostname.lower() != parsed.hostname
        or parsed.hostname.endswith(".")
        or parsed.path or parsed.query or parsed.fragment
        or parsed.netloc != authority
    ):
        raise SystemExit(1)
    provider_authorities.append((parsed.hostname, port))
if len(provider_authorities) != len(set(provider_authorities)) or set(provider_authorities) & expected_authorities:
    raise SystemExit(1)
PY

expected_fixtures='panorama-result.json recruiting-results.json resume-adjacent.md resume-invalid.txt resume-strong.md'
actual_fixtures="$(/usr/bin/find "$remote_fixture_root" -mindepth 1 -maxdepth 1 -type f -printf '%f\n' | /usr/bin/sort | /usr/bin/tr '\n' ' ')"
[[ "$actual_fixtures" == "$expected_fixtures " ]] || remote_fail
/usr/bin/docker exec "$container_name" /bin/mkdir -m 700 -- "$container_root" || remote_fail
/usr/bin/docker exec "$container_name" /bin/mkdir -m 700 -- "$container_fixture_root" || remote_fail
/usr/bin/docker cp "$remote_config" "$container_name:$container_config" >/dev/null || remote_fail
for fixture in resume-strong.md resume-adjacent.md resume-invalid.txt panorama-result.json recruiting-results.json; do
  /usr/bin/docker cp "$remote_fixture_root/$fixture" "$container_name:$container_fixture_root/$fixture" >/dev/null || remote_fail
done
/usr/bin/docker exec -u 0 "$container_name" /bin/chown 10001:10001 -- "$container_config" || remote_fail
for fixture in resume-strong.md resume-adjacent.md resume-invalid.txt panorama-result.json recruiting-results.json; do
  /usr/bin/docker exec -u 0 "$container_name" /bin/chown 10001:10001 -- "$container_fixture_root/$fixture" || remote_fail
done

cli_status=0
status="$(/usr/bin/docker exec "$container_name" \
  /usr/local/bin/python -m app.hr.p0_acceptance_cli)" || cli_status=$?
/usr/bin/docker cp "$container_name:$container_cleanup_manifest" "$host_cleanup_manifest" >/dev/null || remote_fail
[[ -f "$host_cleanup_manifest" && ! -L "$host_cleanup_manifest" ]] || remote_fail
/bin/chmod 600 -- "$host_cleanup_manifest" || remote_fail
[[ "$(/usr/bin/stat -c '%a %U' "$host_cleanup_manifest")" == "600 root" ]] || remote_fail
manifest_size="$(/usr/bin/stat -c '%s' "$host_cleanup_manifest")"
[[ "$manifest_size" =~ ^[0-9]+$ && "$manifest_size" -gt 0 && "$manifest_size" -le 65536 ]] || remote_fail
"$python_bin" - "$remote_config" "$host_cleanup_manifest" "$postgres_container" "$cli_status" <<'PY' || remote_fail
import json
import pathlib
import re
import subprocess
import sys

config = json.loads(pathlib.Path(sys.argv[1]).read_bytes())
manifest = json.loads(pathlib.Path(sys.argv[2]).read_bytes())
container = sys.argv[3]
cli_succeeded = sys.argv[4] == "0"
created_keys = {
    "conversation_ids", "position_ids", "candidate_ids",
    "position_candidate_ids", "candidate_document_ids",
}
if (
    not isinstance(manifest, dict)
    or set(manifest) != {"schema_version", "owner_id", "created_ids"}
    or manifest.get("schema_version") != 1
    or manifest.get("owner_id") != config.get("owner_id")
    or not isinstance(manifest.get("created_ids"), dict)
    or set(manifest["created_ids"]) != created_keys
):
    raise SystemExit(1)
uuid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
for key, expected_count in {
    "conversation_ids": 5,
    "position_ids": 1,
    "candidate_ids": 2,
    "position_candidate_ids": 2,
    "candidate_document_ids": 2,
}.items():
    values = manifest["created_ids"][key]
    if (
        not isinstance(values, list)
        or len(values) > expected_count
        or (cli_succeeded and len(values) != expected_count)
        or len(values) != len(set(values))
        or any(not isinstance(value, str) or not uuid.fullmatch(value) for value in values)
    ):
        raise SystemExit(1)

def array(key):
    values = manifest["created_ids"][key]
    if not values:
        return "array[]::uuid[]"
    return "array[" + ",".join(
        "'" + value + "'::uuid" for value in values
    ) + "]"

owner = manifest["owner_id"]
positions = array("position_ids")
relations = array("position_candidate_ids")
candidates = array("candidate_ids")
documents = array("candidate_document_ids")
sql = f"""
begin;
do $cleanup$
declare
  owner_value uuid := '{owner}'::uuid;
  position_values uuid[] := {positions};
  relation_values uuid[] := {relations};
  candidate_values uuid[] := {candidates};
  document_values uuid[] := {documents};
  changed integer;
begin
  if (select count(*) from platform_hr.positions where owner_internal_user_id=owner_value and position_id=any(position_values)) <> cardinality(position_values) then raise exception 'scope'; end if;
  if (select count(*) from platform_hr.position_candidates where owner_internal_user_id=owner_value and position_candidate_id=any(relation_values) and candidate_id=any(candidate_values)) <> cardinality(relation_values) then raise exception 'scope'; end if;
  if (select count(*) from platform_hr.candidate_documents where owner_internal_user_id=owner_value and document_id=any(document_values) and candidate_id=any(candidate_values)) <> cardinality(document_values) then raise exception 'scope'; end if;
  if (select count(distinct candidate_id) from platform_hr.candidate_documents where owner_internal_user_id=owner_value and document_id=any(document_values)) <> cardinality(document_values) then raise exception 'scope'; end if;
  if (select count(*) from platform_hr.candidates where owner_internal_user_id=owner_value and candidate_id=any(candidate_values)) <> cardinality(candidate_values) then raise exception 'scope'; end if;
  update platform_hr.positions set internal_status='archived',row_version=row_version+1,updated_at=now() where owner_internal_user_id=owner_value and position_id=any(position_values) and internal_status<>'archived';
  update platform_hr.position_candidates set status='archived',row_version=row_version+1,updated_at=now() where owner_internal_user_id=owner_value and position_candidate_id=any(relation_values) and status<>'archived';
  update platform_hr.candidate_documents set status='erased' where owner_internal_user_id=owner_value and document_id=any(document_values) and status<>'erased';
  if exists(select 1 from platform_hr.positions where owner_internal_user_id=owner_value and position_id=any(position_values) and internal_status<>'archived') then raise exception 'cleanup'; end if;
  if exists(select 1 from platform_hr.position_candidates where owner_internal_user_id=owner_value and position_candidate_id=any(relation_values) and status<>'archived') then raise exception 'cleanup'; end if;
  if exists(select 1 from platform_hr.candidate_documents where owner_internal_user_id=owner_value and document_id=any(document_values) and status<>'erased') then raise exception 'cleanup'; end if;
end
$cleanup$;
commit;
"""
subprocess.run(
    [
        "/usr/bin/docker", "exec", "-i", container,
        "/usr/bin/psql", "-X", "-v", "ON_ERROR_STOP=1",
        "-U", "platform_owner", "-d", "agent_platform_control",
    ],
    input=sql.encode(),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    timeout=60,
    check=True,
)
PY
[[ "$cli_status" == 0 && "$status" =~ ^HR_P0_ACCEPTANCE_OK\ [0-9a-f-]{36}$ ]] || remote_fail
/usr/bin/printf '%s\n' "$status"
REMOTE
)" || fail
[[ "$result" =~ ^HR_P0_ACCEPTANCE_OK\ [0-9a-f-]{36}$ ]] || fail
/usr/bin/printf '%s\n' "$result"
