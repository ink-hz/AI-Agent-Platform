#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_ROOT="${SCRIPT_DIR:h}"
LABEL="com.orbbec.ai-agent-platform-sync"
TARGET_DIR="${HOME}/Library/LaunchAgents"
TARGET="${TARGET_DIR}/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
TEMP_FILE="$(mktemp)"
trap 'rm -f "$TEMP_FILE"' EXIT

mkdir -p "$TARGET_DIR"
sed "s|__REPO_ROOT__|${REPO_ROOT}|g" "${SCRIPT_DIR}/${LABEL}.plist" > "$TEMP_FILE"
plutil -lint "$TEMP_FILE" >/dev/null
install -m 600 "$TEMP_FILE" "$TARGET"

launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$TARGET"

echo "Installed ${LABEL} for daily 03:20 sync."
