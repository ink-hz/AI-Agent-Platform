#!/bin/bash
set -euo pipefail
umask 077

fail() {
  echo "AGENT_DEMO_PREVIEW_INSTALL_FAILED" >&2
  exit 1
}

[[ "$EUID" -eq 0 && $# -le 1 ]] || fail
EXPECTED_LIVE_SHA256="${EXPECTED_LIVE_SHA256:-}"
[[ "$EXPECTED_LIVE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail

script_dir="$(/usr/bin/readlink -f "$(/usr/bin/dirname "$0")")"
snippet_source="${1:-$script_dir/demo-preview.nginx.conf}"
agent_enabled=/etc/nginx/sites-enabled/agent-domain.conf
snippet_target=/etc/nginx/snippets/orbbec-agent-demo-preview.conf
state_dir=/var/lib/orbbec-agent-demo-preview
active_state="$state_dir/active-backup"
timestamp="$(/usr/bin/date -u +%Y%m%dT%H%M%SZ)"
backup_path="/root/nginx-backups/agent-demo-preview-$timestamp"

for source_path in "$snippet_source" "$agent_enabled"; do
  [[ -f "$source_path" && ! -L "$source_path" ]] || {
    [[ "$source_path" == "$agent_enabled" && -L "$source_path" && -f "$source_path" ]] || fail
  }
done
[[ "$snippet_source" == /* ]] || fail
[[ "$(/usr/bin/stat -c '%U' "$snippet_source")" == "root" ]] || fail
[[ "$(/usr/bin/stat -c '%a' "$snippet_source")" =~ ^(600|640|644)$ ]] || fail
[[ ! -e "$snippet_target" && ! -L "$snippet_target" ]] || fail
[[ ! -e "$active_state" && ! -L "$active_state" ]] || fail

agent_target="$(/usr/bin/readlink -f "$agent_enabled")"
[[ "$agent_target" == /etc/nginx/* && -f "$agent_target" && ! -L "$agent_target" ]] || fail
agent_mode="$(/usr/bin/stat -c '%a' "$agent_target")"
[[ "$agent_mode" =~ ^(600|640|644)$ ]] || fail
[[ "$(/usr/bin/sha256sum "$agent_target" | /usr/bin/awk '{print $1}')" == "$EXPECTED_LIVE_SHA256" ]] || fail
[[ "$(/usr/bin/grep -Fxc '    include /etc/nginx/snippets/orbbec-agent-demo-preview.conf;' "$agent_target" || true)" == "0" ]] || fail

/usr/bin/install -d -o root -g root -m 700 "$backup_path" "$state_dir"
/bin/cp -a -- "$agent_target" "$backup_path/agent-domain.conf.original"
/usr/bin/printf '%s\n' "$agent_target" > "$backup_path/agent-target-path"
/usr/bin/printf '%s\n' "$EXPECTED_LIVE_SHA256" > "$backup_path/expected-live-sha256"
/usr/bin/sha256sum "$snippet_source" > "$backup_path/snippet-source.sha256"
/bin/chown -R root:root "$backup_path"
/bin/chmod 600 "$backup_path"/*

enabled_invariants() {
  /usr/bin/python3 - "$agent_enabled" <<'PY'
import hashlib
import pathlib
import sys

excluded = pathlib.Path(sys.argv[1])
for path in sorted(pathlib.Path("/etc/nginx/sites-enabled").iterdir()):
    if path == excluded:
        continue
    if not (path.is_file() or path.is_symlink()):
        continue
    target = path.resolve(strict=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print(f"{path.name}\t{path.readlink() if path.is_symlink() else '-'}\t{digest}")
PY
}

container_invariants() {
  local name
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    /usr/bin/docker inspect --format \
      '{{.Name}}|{{.Id}}|{{.Config.Image}}|{{.State.StartedAt}}|{{.RestartCount}}' \
      "$name"
  done < <(/usr/bin/docker ps --format '{{.Names}}' | /usr/bin/sort)
}

listener_invariants() {
  /usr/bin/ss -H -lnt | /usr/bin/awk '{print $4}' | /usr/bin/sort -u
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

enabled_invariants > "$backup_path/sites-enabled.before"
container_invariants > "$backup_path/containers.before"
listener_invariants > "$backup_path/listeners.before"
{
  response_invariants agent_root https://agent.orbbec.com.cn/ agent.orbbec.com.cn:443:127.0.0.1
  response_invariants agent_admin https://agent.orbbec.com.cn/admin/ agent.orbbec.com.cn:443:127.0.0.1
  response_invariants fae_domain https://fae.orbbec.com.cn/ fae.orbbec.com.cn:443:127.0.0.1
  response_invariants fae_ip http://47.106.112.69/
} > "$backup_path/responses.before"

candidate="$backup_path/agent-domain.conf.candidate"
# Select exactly the `listen 443 ssl` server for
# `server_name agent.orbbec.com.cn`, then its root with
# `proxy_pass http://127.0.0.1:8080`.  This deliberately cannot select the
# HTTP redirect server or the independently managed /admin location.
/usr/bin/python3 - "$agent_target" "$candidate" <<'PY'
import pathlib
import re
import sys

source = pathlib.Path(sys.argv[1])
candidate = pathlib.Path(sys.argv[2])
value = source.read_text(encoding="utf-8")
include = "include /etc/nginx/snippets/orbbec-agent-demo-preview.conf;"
if include in value:
    raise SystemExit(1)

def block_end(text: str, opening: int) -> int:
    depth = 0
    quote = None
    escaped = False
    comment = False
    for index in range(opening, len(text)):
        char = text[index]
        if comment:
            if char == "\n":
                comment = False
            continue
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            continue
        if char == "#":
            comment = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    raise ValueError("unterminated nginx block")

servers = []
for match in re.finditer(r"(?m)^[ \t]*server[ \t]*\{", value):
    opening = value.index("{", match.start())
    end = block_end(value, opening)
    body = value[opening + 1 : end - 1]
    if (
        re.search(r"(?m)^\s*listen\s+443\s+ssl;", body)
        and re.search(r"(?m)^\s*server_name\s+agent\.orbbec\.com\.cn;", body)
    ):
        servers.append((opening + 1, end - 1, body))
if len(servers) != 1:
    raise SystemExit(1)

server_start, _, server_body = servers[0]
roots = []
for match in re.finditer(r"(?m)^([ \t]*)location\s+/\s*\{", server_body):
    absolute = server_start + match.start()
    opening = value.index("{", absolute)
    end = block_end(value, opening)
    body = value[opening + 1 : end - 1]
    if re.search(r"(?m)^\s*proxy_pass\s+http://127\.0\.0\.1:8080;", body):
        roots.append((absolute, match.group(1)))
if len(roots) != 1:
    raise SystemExit(1)

position, indent = roots[0]
inserted = f"{indent}{include}\n\n"
updated = value[:position] + inserted + value[position:]
if updated.replace(inserted, "", 1) != value or updated.count(include) != 1:
    raise SystemExit(1)
candidate.write_text(updated, encoding="utf-8")
PY
/bin/chown root:root "$candidate"
/bin/chmod 600 "$candidate"

# The candidate differs from the live file by the one include only.  Installing
# the two staged files changes no running worker until nginx -t succeeds and the
# explicit reload below is reached.
files_touched=0
reload_completed=0
restore_on_failure() {
  local exit_code=$?
  if [[ "$files_touched" == "1" ]]; then
    /usr/bin/install -o root -g root -m "$agent_mode" "$backup_path/agent-domain.conf.original" "$agent_target.part"
    /bin/mv -f -- "$agent_target.part" "$agent_target"
    /bin/rm -f -- "$snippet_target"
    if /usr/sbin/nginx -t >/dev/null 2>&1 && [[ "$reload_completed" == "1" ]]; then
      /bin/systemctl reload nginx >/dev/null 2>&1 || true
    fi
  fi
  /bin/rm -f -- "$active_state.part"
  if [[ "$exit_code" -ne 0 ]]; then
    echo "AGENT_DEMO_PREVIEW_INSTALL_FAILED" >&2
  fi
}
trap restore_on_failure EXIT

/usr/bin/install -o root -g root -m 644 "$snippet_source" "$snippet_target.part"
/bin/mv -f -- "$snippet_target.part" "$snippet_target"
/usr/bin/install -o root -g root -m "$agent_mode" "$candidate" "$agent_target.part"
/bin/mv -f -- "$agent_target.part" "$agent_target"
files_touched=1

/usr/sbin/nginx -t >/dev/null 2>&1 || fail
/bin/systemctl reload nginx
reload_completed=1

enabled_invariants > "$backup_path/sites-enabled.after"
container_invariants > "$backup_path/containers.after"
listener_invariants > "$backup_path/listeners.after"
{
  response_invariants agent_root https://agent.orbbec.com.cn/ agent.orbbec.com.cn:443:127.0.0.1
  response_invariants agent_admin https://agent.orbbec.com.cn/admin/ agent.orbbec.com.cn:443:127.0.0.1
  response_invariants fae_domain https://fae.orbbec.com.cn/ fae.orbbec.com.cn:443:127.0.0.1
  response_invariants fae_ip http://47.106.112.69/
} > "$backup_path/responses.after"

/usr/bin/cmp -s "$backup_path/sites-enabled.before" "$backup_path/sites-enabled.after" || fail
/usr/bin/cmp -s "$backup_path/containers.before" "$backup_path/containers.after" || fail
/usr/bin/cmp -s "$backup_path/listeners.before" "$backup_path/listeners.after" || fail
/usr/bin/cmp -s "$backup_path/responses.before" "$backup_path/responses.after" || fail
/usr/bin/cmp -s "$candidate" "$agent_target" || fail
[[ "$(/usr/bin/sha256sum "$snippet_target" | /usr/bin/awk '{print $1}')" == "$(/usr/bin/sha256sum "$snippet_source" | /usr/bin/awk '{print $1}')" ]] || fail
[[ "$(/usr/bin/grep -Fxc '    include /etc/nginx/snippets/orbbec-agent-demo-preview.conf;' "$agent_target")" == "1" ]] || fail
/usr/sbin/nginx -t >/dev/null 2>&1 || fail

/usr/bin/printf '%s\n' "$backup_path" > "$active_state.part"
/bin/chown root:root "$active_state.part"
/bin/chmod 600 "$active_state.part"
/bin/mv -f -- "$active_state.part" "$active_state"
files_touched=0
trap - EXIT
echo "AGENT_DEMO_PREVIEW_INSTALL_OK"
