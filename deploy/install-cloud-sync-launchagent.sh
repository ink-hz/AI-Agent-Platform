#!/bin/bash
set -euo pipefail
umask 077

label="com.orbbec.ai-agent-platform-cloud-sync"
if [[ $# -ne 2 || "$1" != /* || "$2" != /* || "$(/usr/bin/basename "$2")" != "$label.plist" ]]; then
  echo "CLOUD_SYNC_INSTALL_FAILED" >&2
  exit 1
fi
config_path="$1"
target_path="$2"
if [[ ! -f "$config_path" || -L "$config_path" || "$(/usr/bin/stat -f '%Lp' "$config_path")" != "600" ]]; then
  echo "CLOUD_SYNC_INSTALL_FAILED" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$config_path"
set +a
if [[ -z "${CLOUD_SYNC_LOG_DIR:-}" || "$CLOUD_SYNC_LOG_DIR" != /* ]]; then
  echo "CLOUD_SYNC_INSTALL_FAILED" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "$0")" && pwd)"
template_path="$script_dir/com.orbbec.ai-agent-platform-cloud-sync.plist.template"
push_script="$script_dir/cloud/push-replica.sh"
target_dir="$(dirname "$target_path")"
/bin/mkdir -p "$target_dir" "$CLOUD_SYNC_LOG_DIR"
/bin/chmod 700 "$CLOUD_SYNC_LOG_DIR"
temporary_path="$(/usr/bin/mktemp "$target_dir/.${label}.XXXXXX")"
cleanup() { /bin/rm -f -- "$temporary_path"; }
trap cleanup EXIT

/usr/bin/python3 - "$template_path" "$temporary_path" "$push_script" "$config_path" "$CLOUD_SYNC_LOG_DIR" <<'PY'
import pathlib
import sys

template, output, push, config, logs = map(pathlib.Path, sys.argv[1:])
value = template.read_text(encoding="utf-8")
replacements = {
    "__PUSH_SCRIPT__": str(push),
    "__CONFIG_PATH__": str(config),
    "__STDOUT_LOG__": str(logs / "cloud-sync.out.log"),
    "__STDERR_LOG__": str(logs / "cloud-sync.err.log"),
}
for source, target in replacements.items():
    value = value.replace(source, target)
output.write_text(value, encoding="utf-8")
PY
/bin/chmod 600 "$temporary_path"
/usr/bin/plutil -lint "$temporary_path" >/dev/null
/bin/mv -f "$temporary_path" "$target_path"
trap - EXIT

domain="gui/$(/usr/bin/id -u)"
/bin/launchctl bootout "$domain" "$target_path" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "$domain" "$target_path"
/bin/launchctl enable "$domain/$label"
echo "CLOUD_SYNC_INSTALLED"
