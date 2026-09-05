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
script_dir="$(cd "$(dirname "$0")" && pwd)"
release_verifier="$script_dir/verify-web-research-release.py"
web_research_current=/Users/agentops/AgentRuntime/web-research/current
expected_egress_source_sha256=5604d7ac150a5bcd9e722edd777c5946f9e82fdb1bc4df5e6a3aceed0b8d5fe6
expected_egress_release_sha256=c0a7aaf71f5ae8555371b0a93eae8499dd4e68e7224f0cb51cce4351df8f39fd
web_research_service=system/com.orbbec.web-research
web_research_plist=/Library/LaunchDaemons/com.orbbec.web-research.plist
provider_gateway=10.10.20.133
provider_port=8088
target_denial_probe=1.1.1.1
target_denial_port=443
sandbox_profile='(version 1) (allow default) (deny network-outbound) (allow network-outbound (literal "/var/run/mDNSResponder")) (allow network-outbound (remote ip "*:8088"))'

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

[[ -L "$web_research_current" ]] || fail
web_research_release="$($python_bin - "$web_research_current" <<'PY'
import pathlib
import sys
print(pathlib.Path(sys.argv[1]).resolve(strict=True))
PY
)" || fail
[[ "$web_research_release" =~ ^/Users/agentops/AgentRuntime/web-research/releases/[0-9a-f]{64}$ ]] || fail
web_research_manifest="$web_research_release/.manifest.sha256"
web_research_source="$web_research_release/codex-process.mjs"
for deployed_file in "$release_verifier" "$web_research_manifest" "$web_research_source"; do
  [[ -f "$deployed_file" && ! -L "$deployed_file" ]] || fail
done
[[ "$(/usr/bin/stat -f '%Lp %Su' "$web_research_manifest")" == "600 $required_user" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp %Su' "$web_research_source")" == "600 $required_user" ]] || fail
"$python_bin" "$release_verifier" "$web_research_release" \
  /Users/agentops/AgentRuntime/web-research \
  "$expected_egress_release_sha256" "$expected_egress_source_sha256" >/dev/null || fail
[[ -f "$web_research_plist" && ! -L "$web_research_plist" ]] || fail
[[ "$(/usr/bin/stat -f '%Lp %Su' "$web_research_plist")" == "644 root" ]] || fail
[[ "$(/usr/libexec/PlistBuddy -c 'Print :UserName' "$web_research_plist")" == "$required_user" ]] || fail
[[ "$(/usr/libexec/PlistBuddy -c 'Print :GroupName' "$web_research_plist")" == staff ]] || fail
[[ "$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:0' "$web_research_plist")" == /opt/homebrew/bin/node ]] || fail
[[ "$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:1' "$web_research_plist")" == "$web_research_current/sidecar.mjs" ]] || fail
[[ "$(/usr/libexec/PlistBuddy -c 'Print :WorkingDirectory' "$web_research_plist")" == /Users/agentops/AgentRuntime/web-research ]] || fail
/bin/launchctl print "$web_research_service" >/dev/null || fail
health_response="$(/usr/bin/env -i HOME=/Users/agentops USER=agentops LOGNAME=agentops PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin METABOT_ONLY_BOTS=hr-bot /Users/agentops/AgentRuntime/web-research/bin/web-search health)" || fail
"$python_bin" - "$expected_egress_release_sha256" "$health_response" <<'PY' || fail
import json
import sys

expected, raw = sys.argv[1:]
value = json.loads(raw)
if (
    not isinstance(value, dict) or value.get("ok") is not True
    or value.get("operation") != "health" or value.get("provider") != "codex"
    or value.get("release_sha") != expected
    or value.get("auth_configured") is not True
    or value.get("socket_mode") != "0600"
):
    raise SystemExit(1)
PY
/usr/bin/sandbox-exec -p "$sandbox_profile" /usr/bin/nc -G 3 -z "$provider_gateway" "$provider_port" >/dev/null 2>&1 || fail
if /usr/bin/sandbox-exec -p "$sandbox_profile" /usr/bin/nc -G 1 -z "$target_denial_probe" "$target_denial_port" >/dev/null 2>&1; then fail; fi
egress_gate=SANDBOX_PROVIDER_EGRESS_OK
[[ "$egress_gate" == SANDBOX_PROVIDER_EGRESS_OK ]] || fail
egress_digest="$expected_egress_release_sha256"

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

result="$($ssh_bin "${ssh_options[@]}" "$cloud_admin_host" /usr/bin/timeout -k 10 1200 /bin/bash -s -- "$egress_digest" <<'REMOTE'
set -eEuo pipefail
umask 077

remote_config=/opt/orbbec-agent-platform/private/hr-p0-acceptance.json
deployment_egress_evidence_sha256=$1
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

for remote_file in "$remote_config"; do
  [[ -f "$remote_file" && ! -L "$remote_file" ]] || remote_fail
  [[ "$(/usr/bin/stat -c '%a %U' "$remote_file")" == "600 root" ]] || remote_fail
  remote_size="$(/usr/bin/stat -c '%s' "$remote_file")"
  [[ "$remote_size" =~ ^[0-9]+$ && "$remote_size" -gt 0 && "$remote_size" -le 65536 ]] || remote_fail
done
[[ "$(/usr/bin/stat -c '%a %U' "$(/usr/bin/dirname "$remote_config")")" == "700 root" ]] || remote_fail

"$python_bin" - "$remote_config" "$deployment_egress_evidence_sha256" "$target_agent" <<'PY' || remote_fail
import json
import pathlib
import sys
from urllib.parse import urlsplit

config = json.loads(pathlib.Path(sys.argv[1]).read_bytes())
digest, target_agent = sys.argv[2:]
config_keys = {
    "schema_version", "agent_id", "api_base_url", "public_origin", "owner_id",
    "session_cookie", "csrf_token", "companies", "connect_timeout_seconds",
    "request_timeout_seconds", "run_timeout_seconds", "poll_interval_seconds",
    "deployment_egress_evidence_sha256",
}
if not isinstance(config, dict) or set(config) != config_keys:
    raise SystemExit(1)
if config.get("schema_version") != 1 or config.get("agent_id") != target_agent:
    raise SystemExit(1)
if config.get("deployment_egress_evidence_sha256") != digest:
    raise SystemExit(1)
companies = config.get("companies")
if not isinstance(companies, list) or not companies:
    raise SystemExit(1)
for company in companies:
    if not isinstance(company, dict) or not isinstance(company.get("approved_urls"), list) or not company["approved_urls"]:
        raise SystemExit(1)
    for url in company["approved_urls"]:
        parsed = urlsplit(url)
        if (
            not isinstance(url, str) or parsed.scheme != "https"
            or not parsed.hostname or parsed.hostname.lower() != parsed.hostname
            or parsed.hostname.endswith(".") or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment
        ):
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
