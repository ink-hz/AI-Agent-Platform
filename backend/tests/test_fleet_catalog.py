from app.fleet.catalog import AgentCatalog


CURRENT_BOT_IDS = {
    "feishu-default",
    "hr-bot",
    "marketing-prospecting-bot",
    "marketing-inbound-bot",
    "marketing-voice-bot",
    "fae-bot",
    "test-bot",
    "marketing-gtm-bot",
    "marketing-intelligence-bot",
    "codex-assistant",
}


EXPECTED_IDENTITIES = {
    "feishu-default": (
        "Feishu Default",
        "Feishu",
        "FS",
        "承接飞书默认会话与日常协作任务。",
    ),
    "hr-bot": ("HR", "HR", "HR", "处理招聘、人事与员工服务相关工作。"),
    "marketing-prospecting-bot": (
        "Marketing Prospecting",
        "Marketing",
        "PRO",
        "发现、筛选并跟进潜在客户线索。",
    ),
    "marketing-inbound-bot": (
        "Marketing Inbound",
        "Marketing",
        "IN",
        "处理入站线索、内容触达与客户咨询。",
    ),
    "marketing-voice-bot": (
        "Marketing Voice",
        "Marketing",
        "VO",
        "处理语音触达、通话沟通与结果整理。",
    ),
    "fae-bot": ("FAE", "FAE", "FAE", "处理产品咨询、问题诊断与现场应用。"),
    "test-bot": (
        "Test",
        "System",
        "T",
        "用于接口联调、集成测试与运行验证。",
    ),
    "marketing-gtm-bot": (
        "Marketing GTM",
        "Marketing",
        "GTM",
        "负责市场进入策略、节奏规划与执行协同。",
    ),
    "marketing-intelligence-bot": (
        "Marketing Intelligence",
        "Marketing",
        "INT",
        "收集并整理市场动态与竞争信息。",
    ),
    "codex-assistant": (
        "Iris Codex",
        "Personal Workspace",
        "IC",
        "Private Codex workspace for Iris's development and operational work.",
    ),
}


def test_catalog_has_approved_identity_for_all_current_bots():
    catalog = AgentCatalog.default()

    actual = {
        bot_id: (
            catalog.profile(bot_id, bot_id).name,
            catalog.profile(bot_id, bot_id).domain,
            catalog.profile(bot_id, bot_id).glyph,
            catalog.profile(bot_id, bot_id).description,
        )
        for bot_id in CURRENT_BOT_IDS
    }

    assert actual == EXPECTED_IDENTITIES
    assert all("助手" not in identity[0] for identity in actual.values())


def test_catalog_maps_only_confirmed_legacy_aliases():
    catalog = AgentCatalog.default()

    assert catalog.canonical_id("marketing-bot") == "marketing-prospecting-bot"
    assert catalog.canonical_id("pc-bot") is None
    assert catalog.canonical_id("quality-bot") is None
    assert catalog.canonical_id("hr-bot") == "hr-bot"
    assert catalog.canonical_id("new-runtime-bot") == "new-runtime-bot"


def test_unknown_runtime_bot_gets_generic_profile():
    profile = AgentCatalog.default().profile("new-bot", "New Bot")

    assert profile.id == "new-bot"
    assert profile.name == "New Bot"
    assert profile.glyph == "AI"
    assert profile.domain == "MetaBot 实例"
    assert profile.accent == "default"


def test_iris_codex_has_business_visibility_and_release_lifecycle():
    profile = AgentCatalog.default().profile("codex-assistant", "codex-assistant")

    assert profile.accent == "intelligence"
    assert profile.visibility == "business"
    assert profile.live_since == "2026-07-21T18:01:18+08:00"
    assert profile.live_since_basis == "release_artifact"
    assert profile.last_updated_at == "2026-07-21T18:01:18+08:00"
    assert profile.last_updated_basis == "release_artifact"
