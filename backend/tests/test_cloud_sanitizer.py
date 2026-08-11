from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from app.cloud_replica.models import RawAttachment, RawSession, RawTurn
from app.cloud_replica.sanitize import (
    OMITTED_TEXT,
    SanitizationPolicy,
    sanitize_session,
    sanitize_text,
)


@pytest.fixture
def policy() -> SanitizationPolicy:
    return SanitizationPolicy(
        version="test-v1",
        customers=("客户甲集团", "客户甲"),
        candidates=("张候选人",),
        projects=("项目鹰",),
        products=("秘密型号X9",),
        addresses=("深圳市南山区测试路88号",),
    )


@pytest.mark.parametrize(
    ("canary", "expected"),
    [
        ("13800138000", "[电话]"),
        ("alice@example.com", "[邮箱]"),
        ("11010519491231002X", "[证件]"),
        ("深圳市南山区测试路88号", "[地址1]"),
        ("Bearer abcdefghijklmnopqrstuvwxyz", "[凭证]"),
        ("AKIAIOSFODNN7EXAMPLE", "[凭证]"),
        ("/Users/neo/secret/customer.md", "[路径]"),
        ("/etc/orbbec/private.conf", "[路径]"),
        ("https://example.com/a?X-Amz-Signature=secret", "[链接1]"),
        ("on_27882925f0e4f159846581dd8144ad63", "[用户标识]"),
        ("客户甲集团", "[客户1]"),
        ("张候选人", "[候选人1]"),
        ("项目鹰", "[项目1]"),
        ("秘密型号X9", "[产品1]"),
    ],
)
def test_deterministic_canaries_never_survive(policy, canary, expected):
    result = sanitize_text(f"前缀 {canary} 后缀", policy, "session-1")

    assert canary not in result.text
    assert expected in result.text
    assert result.safe is True


def test_placeholders_are_stable_and_longest_alias_wins(policy):
    result = sanitize_text(
        "客户甲集团询问项目鹰，客户甲集团要求附件报价",
        policy,
        "session-1",
    )

    assert result.text == "[客户1]询问[项目1]，[客户1]要求[附件1]报价"
    assert result.safe is True


def test_post_detector_can_only_force_omission(policy):
    result = sanitize_text(
        "未覆盖的凭证 ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
        policy,
        "session-1",
    )

    assert result.text == OMITTED_TEXT
    assert result.safe is False


def test_private_dictionary_requires_private_owned_regular_file(tmp_path):
    path = tmp_path / "sensitive.yaml"
    path.write_text("customers:\n  - 客户甲\n", encoding="utf-8")
    path.chmod(0o600)

    policy = SanitizationPolicy.from_private_file(path, version="v2")

    assert policy.customers == ("客户甲",)

    path.chmod(0o644)
    with pytest.raises(RuntimeError, match="0600"):
        SanitizationPolicy.from_private_file(path)


def test_private_dictionary_rejects_relative_path(monkeypatch, tmp_path):
    path = tmp_path / "sensitive.yaml"
    path.write_text("customers: []\n", encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="absolute"):
        SanitizationPolicy.from_private_file(Path("sensitive.yaml"))


def test_private_dictionary_rejects_symlink(tmp_path):
    target = tmp_path / "target.yaml"
    target.write_text("customers: []\n", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "link.yaml"
    link.symlink_to(target)

    with pytest.raises(RuntimeError, match="regular"):
        SanitizationPolicy.from_private_file(link)


def test_sanitize_session_exports_only_allowlisted_safe_fields(policy):
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    raw = RawSession(
        session_key="raw-session-id",
        agent_id="hr-bot",
        source_kind="metabot",
        channel="feishu",
        title="客户甲集团招聘项目鹰",
        user_identity="on_27882925f0e4f159846581dd8144ad63",
        primary_sender_name="洛奇",
        primary_sender_department="市场部",
        created_at=now,
        last_active_at=now,
        turns=(
            RawTurn(
                turn_key="raw-turn-id",
                turn_index=1,
                question="张候选人的邮箱是 alice@example.com",
                answer="请查附件 customer-secret.pdf",
                created_at=now,
                outcome="success",
                fallback_used=False,
                duration_ms=1200,
                attachments=(
                    RawAttachment(
                        attachment_id="raw-attachment-id",
                        direction="generated",
                        display_name="customer-secret.pdf",
                        mime_type="application/pdf",
                        size_bytes=456789,
                        received_or_generated_at=now,
                        archive_status="archived",
                        delivery_status="delivered",
                    ),
                ),
                sources=({"path": "/Users/neo/private.md"},),
                details={"system_prompt": "secret"},
            ),
        ),
        details={"provider_chat_id": "oc_secret"},
    )

    result = sanitize_session(raw, policy)
    serialized = json.dumps(asdict(result), ensure_ascii=False, default=str)

    for forbidden in (
        "raw-session-id",
        "raw-turn-id",
        "raw-attachment-id",
        "on_27882925f0e4f159846581dd8144ad63",
        "customer-secret.pdf",
        "/Users/neo/private.md",
        "system_prompt",
        "provider_chat_id",
        "alice@example.com",
        "客户甲集团",
        "张候选人",
    ):
        assert forbidden not in serialized
    assert result.title.text == "[客户1]招聘[项目1]"
    assert result.turns[0].attachments[0].display_label == "附件 1"
    assert result.turns[0].attachments[0].size_bucket == "100 KiB–1 MiB"
    assert result.primary_sender_name == "洛奇"
    assert result.primary_sender_department == "市场部"


def test_unsafe_turn_omits_both_message_bodies(policy):
    now = datetime(2026, 8, 11, tzinfo=UTC)
    raw = RawSession(
        session_key="s1",
        agent_id="hr-bot",
        source_kind="metabot",
        channel="feishu",
        title=None,
        user_identity="provider-user",
        primary_sender_name="洛奇",
        primary_sender_department=None,
        created_at=now,
        last_active_at=now,
        turns=(
            RawTurn(
                turn_key="t1",
                turn_index=1,
                question="ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
                answer="本来安全的回答",
                created_at=now,
            ),
        ),
    )

    result = sanitize_session(raw, policy)

    assert result.turns[0].question.text == OMITTED_TEXT
    assert result.turns[0].answer.text == OMITTED_TEXT
    assert result.turns[0].question.safe is False
    assert result.turns[0].answer.safe is False
