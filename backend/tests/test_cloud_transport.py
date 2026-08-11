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
    assert "exec -T platform-api" in script
    assert "app.cloud_replica.cli import" in script
    assert "REPLICA_IMPORT_OK sequence=" in script
    assert "digest=" in script
    assert "replay=" in script
    assert "$@" not in script


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
