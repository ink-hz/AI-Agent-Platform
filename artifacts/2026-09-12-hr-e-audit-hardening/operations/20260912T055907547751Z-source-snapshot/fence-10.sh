(
set -euo pipefail
legacy_hr_ids=$("${hr_compose[@]}" --profile hr-web ps -q platform-hr-web-worker)
test -n "$legacy_hr_ids"
"${hr_compose[@]}" --profile hr-web stop platform-hr-web-worker
for legacy_hr_id in $legacy_hr_ids; do
  test "$(/usr/bin/docker inspect --format '{{.State.Running}}' "$legacy_hr_id")" = false
done
)
