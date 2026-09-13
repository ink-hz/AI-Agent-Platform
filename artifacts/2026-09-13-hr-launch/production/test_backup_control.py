from __future__ import annotations

from collections import namedtuple
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "backup_control.py"


def load_module():
    spec = importlib.util.spec_from_file_location("backup_control_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BackupHarness:
    def __init__(self, module, root: Path, failure: str | None = None):
        self.module = module
        self.root = root
        self.failure = failure
        self.uid = os.getuid()
        self.private = root / "private"
        self.private.mkdir(mode=0o700)
        self.metadata = root / "metadata"
        self.metadata.mkdir(mode=0o700)
        (self.metadata / module.RELEASE).mkdir(mode=0o700)
        self.backups = root / "backups"
        self.helper = root / "deploy-input-lock.py"
        self.helper.write_bytes(b"fixed helper")
        self.action = self.private / "agent-brain-action.lock"
        self.real_path = Path

    def route_path(self, value):
        if str(value) == "/data/orbbec-agent-platform/release-metadata":
            return self.metadata
        if str(value) == "/data/orbbec-agent-platform/private-backups":
            return self.backups
        return self.real_path(value)

    def fake_run(self, args, *, output=None, input_file=None, timeout=180):
        if args[:2] == ["docker", "inspect"]:
            return (self.module.POSTGRES + "\n").encode()
        if args[0] == "python3":
            if args[2] == "acquire":
                if self.failure == "foreign_owner":
                    (self.action / "owner").write_text("foreign-token\n")
                    raise RuntimeError("injected")
                if self.failure == "signal":
                    self.module.interrupted(signal.SIGTERM, None)
            return b""
        command = " ".join(args)
        if "select count(*) from pg_stat_activity" in command:
            return b"0\n"
        if output is not None:
            if "select json_agg" in command:
                output.write(
                    json.dumps(
                        [{"version": number, "sha256": "0" * 64} for number in range(1, 96)]
                    ).encode()
                )
            else:
                output.write(b"private backup fixture\n")
        return b""

    def patches(self):
        usage = namedtuple("usage", "total used free")(100, 1, 99 * 1024**3)
        self.module.PRIVATE = self.private
        self.module.HELPER = self.helper
        self.module.HELPER_SHA = self.module.hashlib.sha256(self.helper.read_bytes()).hexdigest()

        def protected_for_fixture(path, mode, directory=False):
            info = path.lstat()
            if path.is_symlink() or info.st_uid != self.uid or stat.S_IMODE(info.st_mode) != mode:
                raise RuntimeError("fixture_guard_failed")
            expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
            if not expected:
                raise RuntimeError("fixture_guard_failed")

        return (
            mock.patch.object(self.module, "Path", side_effect=self.route_path),
            mock.patch.object(self.module, "run", side_effect=self.fake_run),
            mock.patch.object(self.module.os, "getuid", return_value=0),
            mock.patch.object(self.module.shutil, "disk_usage", return_value=usage),
            mock.patch.object(self.module, "protected", side_effect=protected_for_fixture),
        )

    def receipt(self) -> dict:
        receipts = list((self.metadata / self.module.RELEASE).glob("backup-*.json"))
        if len(receipts) != 1:
            raise AssertionError(receipts)
        return json.loads(receipts[0].read_text())


class BackupControlSafetyTests(unittest.TestCase):
    def test_guards_remain_active_under_python_optimized_mode(self) -> None:
        program = f"""
import importlib.util
spec=importlib.util.spec_from_file_location('backup', {str(SCRIPT)!r})
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
try:
    module.require(False)
except RuntimeError:
    print('GUARD_ACTIVE')
else:
    raise SystemExit('guard was optimized away')
"""
        result = subprocess.run(
            ["python3", "-O", "-c", program], text=True, capture_output=True
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("GUARD_ACTIVE", result.stdout.strip())

    def test_foreign_owner_token_is_never_deleted(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            harness = BackupHarness(module, Path(temporary), "foreign_owner")
            previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)}
            with redirect_stdout(io.StringIO()):
                with harness.patches()[0], harness.patches()[1], harness.patches()[2], harness.patches()[3], harness.patches()[4]:
                    self.assertEqual(1, module.main())
            for number, handler in previous.items():
                signal.signal(number, handler)
            self.assertTrue(harness.action.is_dir())
            self.assertEqual("foreign-token\n", (harness.action / "owner").read_text())
            receipt = harness.receipt()
            self.assertFalse(receipt["cleanup_verified"])
            self.assertEqual("failed", receipt["status"])

    def test_owner_creation_failure_removes_only_empty_self_created_lock(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            harness = BackupHarness(module, Path(temporary))
            original_open = io.open

            def failing_open(file, mode="r", *args, **kwargs):
                if Path(file).name == "owner" and mode == "x":
                    raise OSError("injected owner write failure")
                return original_open(file, mode, *args, **kwargs)

            previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)}
            with mock.patch("io.open", side_effect=failing_open):
                with redirect_stdout(io.StringIO()):
                    with harness.patches()[0], harness.patches()[1], harness.patches()[2], harness.patches()[3], harness.patches()[4]:
                        self.assertEqual(1, module.main())
            for number, handler in previous.items():
                signal.signal(number, handler)
            self.assertFalse(harness.action.exists())
            self.assertTrue(harness.receipt()["cleanup_verified"])

    def test_sigterm_path_runs_finally_and_persists_failed_receipt(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            harness = BackupHarness(module, Path(temporary), "signal")
            previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)}
            with redirect_stdout(io.StringIO()):
                with harness.patches()[0], harness.patches()[1], harness.patches()[2], harness.patches()[3], harness.patches()[4]:
                    self.assertEqual(1, module.main())
            for number, handler in previous.items():
                signal.signal(number, handler)
            receipt = harness.receipt()
            self.assertEqual("failed", receipt["status"])
            self.assertEqual("BackupInterrupted", receipt["failure_type"])
            self.assertTrue(receipt["cleanup_verified"])
            self.assertTrue(receipt["backup_sessions_zero"])
            self.assertFalse(harness.action.exists())


if __name__ == "__main__":
    unittest.main()
