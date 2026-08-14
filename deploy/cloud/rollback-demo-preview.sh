#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "AGENT_DEMO_PREVIEW_ROLLBACK_FAILED" >&2
  exit 1
}

[[ "$EUID" -eq 0 && $# -eq 0 ]] || fail

agent_enabled=/etc/nginx/sites-enabled/agent-domain.conf
snippet_target=/etc/nginx/snippets/orbbec-agent-demo-preview.conf
state_dir=/var/lib/orbbec-agent-demo-preview
active_state="$state_dir/active-backup"
platform_root=/opt/orbbec-agent-platform
platform_environment="$platform_root/private/platform.env"
base_compose="$platform_root/current/deploy/cloud/compose.yaml"
preview_base_compose="$platform_root/current/deploy/cloud/compose.demo-preview-base.yaml"
preview_compose="$platform_root/current/deploy/cloud/compose.demo-preview.yaml"

protected_container_invariants() {
  local name
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    case "$name" in
      *platform-api-demo-preview*|*platform-loopback-demo-preview*) continue ;;
    esac
    /usr/bin/docker inspect --format \
      '{{.Name}}|{{.Id}}|{{.Config.Image}}|{{.State.StartedAt}}|{{.RestartCount}}' \
      "$name"
  done < <(/usr/bin/docker ps --format '{{.Names}}' | /usr/bin/sort)
}

public_listener_invariants() {
  /usr/bin/ss -H -lnt | /usr/bin/awk \
    '$4 !~ /^(127\.0\.0\.1|\[::1\]):/ {print $4}' | /usr/bin/sort -u
}

response_invariants() {
  local label="$1" url="$2" resolve_value="${3:-}"
  local command=(/usr/bin/curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 8)
  if [[ -n "$resolve_value" ]]; then
    command+=(--resolve "$resolve_value")
  fi
  local code
  code="$("${command[@]}" "$url")" || fail
  [[ "$code" =~ ^[0-9]{3}$ ]] || fail
  /usr/bin/printf '%s=%s\n' "$label" "$code"
}

stop_demo_services() {
  if [[ -f "$platform_environment" && ! -L "$platform_environment" && \
        -f "$base_compose" && ! -L "$base_compose" && \
        -f "$preview_base_compose" && ! -L "$preview_base_compose" && \
        -f "$preview_compose" && ! -L "$preview_compose" ]]; then
    local production_compose=(/usr/bin/docker compose --env-file "$platform_environment" \
      -f "$base_compose")
    local preview_stack=(/usr/bin/docker compose --env-file "$platform_environment" \
      -f "$preview_base_compose" -f "$preview_compose")
    local postgres_container postgres_address edge_signature edge_name edge_driver
    local edge_scope edge_internal edge_config_count edge_subnet edge_gateway_address
    postgres_container="$("${production_compose[@]}" ps -q platform-postgres)" || fail
    [[ "$postgres_container" =~ ^[0-9a-f]{12,64}$ ]] || fail
    postgres_address="$(/usr/bin/docker inspect --format \
      '{{with index .NetworkSettings.Networks "orbbec-agent-platform-internal"}}{{.IPAddress}}{{end}}' \
      "$postgres_container")" || fail
    [[ "$postgres_address" =~ ^172\.30\.0\.[0-9]+$ ]] || fail
    edge_signature="$(/usr/bin/docker network inspect --format \
      '{{.Name}}|{{.Driver}}|{{.Scope}}|{{.Internal}}|{{len .IPAM.Config}}|{{(index .IPAM.Config 0).Subnet}}|{{(index .IPAM.Config 0).Gateway}}' \
      orbbec-agent-platform-edge)" || fail
    IFS='|' read -r edge_name edge_driver edge_scope edge_internal \
      edge_config_count edge_subnet edge_gateway_address <<< "$edge_signature"
    [[ "$edge_name" == orbbec-agent-platform-edge && "$edge_driver" == bridge && \
       "$edge_scope" == local && "$edge_internal" == false && \
       "$edge_config_count" == 1 ]] || fail
    /usr/bin/python3 - "$edge_subnet" "$edge_gateway_address" <<'PY' || fail
import ipaddress
import sys

network = ipaddress.ip_network(sys.argv[1], strict=True)
gateway = ipaddress.ip_address(sys.argv[2])
if (
    network.version != 4
    or not network.is_private
    or network.overlaps(ipaddress.ip_network("172.30.0.0/28"))
    or gateway not in network
    or gateway in {network.network_address, network.broadcast_address}
):
    raise SystemExit(1)
PY
    PLATFORM_IMAGE="${PLATFORM_IMAGE:-orbbec-agent-platform-demo-preview:rollback}" \
      PLATFORM_POSTGRES_PREVIEW_ADDRESS="$postgres_address" \
      PLATFORM_EDGE_GATEWAY_PREVIEW_ADDRESS="$edge_gateway_address" \
      "${preview_stack[@]}" stop \
      platform-api-demo-preview platform-loopback-demo-preview >/dev/null 2>&1
    PLATFORM_IMAGE="${PLATFORM_IMAGE:-orbbec-agent-platform-demo-preview:rollback}" \
      PLATFORM_POSTGRES_PREVIEW_ADDRESS="$postgres_address" \
      PLATFORM_EDGE_GATEWAY_PREVIEW_ADDRESS="$edge_gateway_address" \
      "${preview_stack[@]}" rm -f \
      platform-api-demo-preview platform-loopback-demo-preview >/dev/null 2>&1
  else
    local running
    running="$(
      /usr/bin/docker ps -aq --filter label=com.docker.compose.service=platform-api-demo-preview
      /usr/bin/docker ps -aq --filter label=com.docker.compose.service=platform-loopback-demo-preview
    )"
    [[ -z "$running" ]] || fail
  fi
}

