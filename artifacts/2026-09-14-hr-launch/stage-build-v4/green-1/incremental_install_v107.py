#!/usr/bin/env python3
"""Fixed incremental HR preparation. No SSH, migration, service start or pointer switch.

Host execution requires root and explicit --execute; caller transports the exact
private inputs first. Partial generations are retained for inspection, never adopted.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tarfile
from pathlib import Path

RELEASE = "UNBOUND_RELEASE_SHA"
IMAGE = "UNBOUND_IMAGE_ID"
KNOWLEDGE = "hr-intelligence-ad5f3cac253a6a28d3c764db"
ARCHIVE_SHA = "b5133cb22f498f51869bdc2518f6f466e8499b0533d287ea80a7e340ca87afd2"
GENERATION = Path("/data/orbbec-agent-platform/hr-generations/ad5f3cac253a6a28d3c764db")
VOLUME = "orbbec-agent-platform-hr-agent-secrets-ad5f3cac253a6a28d3c764db"
OLD_VOLUME = "orbbec-agent-platform-hr-agent-secrets"
API_VOLUME = "orbbec-agent-platform-api-secrets"
OLD_KNOWLEDGE = Path("/data/orbbec-agent-platform/hr-knowledge")
FILES = (
    "hr-budget-profile.json",
    "hr-content-keyring.json",
    "hr-diagnostic-profile.json",
    "hr-provider-credential",
    "hr-provider-profile.json",
    "hr-release-policy.json",
)
SHARED = (
    "control-database-url",
    "attachment-s3-access-key",
    "attachment-s3-secret-key",
    "content-encryption-keyring",
)
ENVS = ("api-runtime.env", "worker-runtime.env")
OLD_RUNTIME = Path("/opt/orbbec-agent-platform/private/hr-agent/runtime.env")
RELEASE_ROOT = Path("/opt/orbbec-agent-platform/releases") / RELEASE
OLD_RUNTIME_SHA = "68024083ee6b26e6033c8bab0074132df26bc2fc7012e5480efca0f639cfd2d8"
NEW_RUNTIME_SHA = "UNBOUND_RUNTIME_ENV_SHA"
COMPOSE_SHA = {
    "compose.yaml": "3a87066354ef2f9b17121af974756d657dc4fe3f2c3103d770e683d92a6ee86c",
    "compose.hr-agent.yaml": "d61a471c9d3a971df22d489ac8b7c1ea45ec5a15937f0001154c2226143b9e40",
}


class InstallError(Exception):
    pass


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def update_policy(profiles, policy):
    if (
        set(policy)
        != {
            "version",
            "scope",
            "authorization_ref",
            "configuration_sha256",
            "usage_accounting",
        }
        or policy["version"] != 1
        or policy["scope"] != "public-only"
        or policy["usage_accounting"] != "conservative_estimate_not_invoice"
        or not policy["authorization_ref"]
    ):
        raise InstallError("policy_invalid")
    return {**policy, "configuration_sha256": digest(canonical(profiles))}


def runtime_env(payload):
    if b"\x00" in payload:
        raise InstallError("runtime_environment_invalid")
    lines = payload.splitlines()
    return (
        b"\n".join(line for line in lines if not line.startswith(b"PLATFORM_IMAGE="))
        + b"\nPLATFORM_IMAGE="
        + IMAGE.encode()
        + b"\n"
    )


def overlay():
    return {
        "services": {
            name: {"volumes": [f"{GENERATION}/knowledge:/data/hr-knowledge:ro"]}
            for name in ("platform-api", "platform-hr-agent-worker")
        },
        "volumes": {"platform-hr-agent-secrets": {"external": True, "name": VOLUME}},
    }


def rendered_envs(document):
    result = {}
    try:
        if document["volumes"]["platform-hr-agent-secrets"]["name"] != VOLUME:
            raise ValueError()
        for service, filename in zip(
            ("platform-api", "platform-hr-agent-worker"), ENVS
        ):
            value = document["services"][service]
            if (
                value["image"] != IMAGE
                or str(value["user"]) != "10001:10001"
                or value["read_only"] is not True
            ):
                raise ValueError()
            mounts = {v["target"]: v for v in value["volumes"]}
            for target, kind, source in (
                ("/data/hr-knowledge", "bind", str(GENERATION / "knowledge")),
                ("/run/hr-agent-secrets", "volume", "platform-hr-agent-secrets"),
            ):
                mount = mounts[target]
                if (
                    mount["type"] != kind
                    or mount["source"] != source
                    or mount.get("read_only") is not True
                ):
                    raise ValueError()
            env = value["environment"]
            if env["PLATFORM_HR_AGENT_ENABLED"] != "1":
                raise ValueError()
            lines = []
            for key, val in sorted(env.items()):
                if (
                    not re.fullmatch("[A-Z][A-Z0-9_]*", key)
                    or not isinstance(val, str)
                    or any(c in val for c in ("\n", "\r", "\x00"))
                ):
                    raise ValueError()
                lines.append(key + "=" + val)
            result[filename] = ("\n".join(lines) + "\n").encode()
        return result
    except Exception:  # noqa: BLE001 - private adapter failures are fixed diagnostics
        raise InstallError("rendered_compose_invalid") from None


def require_binding():
    if (re.fullmatch('[0-9a-f]{40}', RELEASE) is None
            or re.fullmatch('sha256:[0-9a-f]{64}', IMAGE) is None
            or re.fullmatch('[0-9a-f]{64}', NEW_RUNTIME_SHA) is None):
        raise InstallError("release_identity_unbound")


def install(host, run_id):
    require_binding()
    if re.fullmatch("[0-9a-f]{32}", run_id) is None:
        raise InstallError("run_id_invalid")
    identity = host.call("verify_inputs")
    if identity.get("image") != IMAGE or identity.get("knowledge") != KNOWLEDGE:
        raise InstallError("identity_mismatch")
    if host.call("exists"):
        raise InstallError("target_exists")
    host.call("reserve", run_id=run_id)
    try:
        host.call("copy_generation")
        host.call("copy_volume")
        result = host.call("verify_installed")
        if result != {
            "bytes_equal": True,
            "runtime_loaded": True,
            "personal_materials": False,
        }:
            raise InstallError("verification_failed")
        receipt = {
            **identity,
            "run_id": run_id,
            "status": "prepared_not_started",
            "schema_106_verified": False,
            "installer_sha256": digest(Path(__file__).read_bytes()),
        }
        host.call("publish", receipt=receipt)
        return receipt
    except BaseException:  # noqa: BLE001 - interrupts must fail closed without private output
        host.call("failure_receipt")
        raise InstallError("installation_incomplete") from None


def protected(path, uid, directory=False):
    info = path.lstat()
    if (
        path.is_symlink()
        or info.st_uid != uid
        or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
    ):
        raise InstallError("protected_metadata_invalid")
    return path.read_bytes() if not directory else None


def write_new(path, payload, uid=0):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(payload)
        out.flush()
        os.fsync(out.fileno())
    os.chown(path, uid, uid)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def tree(path):
    result = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink() or not (item.is_dir() or item.is_file()):
            raise InstallError("knowledge_tree_invalid")
        if item.is_file():
            result[str(item.relative_to(path))] = digest(item.read_bytes())
    return result


class Host:
    def __init__(self, inputs):
        self.inputs = inputs
        self.container = None
        self.run_id = None

    def docker(self, *args, timeout=15):
        try:
            result = subprocess.run(
                ["/usr/bin/docker", *args],
                capture_output=True,
                timeout=timeout,
                check=True,
                env={"PATH": "/usr/bin:/bin", "HOME": "/root"},
            )
            return result.stdout
        except Exception:  # noqa: BLE001 - private adapter failures are fixed diagnostics
            raise InstallError("docker_operation_failed") from None

    def volume(self, name):
        obj = json.loads(self.docker("volume", "inspect", name))[0]
        path = Path(obj["Mountpoint"])
        if (
            obj["Name"] != name
            or obj["Driver"] != "local"
            or obj.get("Options")
            or path.is_symlink()
            or str(path) != f"/var/lib/docker/volumes/{name}/_data"
        ):
            raise InstallError("volume_identity_invalid")
        return path, obj

    def call(self, op, **kw):
        if op == "verify_inputs":
            protected(self.inputs, 0, True)
            self.config = {name: protected(self.inputs / name, 0) for name in FILES}
            self.archive = protected(self.inputs / "knowledge.tar.gz", 0)
            if digest(self.archive) != ARCHIVE_SHA:
                raise InstallError("archive_identity_invalid")
            profiles = {
                k: json.loads(self.config[f"hr-{k}-profile.json"])
                for k in ("provider", "budget", "diagnostic")
            }
            policy = json.loads(self.config["hr-release-policy.json"])
            if update_policy(profiles, policy) != policy:
                raise InstallError("policy_digest_mismatch")
            self.revision = digest(canonical({**profiles, "release_policy": policy}))
            self.old, _ = self.volume(OLD_VOLUME)
            api, _ = self.volume(API_VOLUME)
            self.api = api
            for name in FILES:
                if protected(self.old / name, 10001) != self.config[name]:
                    raise InstallError("existing_configuration_drift")
            self.shared = {name: protected(api / name, 10001) for name in SHARED}
            if any(
                protected(self.old / name, 10001) != value
                for name, value in self.shared.items()
            ):
                raise InstallError("shared_secret_drift")
            self.old_envs = {name: protected(self.old / name, 10001) for name in ENVS}
            self.runtime = protected(OLD_RUNTIME, 10001)
            if (
                digest(self.runtime) != OLD_RUNTIME_SHA
                or digest(runtime_env(self.runtime)) != NEW_RUNTIME_SHA
            ):
                raise InstallError("runtime_source_drift")
            for name, expected in COMPOSE_SHA.items():
                path = RELEASE_ROOT / "deploy/cloud" / name
                if path.is_symlink() or digest(path.read_bytes()) != expected:
                    raise InstallError("compose_source_drift")
            self.old_tree = tree(OLD_KNOWLEDGE)
            if json.loads(self.docker("image", "inspect", IMAGE))[0]["Id"] != IMAGE:
                raise InstallError("image_identity_invalid")
            return {
                "image": IMAGE,
                "release": RELEASE,
                "knowledge": KNOWLEDGE,
                "configuration": self.revision,
                "policy_configuration_sha256": policy["configuration_sha256"],
                "volume": VOLUME,
                "knowledge_host": str(GENERATION / "knowledge"),
            }
        if op == "exists":
            names = (
                self.docker("volume", "ls", "--format", "{{.Name}}")
                .decode()
                .splitlines()
            )
            return GENERATION.exists() or GENERATION.is_symlink() or VOLUME in names
        if op == "reserve":
            self.run_id = kw["run_id"]
            GENERATION.parent.mkdir(mode=0o700, exist_ok=True)
            GENERATION.mkdir(mode=0o700)
            write_new(
                GENERATION / "reservation.json",
                canonical({"run_id": self.run_id, "image": IMAGE, "volume": VOLUME}),
            )
        if op == "copy_generation":
            # Retain old releases for existing work; only this new generation gets a new pointer.
            shutil.copytree(OLD_KNOWLEDGE, GENERATION / "knowledge")
            with tarfile.open(fileobj=io.BytesIO(self.archive), mode="r:gz") as archive:
                members = archive.getmembers()
                seen = set()
                for member in members:
                    path = Path(member.name)
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or member.name in seen
                        or not (member.isfile() or member.isdir())
                        or (
                            member.name != "current.json"
                            and member.name != "releases"
                            and member.name != f"releases/{KNOWLEDGE}"
                            and not member.name.startswith(f"releases/{KNOWLEDGE}/")
                        )
                    ):
                        raise InstallError("archive_path_invalid")
                    seen.add(member.name)
                for member in members:
                    target = GENERATION / "knowledge" / member.name
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.name == "current.json":
                        target.unlink()
                        write_new(target, archive.extractfile(member).read())
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        write_new(target, archive.extractfile(member).read())
            if json.loads((GENERATION / "knowledge/current.json").read_bytes()) != {
                "release_id": KNOWLEDGE
            }:
                raise InstallError("knowledge_pointer_invalid")
            for path in (
                GENERATION / "knowledge",
                *(GENERATION / "knowledge").rglob("*"),
            ):
                os.chmod(path, 0o755 if path.is_dir() else 0o644)
            write_new(GENERATION / "runtime.env", runtime_env(self.runtime))
            write_new(GENERATION / "compose.hr-generation.json", canonical(overlay()))
            rendered = self.docker(
                "compose",
                "--project-name",
                "orbbec-agent-platform",
                "--env-file",
                str(GENERATION / "runtime.env"),
                "-f",
                str(RELEASE_ROOT / "deploy/cloud/compose.yaml"),
                "-f",
                str(RELEASE_ROOT / "deploy/cloud/compose.hr-agent.yaml"),
                "-f",
                str(GENERATION / "compose.hr-generation.json"),
                "--profile",
                "hr-agent",
                "config",
                "--format",
                "json",
            )
            self.envs = rendered_envs(json.loads(rendered))
            write_new(GENERATION / "rendered-compose.json", rendered)
            (GENERATION / "secrets").mkdir(mode=0o700)
            for name, value in {**self.config, **self.shared, **self.envs}.items():
                write_new(GENERATION / "secrets" / name, value)
        if op == "copy_volume":
            self.docker(
                "volume",
                "create",
                "--label",
                f"hr.incremental.run={self.run_id}",
                "--label",
                f"hr.incremental.image={IMAGE}",
                VOLUME,
            )
            destination, obj = self.volume(VOLUME)
            if obj.get("Labels", {}).get("hr.incremental.run") != self.run_id or list(
                destination.iterdir()
            ):
                raise InstallError("volume_ownership_invalid")
            os.chmod(destination, 0o700)
            os.chown(destination, 10001, 10001)
            for source in (GENERATION / "secrets").iterdir():
                write_new(destination / source.name, source.read_bytes(), 10001)
        if op == "verify_installed":
            destination, _ = self.volume(VOLUME)
            for name, value in {**self.config, **self.shared, **self.envs}.items():
                if (
                    protected(destination / name, 10001) != value
                    or protected(GENERATION / "secrets" / name, 0) != value
                ):
                    raise InstallError("installed_bytes_mismatch")
            if any(
                protected(self.api / name, 10001) != value
                for name, value in self.shared.items()
            ):
                raise InstallError("shared_secret_drift")
            if tree(OLD_KNOWLEDGE) != self.old_tree:
                raise InstallError("old_knowledge_drift")
            for name, value in {**self.config, **self.shared, **self.old_envs}.items():
                if protected(self.old / name, 10001) != value:
                    raise InstallError("old_secrets_drift")
            script = """import json
