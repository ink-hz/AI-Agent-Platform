from app.config import load_config


def test_remote_sync_config_defaults(monkeypatch) -> None:
    for name in (
        "PLATFORM_SYNC_KEYCHAIN_SERVICE",
        "PLATFORM_SYNC_KEYCHAIN_ACCOUNT",
        "PLATFORM_REMOTE_SSH_HOST",
        "PLATFORM_REMOTE_SSH_KEY_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert config.sync_keychain_service == "platform-sync-database-url"
    assert config.sync_keychain_account == "neo"
    assert config.remote_ssh_host == "root@47.106.112.69"
    assert config.remote_ssh_key_path == "/Users/neo/.ssh/orbbec_aliyun_ed25519"


def test_remote_sync_config_accepts_environment_overrides(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_SYNC_KEYCHAIN_SERVICE", "sync-test")
    monkeypatch.setenv("PLATFORM_SYNC_KEYCHAIN_ACCOUNT", "operator")
    monkeypatch.setenv("PLATFORM_REMOTE_SSH_HOST", "agent@example.test")
    monkeypatch.setenv("PLATFORM_REMOTE_SSH_KEY_PATH", "/tmp/test-key")

    config = load_config()

    assert config.sync_keychain_service == "sync-test"
    assert config.sync_keychain_account == "operator"
    assert config.remote_ssh_host == "agent@example.test"
    assert config.remote_ssh_key_path == "/tmp/test-key"