if [[ ! -e "$active_state" && ! -L "$active_state" ]]; then
  [[ -f "$agent_enabled" ]] || fail
  orphan_include_count="$(/usr/bin/grep -Fc 'include /etc/nginx/snippets/orbbec-agent-demo-preview.conf;' "$agent_enabled" || true)"
  if [[ "$orphan_include_count" != "0" || -e "$snippet_target" || -L "$snippet_target" ]]; then
    echo "AGENT_DEMO_PREVIEW_ROLLBACK_FAILED orphaned_preview_state" >&2
    exit 1
  fi
  stop_demo_services
  echo "AGENT_DEMO_PREVIEW_ROLLBACK_OK state=already-absent"
  exit 0
fi
[[ -f "$active_state" && ! -L "$active_state" ]] || fail
[[ "$(/usr/bin/stat -c '%a %U' "$active_state")" == "600 root" ]] || fail
IFS= read -r backup_path < "$active_state" || fail
[[ "$backup_path" =~ ^/root/nginx-backups/agent-demo-preview-[0-9]{8}T[0-9]{6}Z$ ]] || fail
for required in agent-domain.conf.original agent-domain.conf.candidate agent-target-path; do
  [[ -f "$backup_path/$required" && ! -L "$backup_path/$required" ]] || fail
done

