/usr/bin/docker run --cap-drop ALL --security-opt no-new-privileges:true --rm -i --read-only --user 0:0 \
  --network orbbec-agent-platform-internal \
  -v /opt/orbbec-agent-platform/private:/run/control-secrets:ro \
  -e HR_CUTOVER_REQUEST_ID=EXPLICIT_UUID \
  -e HR_CUTOVER_TARGET_PHASE=EXPLICIT_PHASE PLATFORM_IMAGE_SHA \
  python - <<'PY'
import json
import os
import psycopg
from psycopg.rows import dict_row
from app.local_secrets import read_secret_file

# HR_CUTOVER_TRANSITION
target = os.environ["HR_CUTOVER_TARGET_PHASE"]
if target not in {"draining_legacy", "cloud", "draining_cloud"}:
    raise ValueError("invalid HR cutover target phase")
dsn = read_secret_file("/run/control-secrets/control-maintenance-database-url")
with psycopg.connect(dsn, connect_timeout=3, autocommit=True, row_factory=dict_row) as connection:
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '2s'")
        connection.execute("SET LOCAL statement_timeout = '3s'")
        receipt = connection.execute(
            "select * from platform_control.transition_hr_execution_cutover_v102(%s,%s)",
            (target, os.environ["HR_CUTOVER_REQUEST_ID"]),
        ).fetchone()
print(json.dumps(receipt, default=str))
PY
