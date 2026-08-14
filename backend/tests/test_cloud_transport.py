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
    assert "docker run --rm" in script
    assert "--network orbbec-agent-platform-internal" in script
    assert "{{.Config.Image}}" in script
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