agent_target="$(/usr/bin/readlink -f "$agent_enabled")"
[[ "$agent_target" == /etc/nginx/* && -f "$agent_target" && ! -L "$agent_target" ]] || fail
agent_mode="$(/usr/bin/stat -c '%a' "$agent_target")"
[[ "$agent_mode" =~ ^(600|640|644)$ ]] || fail
[[ "$(< "$backup_path/agent-target-path")" == "$agent_target" ]] || fail
[[ -f "$snippet_target" && ! -L "$snippet_target" ]] || fail
[[ "$(/usr/bin/grep -Fxc '    include /etc/nginx/snippets/orbbec-agent-demo-preview.conf;' "$agent_target")" == "1" ]] || fail

rollback_work="$backup_path/rollback-work"
[[ ! -e "$rollback_work" ]] || /bin/rm -f -- "$rollback_work"
/usr/bin/install -o root -g root -m 600 "$agent_target" "$rollback_work"
/usr/bin/install -o root -g root -m 600 "$snippet_target" "$backup_path/snippet.rollback"

protected_container_invariants > "$backup_path/rollback-containers.before"
public_listener_invariants > "$backup_path/rollback-listeners.before"
{
  response_invariants agent_root https://agent.orbbec.com.cn/ agent.orbbec.com.cn:443:127.0.0.1
  response_invariants agent_admin https://agent.orbbec.com.cn/admin/ agent.orbbec.com.cn:443:127.0.0.1
  response_invariants fae_domain https://fae.orbbec.com.cn/ fae.orbbec.com.cn:443:127.0.0.1
  response_invariants fae_ip http://47.106.112.69/
} > "$backup_path/rollback-responses.before"

candidate="$backup_path/agent-domain.conf.rollback-candidate"
/usr/bin/python3 - "$agent_target" "$candidate" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
candidate = pathlib.Path(sys.argv[2])
value = source.read_text(encoding="utf-8")
line = "    include /etc/nginx/snippets/orbbec-agent-demo-preview.conf;\n"
if value.count(line) != 1:
    raise SystemExit(1)
updated = value.replace(line, "", 1)
if "include /etc/nginx/snippets/orbbec-agent-demo-preview.conf;" in updated:
    raise SystemExit(1)
candidate.write_text(updated, encoding="utf-8")
PY

rollback_required=1
reload_completed=0
restore_on_failure() {
  local exit_code=$?
  if [[ "$rollback_required" == "1" ]]; then
    /usr/bin/install -o root -g root -m "$agent_mode" "$rollback_work" "$agent_target.part"
    /bin/mv -f -- "$agent_target.part" "$agent_target"
    /usr/bin/install -o root -g root -m 644 "$backup_path/snippet.rollback" "$snippet_target.part"
    /bin/mv -f -- "$snippet_target.part" "$snippet_target"
    if /usr/sbin/nginx -t >/dev/null 2>&1 && [[ "$reload_completed" == "1" ]]; then
      /bin/systemctl reload nginx >/dev/null 2>&1 || true
    fi
  fi
  if [[ "$exit_code" -ne 0 ]]; then
    echo "AGENT_DEMO_PREVIEW_ROLLBACK_FAILED" >&2
  fi
}
trap restore_on_failure EXIT

/usr/bin/install -o root -g root -m "$agent_mode" "$candidate" "$agent_target.part"
/bin/mv -f -- "$agent_target.part" "$agent_target"
/bin/rm -f -- "$snippet_target"
/usr/sbin/nginx -t >/dev/null 2>&1 || fail
/bin/systemctl reload nginx
reload_completed=1

protected_container_invariants > "$backup_path/rollback-containers.after-nginx"
public_listener_invariants > "$backup_path/rollback-listeners.after-nginx"
{
  response_invariants agent_root https://agent.orbbec.com.cn/ agent.orbbec.com.cn:443:127.0.0.1
  response_invariants agent_admin https://agent.orbbec.com.cn/admin/ agent.orbbec.com.cn:443:127.0.0.1
  response_invariants fae_domain https://fae.orbbec.com.cn/ fae.orbbec.com.cn:443:127.0.0.1
  response_invariants fae_ip http://47.106.112.69/
} > "$backup_path/rollback-responses.after-nginx"

/usr/bin/cmp -s "$backup_path/rollback-containers.before" "$backup_path/rollback-containers.after-nginx" || fail
/usr/bin/cmp -s "$backup_path/rollback-listeners.before" "$backup_path/rollback-listeners.after-nginx" || fail
/usr/bin/cmp -s "$backup_path/rollback-responses.before" "$backup_path/rollback-responses.after-nginx" || fail
[[ "$(/usr/bin/grep -Fc 'include /etc/nginx/snippets/orbbec-agent-demo-preview.conf;' "$agent_target" || true)" == "0" ]] || fail
/usr/sbin/nginx -t >/dev/null 2>&1 || fail

rollback_required=0
trap - EXIT
/bin/rm -f -- "$active_state"
stop_demo_services

protected_container_invariants > "$backup_path/rollback-containers.after-stop"
/usr/bin/cmp -s "$backup_path/rollback-containers.before" "$backup_path/rollback-containers.after-stop" || fail
echo "AGENT_DEMO_PREVIEW_ROLLBACK_OK"
