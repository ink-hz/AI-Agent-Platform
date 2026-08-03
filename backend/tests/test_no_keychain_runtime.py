from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_active_runtime_and_operations_have_no_keychain_dependency():
    active_files = [
        *sorted((ROOT / "backend/app").rglob("*.py")),
        ROOT / "registry.yaml",
        ROOT / "README.md",
    ]
    banned = (
        "/usr/bin/" + "security",
        "key" + "chain:",
        "_KEY" + "CHAIN_",
        "key" + "chain_service",
        "key" + "chain_account",
        "security find-generic-password",
        "Key" + "chain service",
    )

    violations = []
    for path in active_files:
        content = path.read_text(encoding="utf-8")
        for marker in banned:
            if marker.lower() in content.lower():
                violations.append(f"{path.relative_to(ROOT)}:{marker}")

    assert violations == []


def test_live_registry_uses_private_replay_file():
    registry = (ROOT / "registry.yaml").read_text(encoding="utf-8")

    assert (
        'credential_ref: "file:/Users/neo/Library/Application Support/'
        'OrbbecAI-Agent-Platform/secrets/ai-fae-dev-replay-token"'
    ) in registry
