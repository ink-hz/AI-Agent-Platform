from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[2]
FORCED = ROOT / "deploy/cloud/forced-hr-position-import.sh"


def test_forced_hourly_import_accepts_only_its_restricted_stdin_protocol(tmp_path) -> None:
    docker = tmp_path / "docker"
    docker.write_text(f"#!{sys.executable}\n" + '''
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
if args[0]=='compose': print('running-api')
elif args[0]=='inspect': print('sha256:'+'a'*64)
elif args[:2]==['image','inspect']: print(os.environ['TEST_IMAGE_TAG'])
elif args[0]=='run':
 Path(os.environ['TEST_RUN_LOG']).write_text(json.dumps({'args':args,'stdin':sys.stdin.read()}))
 print(json.dumps({'status':'imported','owners':18,'jobs':94,'versions':22,
  'version':'2026-10-09T01-00-00Z_abcd','source_synced_at':'2026-10-09T01:00:00Z','sha256':'b'*64}))
else: sys.exit(2)
''')
    docker.chmod(0o700)
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}")
    environment = tmp_path / "platform.env"
    environment.write_text("")
    script = tmp_path / "forced.sh"
    script.write_text(
        FORCED.read_text().replace("/usr/bin/docker", str(docker))
        .replace("/usr/bin/python3", sys.executable)
    )
    log = tmp_path / "run.json"
    env = {**os.environ, "CLOUD_PLATFORM_COMPOSE_FILE": str(compose),
           "CLOUD_PLATFORM_ENV_FILE": str(environment), "TEST_RUN_LOG": str(log),
           "TEST_IMAGE_TAG": "orbbec-agent-platform:" + "c" * 40,
           "SSH_ORIGINAL_COMMAND": ""}

    result = subprocess.run(["/bin/bash", str(script)], input="published snapshot",
                            text=True, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "HR_OFFICIAL_IMPORT_OK sha256=" + "b" * 64
        + " version=2026-10-09T01-00-00Z_abcd status=imported owners=18 jobs=94\n"
    )
    arguments = json.loads(log.read_text())
    assert arguments["stdin"] == "published snapshot"
    assert "orbbec-agent-platform-api-secrets:/run/secrets:ro" in arguments["args"]
    assert "app.hr.hourly_sync" in arguments["args"]
    log.unlink()

    for override in ({"SSH_ORIGINAL_COMMAND": "id"}, {"TEST_IMAGE_TAG": "other:latest"}):
        rejected = subprocess.run(["/bin/bash", str(script)], input="published snapshot",
                                  text=True, capture_output=True, env={**env, **override})
        assert rejected.returncode != 0
        assert not log.exists()
