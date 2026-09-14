"""Local causal tests; synthetic bytes, no Docker/SSH/model or production claims."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "incremental_install", Path(__file__).with_name("incremental_install.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Host:
    def __init__(self, fail=None, exists=False):
        self.events = []
        self.fail = fail
        self.exists = exists
        self.old = b"old secret and knowledge unchanged"

    def call(self, op, **kwargs):
        self.events.append(op)
        if op == self.fail:
            raise RuntimeError("synthetic private error must not escape")
        if op == "verify_inputs":
            return {
                "image": m.IMAGE,
                "knowledge": m.KNOWLEDGE,
                "configuration": "a" * 64,
            }
        if op == "exists":
            return self.exists
        if op == "verify_installed":
            return {
                "bytes_equal": True,
                "runtime_loaded": True,
                "personal_materials": False,
            }
        return None


def test_prepare_publishes_only_after_copy_and_actual_runtime_verification():
    h = Host()
    receipt = m.install(h, "a" * 32)
    assert h.events == [
        "verify_inputs",
        "exists",
        "reserve",
        "copy_generation",
        "copy_volume",
        "verify_installed",
        "publish",
    ]
    assert receipt["status"] == "prepared_not_started"
    assert receipt["schema_106_verified"] is False
    assert h.old == b"old secret and knowledge unchanged"


@pytest.mark.parametrize(
    "failure", ["copy_generation", "copy_volume", "verify_installed", "publish"]
)
def test_partial_failure_never_deletes_or_activates_old_or_new(failure):
    h = Host(fail=failure)
    with pytest.raises(m.InstallError, match="installation_incomplete"):
        m.install(h, "b" * 32)
    assert "failure_receipt" in h.events
    assert not any(x in h.events for x in ("start", "delete", "switch_current"))
    if failure != "publish":
        assert "publish" not in h.events
    assert h.old == b"old secret and knowledge unchanged"


def test_existing_target_is_never_adopted_or_deleted():
    h = Host(exists=True)
    with pytest.raises(m.InstallError, match="target_exists"):
        m.install(h, "c" * 32)
    assert h.events == ["verify_inputs", "exists"]


def test_bad_verification_cannot_publish():
    h = Host()
    orig = h.call

    def call(op, **kwargs):
        result = orig(op, **kwargs)
        return (
            {"bytes_equal": True, "runtime_loaded": False, "personal_materials": False}
            if op == "verify_installed"
            else result
        )

    h.call = call
    with pytest.raises(m.InstallError):
        m.install(h, "d" * 32)
    assert "publish" not in h.events


def test_policy_recomputed_without_broadening_or_altering_provider():
    profiles = {
        "provider": {"model": "synthetic"},
        "budget": {"limit": 2},
        "diagnostic": {"enabled": False},
    }
    policy = {
        "version": 1,
        "scope": "public-only",
        "authorization_ref": "synthetic authorization",
        "configuration_sha256": "old",
        "usage_accounting": "conservative_estimate_not_invoice",
    }
    updated = m.update_policy(profiles, policy)
    assert updated["configuration_sha256"] == m.digest(m.canonical(profiles))
    assert policy["configuration_sha256"] == "old"
    assert updated["scope"] == "public-only"
    policy["scope"] = "all"
    with pytest.raises(m.InstallError):
        m.update_policy(profiles, policy)


@pytest.fixture
def disk_host(tmp_path, monkeypatch):
    import io
    import os
    import tarfile

    monkeypatch.setattr(m.os, "chown", lambda *args: None)
    original_protected = m.protected
    monkeypatch.setattr(
        m,
        "protected",
        lambda path, uid, directory=False: original_protected(
            path, os.getuid(), directory
        ),
    )
    inputs = tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    old = tmp_path / "old"
    old.mkdir(mode=0o700)
    api = tmp_path / "api"
    api.mkdir(mode=0o700)
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "current.json").write_text('{"release_id":"retained-old"}')
    (knowledge / "old-content.md").write_text("synthetic retained previous release")
    generation = tmp_path / "generations" / "new"
    profiles = {
        "provider": {"model": "synthetic"},
        "budget": {"limit": 2},
        "diagnostic": {"enabled": False},
    }
    policy = m.update_policy(
        profiles,
        {
            "version": 1,
            "scope": "public-only",
            "authorization_ref": "synthetic",
            "configuration_sha256": "",
            "usage_accounting": "conservative_estimate_not_invoice",
        },
    )
    content = {f"hr-{k}-profile.json": m.canonical(v) for k, v in profiles.items()}
    content.update(
        {
            "hr-release-policy.json": m.canonical(policy),
            "hr-content-keyring.json": b"synthetic keyring",
            "hr-provider-credential": b"synthetic credential",
        }
    )
    for name, payload in content.items():
        m.write_new(inputs / name, payload)
        m.write_new(old / name, payload)
    for name in m.SHARED:
        m.write_new(old / name, b"synthetic shared")
        m.write_new(api / name, b"synthetic shared")
    for name in m.ENVS:
        m.write_new(old / name, b"synthetic environment")
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for name, payload in {
            "current.json": m.canonical({"release_id": m.KNOWLEDGE}),
            f"releases/{m.KNOWLEDGE}/manifest.json": b"{}",
        }.items():
            item = tarfile.TarInfo(name)
            item.size = len(payload)
            tar.addfile(item, io.BytesIO(payload))
    m.write_new(inputs / "knowledge.tar.gz", archive.getvalue())
    monkeypatch.setattr(m, "ARCHIVE_SHA", m.digest(archive.getvalue()))
    monkeypatch.setattr(m, "GENERATION", generation)
    monkeypatch.setattr(m, "OLD_KNOWLEDGE", knowledge)
    runtime = tmp_path / "runtime.env"
    m.write_new(runtime, b"BUSINESS=synthetic\nPLATFORM_IMAGE=old\n")
    monkeypatch.setattr(m, "OLD_RUNTIME", runtime)
    monkeypatch.setattr(m, "COMPOSE_SHA", {})
    h = m.Host(inputs)
    dest = tmp_path / "volume"

    def volume(name):
        if name == m.OLD_VOLUME:
            return old, {}
        if name == m.API_VOLUME:
            return api, {}
        return dest, {"Labels": {"hr.incremental.run": h.run_id}}

    h.volume = volume

    def docker(*args, **kwargs):
        if args[:2] == ("image", "inspect"):
            return m.canonical([{"Id": m.IMAGE}])
        if args[:2] == ("volume", "ls"):
            return b""
        if args[:2] == ("volume", "create"):
            dest.mkdir()
            return b""
        if args[0] == "compose":
            service = {
                "image": m.IMAGE,
                "user": "10001:10001",
                "read_only": True,
                "environment": {"PLATFORM_HR_AGENT_ENABLED": "1"},
                "volumes": [
                    {
                        "target": "/data/hr-knowledge",
                        "type": "bind",
                        "source": str(generation / "knowledge"),
                        "read_only": True,
                    },
                    {
                        "target": "/run/hr-agent-secrets",
                        "type": "volume",
                        "source": "platform-hr-agent-secrets",
                        "read_only": True,
                    },
                ],
            }
            return m.canonical(
                {
                    "services": {
                        "platform-api": service,
                        "platform-hr-agent-worker": service,
                    },
                    "volumes": {"platform-hr-agent-secrets": {"name": m.VOLUME}},
                }
            )
        if args[0] == "start":
            return m.canonical([h.revision, m.KNOWLEDGE])
        if args[0] == "inspect":
            return m.canonical(
                [
                    {
                        "Id": "9" * 64,
                        "Name": "/hr-config-check-" + h.run_id,
                        "Image": m.IMAGE,
                        "Config": {"Labels": {"hr.config-check.run": h.run_id}},
                        "State": {"Running": False, "ExitCode": 0},
                    }
                ]
            )
        return b""

    h.docker = docker
    return h, old, api, knowledge, generation, dest


def test_filesystem_adapter_copies_identical_bytes_retains_old_pointer(disk_host):
    h, old, _api, knowledge, generation, dest = disk_host
    before = {p.name: p.read_bytes() for p in old.iterdir()}
    receipt = m.install(h, "e" * 32)
    assert receipt["status"] == "prepared_not_started"
    assert {p.name: p.read_bytes() for p in old.iterdir()} == before
    assert (knowledge / "current.json").read_text() == '{"release_id":"retained-old"}'
    assert (generation / "knowledge/old-content.md").read_bytes() == (
        knowledge / "old-content.md"
    ).read_bytes()
    assert all(
        (dest / name).read_bytes() == value
        for name, value in before.items()
        if name not in m.ENVS
    )
    assert all(
        (dest / name).read_bytes() == b"PLATFORM_HR_AGENT_ENABLED=1\n"
        for name in m.ENVS
    )
    assert all((p.stat().st_mode & 0o777) == 0o600 for p in dest.iterdir())


def test_shared_secret_drift_rejected_before_any_generation(disk_host):
    h, _old, api, _knowledge, generation, dest = disk_host
    (api / m.SHARED[0]).write_bytes(b"changed synthetic shared")
    with pytest.raises(m.InstallError, match="shared_secret_drift"):
        m.install(h, "f" * 32)
    assert not generation.exists() and not dest.exists()


def test_existing_volume_contents_are_never_overwritten_even_create_race(disk_host):
    h, _old, _api, _knowledge, generation, dest = disk_host
    original = h.docker

    def raced(*args, **kw):
        result = original(*args, **kw)
        if args[:2] == ("volume", "create"):
            (dest / "foreign").write_bytes(b"foreign synthetic")
        return result

    h.docker = raced
    with pytest.raises(m.InstallError):
        m.install(h, "1" * 32)
    assert (dest / "foreign").read_bytes() == b"foreign synthetic"
    assert not (generation / "prepared.json").exists()


def test_symlink_secret_rejected_before_mutation(disk_host):
    h, old, _api, _knowledge, generation, _dest = disk_host
    (h.inputs / m.FILES[0]).unlink()
    (h.inputs / m.FILES[0]).symlink_to(old / m.FILES[0])
    with pytest.raises(m.InstallError, match="protected_metadata_invalid"):
        m.install(h, "2" * 32)
    assert not generation.exists()


def test_preexisting_check_container_is_not_removed(disk_host):
    h, *_ = disk_host
    original = h.docker
    events = []

    def docker(*args, **kwargs):
        events.append(args)
        if args[0] == "ps":
            return b"hr-config-check-33333333333333333333333333333333\n"
        return original(*args, **kwargs)

    h.docker = docker
    with pytest.raises(m.InstallError):
        m.install(h, "3" * 32)
    assert not any(args[0] in ("rm", "start", "run", "create") for args in events)


@pytest.mark.parametrize("foreign", [False, True])
def test_unknown_create_response_cleanup_requires_exact_owned_id(disk_host, foreign):
    h, *_ = disk_host
    original = h.docker
    events = []

    def docker(*args, **kwargs):
        events.append(args)
        if args[0] == "create":
            raise m.InstallError("docker_operation_failed")
        if args[0] == "inspect" and foreign:
            import json

            obj = json.loads(original(*args, **kwargs))
            obj[0]["Config"]["Labels"] = {}
            return m.canonical(obj)
        return original(*args, **kwargs)

    h.docker = docker
    with pytest.raises(m.InstallError):
        m.install(h, "4" * 32)
    assert not any(args[0] == "start" for args in events)
    assert [args for args in events if args[0] == "rm"] == (
        [] if foreign else [("rm", "--force", "9" * 64)]
    )


def test_runtime_env_updates_only_image_keeps_business_and_worker_flag():
    payload = (
        b"BUSINESS=synthetic\nPLATFORM_IMAGE=old\nPLATFORM_HR_WEB_WORKER_ENABLED=1\n"
    )
    result = m.runtime_env(payload)
    assert (
        result
        == b"BUSINESS=synthetic\nPLATFORM_HR_WEB_WORKER_ENABLED=1\nPLATFORM_IMAGE="
        + m.IMAGE.encode()
        + b"\n"
    )


def test_rendered_compose_rejects_wrong_image_and_wrong_knowledge_mount():
    with pytest.raises(m.InstallError):
        m.rendered_envs({"services": {"platform-api": {"image": "old"}}})
