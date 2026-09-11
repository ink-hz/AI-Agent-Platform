#!/bin/bash
set -euo pipefail
script_directory="$(cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$script_directory/hr_agent_migrate.py" "$@"
