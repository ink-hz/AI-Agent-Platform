import json
import os
from datetime import datetime, timezone
from pathlib import Path
import psycopg

output = {"observed_at": datetime.now(timezone.utc).isoformat(), "scope": "production API database identity and migration receipt only"}
dsn = Path(os.environ["PLATFORM_CONTROL_DATABASE_URL_FILE"]).read_text().strip()
with psycopg.connect(dsn, options="-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=1000") as conn:
    output["transaction_read_only"] = conn.execute("SHOW transaction_read_only").fetchone()[0]
    output["database_identity"] = conn.execute("SELECT current_database(),current_user").fetchone()
    try:
        with conn.transaction():
            cursor = conn.execute("SELECT version,sha256,applied_at FROM platform_control.schema_migrations WHERE version IN (64,100) ORDER BY version")
            output["migration_receipts"] = [dict(zip([c.name for c in cursor.description], row)) for row in cursor.fetchall()]
    except psycopg.Error as error:
        output["migration_receipts"] = {"unavailable_sqlstate": error.sqlstate}
    conn.rollback()
print(json.dumps(output,default=str,indent=2))
