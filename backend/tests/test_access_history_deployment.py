from pathlib import Path


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "deploy" / "cloud"


def test_cross_workspace_probe_uses_only_canonical_pages_and_checks_external_fae() -> None:
    probe = (CLOUD / "access-history-probe.mjs").read_text(encoding="utf-8")
    for path in (
        "https://agent.orbbec.com.cn/",
        "https://agent.orbbec.com.cn/office/?view=services",
        "https://agent.orbbec.com.cn/fae/",
        "https://agent.orbbec.com.cn/voc/",
        "https://agent.orbbec.com.cn/hr/",
        "https://agent.orbbec.com.cn/marketing/prospecting",
        "https://agent.orbbec.com.cn/admin/",
        "https://fae.orbbec.com.cn/",
    ):
        assert path in probe
    for page_key in (
        "platform.brain", "office.services", "fae.workspace", "voc.workspace",
        "hr.workspace", "marketing.workspace", "admin.overview",
    ):
        assert page_key in probe
    assert "external FAE produced a Platform access event" in probe
    assert "document.cookie" not in probe


def test_cloud_acceptance_requires_access_history_schema_api_and_browser_probe() -> None:
    script = (CLOUD / "accept.sh").read_text(encoding="utf-8")
    for marker in (
        "ACCESS_HISTORY_MIGRATION=applied",
        "platform_control.user_access_events",
        "platform_control.append_page_view_v65",
        "platform_control.read_user_access_events_v67",
        "platform_control.read_access_subjects_v67",
        "access-history-probe.mjs",
        "/api/v1/manage/access-events",
        "/api/v1/manage/access-subjects",
        "ACCESS_HISTORY_BROWSER_OK",
    ):
        assert marker in script
    assert '"$status_code" == "403"' in script

    replica_acceptance = (CLOUD / "acceptance.sh").read_text(encoding="utf-8")
    assert "where version=67" in replica_acceptance
    assert "platform_control.user_access_events" in replica_acceptance
    assert "/api/v1/manage/access-events" in replica_acceptance
    assert "/api/v1/manage/access-subjects" in replica_acceptance
