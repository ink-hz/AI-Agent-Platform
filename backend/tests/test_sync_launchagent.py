from pathlib import Path
import plistlib


ROOT = Path(__file__).parents[2]


def test_daily_sync_launchagent_contract() -> None:
    with (ROOT / "deploy/com.orbbec.ai-agent-platform-sync.plist").open("rb") as handle:
        plist = plistlib.load(handle)

    assert plist["StartCalendarInterval"] == {"Hour": 3, "Minute": 20}
    assert plist["RunAtLoad"] is False
    assert plist["ProgramArguments"][-1].endswith("deploy/sync-remote-agents")
    assert "KeepAlive" not in plist


def test_installer_targets_only_sync_launchagent() -> None:
    script = (ROOT / "deploy/install-sync-launchagent.sh").read_text(encoding="utf-8")

    assert "com.orbbec.ai-agent-platform-sync" in script
    assert "com.orbbec.ai-agent-platform.plist" not in script
    assert "MetaBot" not in script
