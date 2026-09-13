from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "stage_build_v2.py"


def load_module():
    spec = importlib.util.spec_from_file_location("stage_build_v2", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StageBuildV2ContractTests(unittest.TestCase):
    def test_archive_is_exact_fixed_git_object_plus_manifest(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            info = module.build_source_package(Path(temporary))
            with tarfile.open(info.archive_path, "r:gz") as archive:
                members = archive.getmembers()
                names = {member.name for member in members}
                self.assertIn("source/MANIFEST.sha256", names)
                self.assertIn("source/deploy/cloud/Dockerfile", names)
                self.assertFalse(any(".git" in Path(name).parts for name in names))
                self.assertNotIn(
                    "source/artifacts/2026-09-13-hr-launch/production/stage_build_v2.py",
                    names,
                )
                self.assertFalse(any(name.endswith(".env.hr") for name in names))
                self.assertFalse(any(member.issym() or member.islnk() for member in members))
                dockerfile = archive.extractfile("source/deploy/cloud/Dockerfile")
                assert dockerfile is not None
                expected = subprocess.run(
                    [
                        "git",
                        "show",
                        f"{module.RELEASE_SHA}:deploy/cloud/Dockerfile",
                    ],
                    cwd=module.WORKTREE,
                    check=True,
                    stdout=subprocess.PIPE,
                ).stdout
                self.assertEqual(expected, dockerfile.read())
            self.assertRegex(info.archive_sha256, r"^[0-9a-f]{64}$")
            self.assertRegex(info.manifest_sha256, r"^[0-9a-f]{64}$")

    def test_member_validator_rejects_links_and_traversal(self) -> None:
        module = load_module()
        good = tarfile.TarInfo("source/backend/app/main.py")
        good.type = tarfile.REGTYPE
        module.validate_archive_members([good])
        for bad in (
            tarfile.TarInfo("../escape"),
            tarfile.TarInfo("source/../../escape"),
            tarfile.TarInfo("/source/absolute"),
            tarfile.TarInfo("wrong-prefix/file"),
        ):
            bad.type = tarfile.REGTYPE
            with self.assertRaises(ValueError):
                module.validate_archive_members([bad])
        symbolic = tarfile.TarInfo("source/link")
        symbolic.type = tarfile.SYMTYPE
        symbolic.linkname = "backend"
        with self.assertRaises(ValueError):
            module.validate_archive_members([symbolic])
        hard = tarfile.TarInfo("source/hard")
        hard.type = tarfile.LNKTYPE
        hard.linkname = "source/backend/app/main.py"
        with self.assertRaises(ValueError):
            module.validate_archive_members([hard])

    def test_remote_job_is_build_only_and_uses_fixed_private_input(self) -> None:
        module = load_module()
        deployment_id = "1" * 32
        action_token = "11111111-1111-4111-8111-111111111111"
        job = module.render_remote_job(
            deployment_id=deployment_id,
            action_token=action_token,
            archive_sha256="2" * 64,
            manifest_sha256="3" * 64,
            source_tree="4" * 40,
        )
        expected_private = (
            "/opt/orbbec-agent-platform/private/stage-build-inputs/"
            f"{module.RELEASE_SHA}-{deployment_id}/source.tar.gz"
        )
        self.assertIn(expected_private, job)
        self.assertIn(
            f"docker build --pull --build-arg RELEASE_SHA={module.RELEASE_SHA}",
            job,
        )
        self.assertIn('"$deploy_lock" validate', job)
        self.assertIn('"$deploy_lock" release', job)
        for forbidden in (
            "docker stop",
            "docker restart",
            "docker compose up",
            "bootstrap-control-db",
            "migrate-hr-agent",
            "/opt/orbbec-agent-platform/current",
        ):
            self.assertNotIn(forbidden, job)

    def test_remote_python_heredocs_compile(self) -> None:
        module = load_module()
        job = module.render_remote_job(
            deployment_id="1" * 32,
            action_token="11111111-1111-4111-8111-111111111111",
            archive_sha256="2" * 64,
            manifest_sha256="3" * 64,
            source_tree="4" * 40,
        )
        programs = re.findall(r"<<'PY'\n(.*?)\nPY(?:\n|$)", job, re.DOTALL)
        self.assertEqual(2, len(programs))
        for index, program in enumerate(programs):
            compile(program, f"remote-heredoc-{index}.py", "exec")

    def test_wrong_action_token_cannot_move_or_delete_lock(self) -> None:
        module = load_module()
        job = module.render_remote_job(
            deployment_id="1" * 32,
            action_token="11111111-1111-4111-8111-111111111111",
            archive_sha256="2" * 64,
            manifest_sha256="3" * 64,
            source_tree="4" * 40,
        )
        match = re.search(
            r"(release_action_lock\(\) \{.*?^\})", job, re.DOTALL | re.MULTILINE
        )
        assert match is not None
        function_under_test = match.group(1).replace(
            '  [[ "$(/usr/bin/stat -c \'%a %U\' "$action_lock/owner")" == \'600 root\' ]] || return 1\n',
            "  true  # GNU stat metadata guard is separately asserted in source\n",
        )
        self.assertIn("GNU stat metadata guard", function_under_test)
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "agent-brain-action.lock"
            lock.mkdir(mode=0o700)
            (lock / "owner").write_text(
                "22222222-2222-4222-8222-222222222222\n", encoding="ascii"
            )
            harness = (
                "set +e\n"
                f"action_lock={lock}\n"
                "action_token=11111111-1111-4111-8111-111111111111\n"
                f"{function_under_test}\n"
                "release_action_lock\n"
                "exit $?\n"
            )
            self.assertIn(
                '[[ "$(/bin/cat "$action_lock/owner")" == "$action_token" ]] || return 1',
                match.group(1),
            )
            result = subprocess.run(
                ["/bin/bash"],
                input=harness,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertEqual("", result.stderr)
            self.assertTrue(lock.is_dir())
            self.assertEqual(
                "22222222-2222-4222-8222-222222222222\n",
                (lock / "owner").read_text(encoding="ascii"),
            )

    def test_cleanup_only_removes_stage_created_by_job(self) -> None:
        module = load_module()
        job = module.render_remote_job(
            deployment_id="1" * 32,
            action_token="11111111-1111-4111-8111-111111111111",
            archive_sha256="2" * 64,
            manifest_sha256="3" * 64,
            source_tree="4" * 40,
        )
        self.assertIn("stage_created=0", job)
        self.assertIn(
            'if [[ "$stage_created" == 1 && -d "$stage_root"', job
        )
        self.assertIn('stage_created=1', job)

    def test_start_requires_explicit_execute_flag(self) -> None:
        result = subprocess.run(
            ["python3", str(SCRIPT), "start"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--execute", result.stderr)


if __name__ == "__main__":
    unittest.main()
