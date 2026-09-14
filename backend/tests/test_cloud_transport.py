from pathlib import Path
import re


ROOT = Path(__file__).parents[2]
PUSH = ROOT / "deploy" / "cloud" / "push-replica.sh"
FORCED = ROOT / "deploy" / "cloud" / "forced-import.sh"
BOOTSTRAP = ROOT / "deploy" / "cloud" / "bootstrap-keys.sh"


def test_push_is_noninteractive_stdin_only_and_deletes_after_exact_ack():
    script = PUSH.read_text(encoding="utf-8")

    for option in (
        "BatchMode=yes",
        "IdentitiesOnly=yes",
        "ConnectTimeout=8",
        "StrictHostKeyChecking=yes",
    ):
        assert option in script
    assert '< "$batch_path"' in script
    assert 'rm -f -- "$batch_path"' in script
    assert script.index("REPLICA_IMPORT_OK") < script.index('rm -f -- "$batch_path"')
    assert "REPLICA_PUSH_FAILED" in script
    assert "security " not in script
    assert "ssh-add" not in script
    assert not re.search(r"ssh .*\$\(cat", script)


def test_forced_import_rejects_commands_and_prints_bounded_acknowledgement():
    script = FORCED.read_text(encoding="utf-8")

    assert "SSH_ORIGINAL_COMMAND" in script
    assert "docker run --rm -i" in script
    assert "--network orbbec-agent-platform-internal" in script
    assert "{{.Image}}" in script
    assert "run --rm --no-deps -T" not in script
    assert "orbbec-agent-platform-import-secrets:/run/import-secrets:ro" in script
    assert "PLATFORM_REPLICA_ENCRYPTION_KEY_FILE=/run/import-secrets/replica-encryption-key" in script
    assert "PLATFORM_REPLICA_SIGNING_PUBLIC_KEY_FILE=/run/import-secrets/replica-signing-public-key" in script
    assert "platform-api" in script
    assert "app.cloud_replica.cli import" in script
    assert "REPLICA_IMPORT_OK sequence=" in script
    assert "digest=" in script
    assert "replay=" in script
    assert "$@" not in script


def test_import_secret_volume_contains_every_key_required_by_import_and_retention():
    stage = (ROOT / "deploy" / "cloud" / "remote-stage.sh").read_text(
        encoding="utf-8"
    )
    backup = (ROOT / "deploy" / "cloud" / "backup.sh").read_text(
        encoding="utf-8"
    )

    import_copy = next(
        line for line in stage.splitlines()
        if "cp /source/replica-import-database-url" in line
    )
    assert "/source/replica-encryption-key" in import_copy
    assert "/source/replica-signing-public-key" in import_copy
    assert "PLATFORM_REPLICA_ENCRYPTION_KEY_FILE=/run/import-secrets/replica-encryption-key" in backup


def test_bootstrap_emits_restricted_authorized_key_and_never_rotates_keys():
    script = BOOTSTRAP.read_text(encoding="utf-8")

    for option in (
        "restrict",
        "command=",
        "no-pty",
        "no-agent-forwarding",
        "no-port-forwarding",
        "no-X11-forwarding",
    ):
        assert option in script
    assert "ssh-keygen" in script
    assert 'if [[ ! -e "$identity_key" ]]' in script
    assert 'if [[ ! -e "$signing_private" ]]' in script
    assert 'if [[ ! -e "$ssh_private" ]]' in script
    assert "remote AES" in script
    assert "Keychain" not in script


def test_import_uses_running_image_digest_and_rejects_untrusted_tags(tmp_path):
    """Exercise the real shell; Docker is replaced, no container or network call."""
    import json
    import os
    import subprocess
    import sys

    docker = tmp_path / 'docker'
    docker.write_text(f'#!{sys.executable}\n' + '''import json,os,sys
from pathlib import Path
args=sys.argv[1:]
if args[0]=='compose': print('running-api')
elif args[0]=='inspect': print('sha256:'+'a'*64)
elif args[:2]==['image','inspect']: print(os.environ['TEST_IMAGE_TAG'])
elif args[0]=='run':
 Path(os.environ['TEST_RUN_LOG']).write_text(json.dumps(args))
 print(json.dumps({'status':'imported','sequence':7,'digest':'b'*64}))
else: sys.exit(2)
''')
    docker.chmod(0o700)
    compose = tmp_path / 'compose.yaml'; compose.write_text('services: {}')
    config = tmp_path / 'platform.env'; config.write_text('')
    script = tmp_path / 'forced-import.sh'
    script.write_text(FORCED.read_text().replace('/usr/bin/docker', str(docker)).replace('/usr/bin/python3', sys.executable))
    log = tmp_path / 'run.json'
    environment = {**os.environ, 'CLOUD_PLATFORM_COMPOSE_FILE': str(compose),
                   'CLOUD_PLATFORM_ENV_FILE': str(config), 'TEST_RUN_LOG': str(log),
                   'TEST_IMAGE_TAG': 'orbbec-agent-platform:'+'c'*40, 'SSH_ORIGINAL_COMMAND': ''}
    accepted = subprocess.run(['/bin/bash', str(script)], input='signed batch', text=True, capture_output=True, env=environment)
    assert accepted.returncode == 0, accepted.stderr
    assert 'REPLICA_IMPORT_OK sequence=7' in accepted.stdout
    assert 'sha256:'+'a'*64 in json.loads(log.read_text())
    log.unlink()
    for tags in ['different-application:latest', '']:
        rejected = subprocess.run(['/bin/bash', str(script)], input='signed batch', text=True, capture_output=True,
                                  env={**environment, 'TEST_IMAGE_TAG': tags})
        assert rejected.returncode != 0
        assert not log.exists()
