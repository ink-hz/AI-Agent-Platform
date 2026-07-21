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
}


def test_catalog_has_identity_for_all_current_bots():
    catalog = AgentCatalog.default()

    profiles = {catalog.profile(item, item) for item in CURRENT_BOT_IDS}

    assert {profile.id for profile in profiles} == CURRENT_BOT_IDS
    assert all(profile.name != profile.id for profile in profiles)
    assert all(profile.description for profile in profiles)


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