from tools.hr_agent.preflight import read_environment_file
from app.hr_agent.config import load_hr_agent_settings
from app.hr_agent.knowledge import KnowledgeReleases
values=[]
for filename in ('api-runtime.env','worker-runtime.env'):
 e=read_environment_file('/run/hr-agent-secrets/'+filename)
 s=load_hr_agent_settings(e)
 k=KnowledgeReleases(s.knowledge_dir).current(); k.check()
 assert s.enabled and s.release_policy['scope']=='public-only'
 values.append((s.configuration_revision,k.release_id))
assert values[0]==values[1]
print(json.dumps(values[0]))
"""
            name = "hr-config-check-" + self.run_id
            if (
                name
                in self.docker("ps", "-a", "--format", "{{.Names}}")
                .decode()
                .splitlines()
            ):
                raise InstallError("check_container_exists")
            # Never start an unreceipted container. A lost create response can
            # only leave a stopped container, recovered by exact name+labels.
            attempted = False

            def owned():
                obj = json.loads(self.docker("inspect", name))[0]
                if (
                    obj["Name"] != "/" + name
                    or obj["Image"] != IMAGE
                    or obj["Config"].get("Labels", {}).get("hr.config-check.run")
                    != self.run_id
                    or not re.fullmatch("[0-9a-f]{64}", obj["Id"])
                ):
                    raise InstallError("check_container_ownership_invalid")
                return obj["Id"]

            try:
                attempted = True
                self.docker(
                    "create",
                    "--name",
                    name,
                    "--label",
                    f"hr.config-check.run={self.run_id}",
                    "--network",
                    "none",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--user",
                    "10001:10001",
                    "--mount",
                    f"type=volume,src={VOLUME},dst=/run/hr-agent-secrets,readonly",
                    "--mount",
                    f"type=bind,src={GENERATION}/knowledge,dst=/data/hr-knowledge,readonly",
                    "--mount",
                    "type=bind,src=/data/orbbec-agent-platform/hr-work,dst=/data/hr-work",
                    "--entrypoint",
                    "python",
                    IMAGE,
                    "-c",
                    script,
                )
                self.container = owned()
                write_new(
                    GENERATION / "check-container.json",
                    canonical(
                        {
                            "id": self.container,
                            "name": name,
                            "run_id": self.run_id,
                            "image": IMAGE,
                        }
                    ),
                )
                result = self.docker("start", "--attach", self.container, timeout=45)
                obj = json.loads(self.docker("inspect", self.container))[0]
                if (
                    obj["State"]["Running"]
                    or obj["State"]["ExitCode"] != 0
                    or json.loads(result) != [self.revision, KNOWLEDGE]
                ):
                    raise InstallError("runtime_identity_mismatch")
            finally:
                if attempted:
                    exact = owned()
                    self.docker("rm", "--force", exact)
                    self.container = None
            return {
                "bytes_equal": True,
                "runtime_loaded": True,
                "personal_materials": False,
            }
        if op == "publish":
            kw["receipt"]["runtime_env_sha256"] = digest(
                (GENERATION / "runtime.env").read_bytes()
            )
            kw["receipt"]["overlay_sha256"] = digest(
                (GENERATION / "compose.hr-generation.json").read_bytes()
            )
            write_new(GENERATION / "prepared.json", canonical(kw["receipt"]))
        if (
            op == "failure_receipt"
            and GENERATION.is_dir()
            and not (GENERATION / "incomplete.json").exists()
        ):
            write_new(
                GENERATION / "incomplete.json",
                canonical(
                    {
                        "status": "incomplete",
                        "run_id": self.run_id,
                        "services_started": False,
                    }
                ),
            )
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute or os.getuid() != 0:
        raise InstallError("explicit_host_execution_required")
    require_binding()
    host = Host(args.inputs)

    def interrupt(signum, frame):
        raise InstallError("interrupted")

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(signum, interrupt)
    with open("/run/lock/hr-incremental-ad5f3cac.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(install(host, args.run_id), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # noqa: BLE001 - interrupts must fail closed without private output
        print('{"status":"failed","detail":"incremental_preparation_failed"}')
        raise SystemExit(1) from None
