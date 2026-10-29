#!/bin/bash
set -euo pipefail
umask 077

# Install this script and its reviewed helper together in an immutable directory.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/python3 "$script_dir/backup_runtime.py" "$@"
