from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "provision_hr_release.py"


def load_module():
    spec = importlib.util.spec_from_file_location("provision_hr_release", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProvisionHrReleaseContractTests(unittest.TestCase):
    def test_fixed_identity_and_facts_do_not_disclose_credential_fingerprint(self) -> None:
        module = load_module()
        self.assertEqual(
            "ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7", module.RELEASE_SHA
        )
        self.assertEqual(
            "sha256:2631b6a5090d49a51d187b88c6c38acc0da867ebc985c44cf79bd2db338d2e59",
            module.IMAGE_ID,
        )
        facts = module.facts()
        self.assertEqual(module.CONFIGURATION_REVISION, facts["configuration_revision"])
        rendered = json.dumps(facts, sort_keys=True)
        self.assertNotIn("credential_sha", rendered)
        self.assertNotIn("credential_value", rendered)
        self.assertFalse(facts["starts_services"])
        self.assertFalse(facts["runs_migrations"])

    def test_local_inputs_are_exact_protected_regular_files(self) -> None:
        module = load_module()
        info = module.validate_local_inputs()
        self.assertEqual(set(module.PUBLIC_CONFIG_SHA256), set(info["config_sha256"]))
        self.assertNotIn("hr-provider-credential", info["config_sha256"])
        self.assertEqual(module.KNOWLEDGE_SHA256, info["knowledge_sha256"])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in module.CONFIG_FILES:
                (root / name).write_bytes(b"x")
                (root / name).chmod(0o600)
            (root / "extra").write_bytes(b"x")
            (root / "extra").chmod(0o600)
            with self.assertRaises(ValueError):
                module.validate_config_directory(root)
            (root / "extra").unlink()
            (root / "hr-provider-credential").unlink()
            (root / "hr-provider-credential").symlink_to(root / "hr-budget-profile.json")
            with self.assertRaises(ValueError):
                module.validate_config_directory(root)

    def test_knowledge_archive_has_only_safe_expected_release(self) -> None:
        module = load_module()
        info = module.validate_knowledge_archive(module.KNOWLEDGE_ARCHIVE)
        self.assertEqual(module.KNOWLEDGE_RELEASE, info["release_id"])
        self.assertEqual(85, info["intelligence_markdown_count"])
        self.assertEqual(9, info["method_resource_count"])
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "bad.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                member = tarfile.TarInfo("../escape")
                member.size = 1
                import io

                archive.addfile(member, io.BytesIO(b"x"))
            with self.assertRaises(ValueError):
                module.validate_knowledge_archive(archive_path, verify_hash=False)

    def test_remote_job_is_provision_only_and_uses_fixed_sources(self) -> None:
        module = load_module()
        job = module.render_remote_job(
            deployment_id="1" * 32,
            action_token="11111111-1111-4111-8111-111111111111",
        )
        self.assertIn(module.RELEASE_SHA, job)
        self.assertIn(module.IMAGE_ID, job)
        self.assertIn(module.EXISTING_API_ID, job)
        self.assertIn("orbbec-agent-platform-api-secrets", job)
        self.assertIn("orbbec-agent-platform-hr-agent-secrets", job)
        self.assertIn("/data/orbbec-agent-platform/hr-work", job)
        self.assertIn("/data/orbbec-agent-platform/hr-knowledge", job)
        for forbidden in (
            "docker compose up",
            "docker start",
            "docker stop",
            "docker restart",
            "bootstrap-control-db",
            "migrate-hr-agent",
            "app.control_plane.migrate",
            "/opt/orbbec-agent-platform/current",
        ):
            self.assertNotIn(forbidden, job)

    def test_remote_job_renders_env_in_private_files_and_preflights_offline(self) -> None:
        module = load_module()
        job = module.render_remote_job(
            deployment_id="1" * 32,
            action_token="11111111-1111-4111-8111-111111111111",
        )
        self.assertIn("--network none", job)
        self.assertIn("--read-only", job)
        self.assertIn("--cap-drop ALL", job)
        self.assertIn("--security-opt no-new-privileges:true", job)
        self.assertIn("database_not_checked", job)
        self.assertIn("config --format json", job)
        self.assertIn("api-runtime.env", job)
        self.assertIn("worker-runtime.env", job)
        self.assertIn("python -m tools.hr_agent.preflight", job)
        self.assertNotIn("python tools/hr_agent/preflight.py", job)
        self.assertIn("runtime_match", job)
        self.assertNotIn("item.get('ready')", job)
        self.assertIn("metadata_root/preflight.json", job)
        self.assertNotIn("cat $platform_env", job)
        self.assertNotIn("source $platform_env", job)

    def test_remote_job_preserves_old_knowledge_and_cleanup_is_owned(self) -> None:
        module = load_module()
        job = module.render_remote_job(
            deployment_id="1" * 32,
            action_token="11111111-1111-4111-8111-111111111111",
        )
        self.assertIn("knowledge-before.json", job)
        self.assertIn("knowledge_after", job)
        self.assertIn("before.items()", job)
        self.assertIn("release_created=0", job)
        self.assertIn("current_created=0", job)
        self.assertIn("private_created=0", job)
        self.assertIn("work_created=0", job)
        self.assertIn("volume_created=0", job)
        self.assertIn("current_temp_attempted=0", job)
        self.assertIn("knowledge_root_metadata", job)
        self.assertIn("if value['type'] == 'directory'", job)
        self.assertNotIn("chown -R 10001:10001 \"$knowledge_root\"", job)
        self.assertNotIn("chmod -R", job)

    def test_remote_job_uses_both_locks_and_verifies_release_manifest(self) -> None:
        module = load_module()
        job = module.render_remote_job(
            deployment_id="1" * 32,
            action_token="11111111-1111-4111-8111-111111111111",
        )
        self.assertIn(module.DEPLOY_LOCK_SHA256, job)
        self.assertIn('"$deploy_lock" acquire "$release_sha" "$deployment_id"', job)
        self.assertIn('"$deploy_lock" validate "$release_sha" "$deployment_id"', job)
        self.assertIn('"$deploy_lock" release "$release_sha" "$deployment_id"', job)
        self.assertIn("sha256sum --strict -c MANIFEST.sha256", job)
        self.assertIn("trap 'on_signal 3' QUIT", job)

    def test_fixed_deploy_lock_rejects_wrong_release_identity(self) -> None:
        module = load_module()
        source = subprocess.run(
            ["git", "show", f"{module.RELEASE_SHA}:deploy/cloud/deploy-input-lock.py"],
            cwd=module.WORKTREE,
            check=True,
            capture_output=True,
        ).stdout
        self.assertEqual(module.DEPLOY_LOCK_SHA256, __import__("hashlib").sha256(source).hexdigest())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / "private"
            private.mkdir(mode=0o700)
            helper_path = root / "deploy_input_lock.py"
            helper_path.write_bytes(source)
            spec = importlib.util.spec_from_file_location("fixed_deploy_input_lock", helper_path)
            assert spec is not None and spec.loader is not None
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            helper.PRIVATE_ROOT = private
            helper.LOCK_ROOT = private / "deploy-input.lock"
            helper.STATE = helper.LOCK_ROOT / "owner.json"
            helper.STATE_PART = helper.LOCK_ROOT / "owner.json.part"
            helper.TRANSACTION_LOCK = private / "deploy-input.transaction.lock"
            helper.PROVIDER_EVIDENCE = private / "agent-brain-v2/provider-evidence.json"
            helper.PROVIDER_EVIDENCE_DIGEST = private / "agent-brain-v2/provider-evidence.sha256"
            release = "a" * 40
            deployment = "b" * 32
            helper._acquire(release, deployment)
            with self.assertRaises(helper.DeployInputError):
                helper._release(release, "c" * 32)
            self.assertTrue(helper.LOCK_ROOT.is_dir())
            helper._validate(release, deployment)
            helper._release(release, deployment)
            self.assertFalse(helper.LOCK_ROOT.exists())

    def test_hr_volume_contains_twelve_exact_files(self) -> None:
        module = load_module()
        job = module.render_remote_job(
            deployment_id="1" * 32,
            action_token="11111111-1111-4111-8111-111111111111",
        )
        self.assertIn("api-runtime.env", job)
        self.assertIn("worker-runtime.env", job)
        self.assertIn("-eq 12", job)
        expected = (
            "attachment-s3-access-key attachment-s3-secret-key content-encryption-keyring "
            "control-database-url hr-budget-profile.json hr-content-keyring.json "
            "hr-diagnostic-profile.json hr-provider-credential hr-provider-profile.json "
            "hr-release-policy.json api-runtime.env worker-runtime.env"
        )
        for name in expected.split():
            self.assertIn(name, job)

    def test_wrong_action_token_cannot_delete_lock(self) -> None:
        module = load_module()
        job = module.render_remote_job(
            deployment_id="1" * 32,
            action_token="11111111-1111-4111-8111-111111111111",
        )
        match = re.search(
            r"(release_action_lock\(\) \{.*?^\})", job, re.DOTALL | re.MULTILINE
        )
        assert match is not None
        function_under_test = match.group(1).replace(
            '  [[ "$(/usr/bin/stat -c \'%a %U\' "$action_lock/owner")" == \'600 root\' ]] || return 1\n',
            "  true\n",
        )
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "agent-brain-action.lock"
            lock.mkdir(mode=0o700)
            (lock / "owner").write_text("wrong-token\n", encoding="ascii")
            harness = (
                "set +e\n"
                f"action_lock={lock}\n"
                "action_owned=1\n"
                "action_token=11111111-1111-4111-8111-111111111111\n"
                f"{function_under_test}\n"
                "release_action_lock\n"
                "exit $?\n"
            )
            result = subprocess.run(
                ["/bin/bash"], input=harness, text=True, capture_output=True
            )
            self.assertNotEqual(0, result.returncode)
            self.assertTrue(lock.is_dir())

    def test_rendered_bash_and_python_heredocs_compile(self) -> None:
        module = load_module()
        job = module.render_remote_job(
            deployment_id="1" * 32,
            action_token="11111111-1111-4111-8111-111111111111",
        )
        result = subprocess.run(
            ["/bin/bash", "-n"], input=job, text=True, capture_output=True
        )
        self.assertEqual("", result.stderr)
        self.assertEqual(0, result.returncode)
        programs = re.findall(r"<<'PY'\n(.*?)\nPY(?:\n|$)", job, re.DOTALL)
        self.assertGreaterEqual(len(programs), 3)
        for index, program in enumerate(programs):
            compile(program, f"remote-heredoc-{index}.py", "exec")

    def test_preflight_validator_accepts_real_shape_and_rejects_identity_drift(self) -> None:
        module = load_module()
        job = module.render_remote_job(
            deployment_id="1" * 32,
            action_token="11111111-1111-4111-8111-111111111111",
        )
        programs = re.findall(r"<<'PY'\n(.*?)\nPY(?:\n|$)", job, re.DOTALL)
        validator = next(item for item in programs if "preflight_blockers_invalid" in item)
        identity = {
            "d7_product_approved": True,
            "configuration_fingerprint": module.CONFIGURATION_REVISION,
            "hr_content_keyring_fingerprint": "keyring",
            "profile_fingerprints": {"provider": "provider", "budget": "budget"},
            "provider": {"id": "approved", "revision": "r1"},
            "budget": {"fingerprint": "budget"},
            "knowledge": {"release_id": module.KNOWLEDGE_RELEASE, "manifest_fingerprint": "manifest"},
            "attachments": {"enabled": True, "wiring_present": True, "fingerprint": "attachments"},
        }
        report = {
            "ok": False,
            "blockers": ["database_not_checked"],
            "runtime_match": True,
            "api": identity,
            "worker": json.loads(json.dumps(identity)),
            "database": {"checked": False},
            "limitations": {"model_or_network_called": False},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "preflight.json"

            def run(value: dict) -> subprocess.CompletedProcess:
                path.write_text(json.dumps(value), encoding="utf-8")
                return subprocess.run(
                    ["python3", "-c", validator, str(path), module.CONFIGURATION_REVISION,
                     module.KNOWLEDGE_RELEASE],
                    text=True,
                    capture_output=True,
                )

            self.assertEqual(0, run(report).returncode)
            drifted = json.loads(json.dumps(report))
            drifted["worker"]["attachments"]["fingerprint"] = "different"
            self.assertNotEqual(0, run(drifted).returncode)
            fake_ready = {"ok": False, "blockers": ["database_not_checked"],
                          "runtime_match": True, "api": {"ready": True},
                          "worker": {"ready": True}, "database": {"checked": False},
                          "limitations": {"model_or_network_called": False}}
            self.assertNotEqual(0, run(fake_ready).returncode)

    def test_old_knowledge_directory_growth_is_allowed_but_file_mutation_is_rejected(self) -> None:
        module = load_module()
        job = module.render_remote_job(
            deployment_id="1" * 32,
            action_token="11111111-1111-4111-8111-111111111111",
        )
        programs = re.findall(r"<<'PY'\n(.*?)\nPY(?:\n|$)", job, re.DOTALL)
        snapshot = next(item for item in programs if "knowledge_root_metadata" in item and "payload =" in item)
        compare = next(item for item in programs if "old_knowledge_changed" in item)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "knowledge"
            legacy = root / "legacy"
            legacy.mkdir(parents=True)
            old_file = legacy / "old.md"
            old_file.write_text("preserve", encoding="utf-8")
            before = Path(temporary) / "before.json"
            subprocess.run(["python3", "-c", snapshot, str(root), str(before)], check=True)
            published = root / "releases" / module.KNOWLEDGE_RELEASE
            published.mkdir(parents=True)
            (published / "manifest.json").write_text("{}", encoding="utf-8")
            (root / "current.json").write_text("{}", encoding="utf-8")
            result = Path(temporary) / "result.json"
            args = [str(root), str(before), str(result), module.RELEASE_SHA, module.IMAGE_ID,
                    module.CONFIGURATION_REVISION, module.KNOWLEDGE_RELEASE]
            accepted = subprocess.run(["python3", "-c", compare, *args], text=True, capture_output=True)
            self.assertEqual("", accepted.stderr)
            self.assertEqual(0, accepted.returncode)
            old_file.write_text("changed", encoding="utf-8")
            rejected = subprocess.run(
                ["python3", "-c", compare, *[str(root), str(before), str(Path(temporary) / "result-2.json"),
                 module.RELEASE_SHA, module.IMAGE_ID, module.CONFIGURATION_REVISION,
                 module.KNOWLEDGE_RELEASE]],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(0, rejected.returncode)
            self.assertIn("old_knowledge_changed", rejected.stderr)

    def test_start_requires_explicit_execute(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            "/usr/bin/env -i \\\n  PATH=/usr/bin:/bin LANG=C.UTF-8 DOCKER_HOST=unix:///var/run/docker.sock /bin/bash -c",
            source,
        )
        result = subprocess.run(
            ["python3", str(SCRIPT), "start"], text=True, capture_output=True
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--execute", result.stderr)


if __name__ == "__main__":
    unittest.main()
