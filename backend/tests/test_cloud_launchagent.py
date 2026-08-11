from pathlib import Path
import plistlib


ROOT = Path(__file__).parents[2]
TEMPLATE = ROOT / "deploy" / "com.orbbec.ai-agent-platform-cloud-sync.plist.template"
INSTALLER = ROOT / "deploy" / "install-cloud-sync-launchagent.sh"


def test_launchagent_runs_every_five_minutes_with_absolute_placeholders():
    raw = TEMPLATE.read_text(encoding="utf-8")
    rendered = (
        raw.replace("__PUSH_SCRIPT__", "/opt/platform/push-replica.sh")
        .replace("__CONFIG_PATH__", "/private/platform/cloud-sync.conf")
        .replace("__STDOUT_LOG__", "/private/platform/log/cloud-sync.out.log")
        .replace("__STDERR_LOG__", "/private/platform/log/cloud-sync.err.log")
    )
    value = plistlib.loads(rendered.encode("utf-8"))

    assert value["StartInterval"] == 300
    assert value["RunAtLoad"] is True
    assert all(argument.startswith("/") for argument in value["ProgramArguments"])
    assert value["StandardOutPath"] != value["StandardErrorPath"]
    assert "EnvironmentVariables" not in value
    assert "secret" not in raw.lower()


def test_installer_is_atomic_linted_and_only_bootstraps_cloud_sync():
    script = INSTALLER.read_text(encoding="utf-8")

    assert "plutil -lint" in script
    assert "mktemp" in script
    assert "launchctl bootstrap" in script
    assert "com.orbbec.ai-agent-platform-cloud-sync" in script
    assert "metabot" not in script.lower()
    assert "fae" not in script.lower()
    assert "platform restart" not in script.lower()
    assert "security " not in script
