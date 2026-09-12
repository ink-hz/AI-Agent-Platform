/usr/bin/docker run --cap-drop ALL --security-opt no-new-privileges:true --rm -i --read-only --user 0:0 \
  --network orbbec-agent-platform-internal \
  -v /opt/orbbec-agent-platform/private:/run/control-secrets:ro \
  PLATFORM_IMAGE_SHA python - <<'PY'
import json
import psycopg
from app.local_secrets import read_secret_file

# HR_CUTOVER_COUNT
dsn = read_secret_file("/run/control-secrets/control-maintenance-database-url")
with psycopg.connect(dsn, connect_timeout=3, autocommit=True) as connection:
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        connection.execute("SET LOCAL lock_timeout = '2s'")
        connection.execute("SET LOCAL statement_timeout = '3s'")
        counts = connection.execute(
            "select * from platform_control.hr_execution_cutover_counts_v102()"
        ).fetchone()
print(json.dumps(dict(zip(("legacy_nonterminal", "cloud_nonterminal"), counts))))
PY
