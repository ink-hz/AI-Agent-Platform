from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.cloud_replica.models import (
    OperationEventProjection,
    RawAttachment,
    RawSession,
    RawTurn,
    ReviewIssueProjection,
    ReviewInboxProjection,
)
from app.cloud_replica.sanitize import (
    SanitizationPolicy,
    sanitize_management_projection,
    sanitize_session,
    sanitize_text,
)


@pytest.fixture
def policy() -> SanitizationPolicy:
    # Dictionary groups are retired. They are still populated here to prove that
    # a stale dictionary can no longer rewrite business text.
    return SanitizationPolicy(
        version="test-v1",
        customers=("客户甲集团", "客户甲"),
        candidates=("张候选人", "确认", "数据"),
        projects=("项目鹰",),
        products=("秘密型号X9",),
        addresses=("深圳市南山区测试路88号",),
    )


@pytest.mark.parametrize(
    "credential",
    [
        "Bearer abcdefghijklmnopqrstuvwxyz",
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
        "password=Sup3rSecret",
        "api_key: sk-abcdef123456",
    ],
)
def test_live_credentials_never_reach_the_replica(policy, credential):
    result = sanitize_text(f"前缀 {credential} 后缀", policy, "session-1")

    assert credential not in result.text
    assert "[凭证]" in result.text
    assert result.text.startswith("前缀 ")
    assert result.text.endswith(" 后缀")
    assert result.safe is True


@pytest.mark.parametrize(
    "content",
    [
        # The defect this contract exists to prevent: a dictionary entry such as
        # 「确认」 used to rewrite ordinary prose into "[候选人33]".
        "您说的「需要」我想确认一下具体是哪一项",
        "徐良的简历数据已经整理好了",
        "客户甲集团询问项目鹰的秘密型号X9",
        "联系方式 13800138000，邮箱 alice@example.com",
        "身份证 11010519491231002X",
        "文件在 /Users/neo/secret/customer.md 和 /etc/orbbec/private.conf",
        "参考 https://example.com/a?X-Amz-Signature=abc",
        "飞书标识 on_27882925f0e4f159846581dd8144ad63",
        "地址是深圳市南山区测试路88号",
        "请查附件 customer-secret.pdf",
    ],
)
def test_business_content_is_exported_verbatim(policy, content):
    result = sanitize_text(content, policy, "session-1")

    assert result.text == content
    assert result.safe is True


def test_no_placeholder_tokens_are_generated(policy):
    result = sanitize_text(
        "确认数据后，张候选人和客户甲集团在项目鹰的附件里补充地址",
        policy,
        "session-1",
    )

    for placeholder in ("[候选人", "[客户", "[项目", "[产品", "[地址", "[链接", "[附件"):
        assert placeholder not in result.text


def test_whitespace_is_normalized_without_dropping_content(policy):
    result = sanitize_text("  第一行  内容 \r\n  第二行  ", policy, "session-1")

    assert result.text == "第一行 内容\n第二行"


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


def test_sanitize_session_keeps_content_and_drops_raw_identifiers(policy):
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

    # Raw provider identifiers and unexported structures stay out of the replica.
    for forbidden in (
        "raw-session-id",
        "raw-turn-id",
        "raw-attachment-id",
        "on_27882925f0e4f159846581dd8144ad63",
        "/Users/neo/private.md",
        "system_prompt",
        "provider_chat_id",
        "oc_secret",
    ):
        assert forbidden not in serialized

    # Business content is preserved exactly.
    assert result.title.text == "客户甲集团招聘项目鹰"
    assert result.turns[0].question.text == "张候选人的邮箱是 alice@example.com"
    assert result.turns[0].answer.text == "请查附件 customer-secret.pdf"
    assert result.turns[0].attachments[0].display_label == "customer-secret.pdf"
    assert result.turns[0].attachments[0].size_bucket == "100 KiB–1 MiB"
    assert result.primary_sender_name == "洛奇"
    assert result.primary_sender_department == "市场部"


def test_credentials_in_one_turn_do_not_omit_the_other_message(policy):
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

    assert result.turns[0].question.text == "[凭证]"
    assert result.turns[0].answer.text == "本来安全的回答"
    assert result.turns[0].question.safe is True
    assert result.turns[0].answer.safe is True


def test_attachment_without_display_name_falls_back_to_ordinal_label(policy):
    now = datetime(2026, 8, 11, tzinfo=UTC)
    raw = RawSession(
        session_key="s1", agent_id="hr-bot", source_kind="metabot", channel="feishu",
        title=None, user_identity="u1", primary_sender_name="磐德",
        primary_sender_department="HR", created_at=now, last_active_at=now,
        turns=(RawTurn(
            turn_key="t1", turn_index=1, question="问题", answer="回答", created_at=now,
            attachments=(RawAttachment(
                attachment_id="a1", direction="user_input", display_name=None,
                mime_type="application/pdf", size_bytes=100,
                received_or_generated_at=now, archive_status="available",
                delivery_status="not_applicable",
            ),),
        ),),
    )

    assert sanitize_session(raw, policy).turns[0].attachments[0].display_label == "附件 1"


def test_source_attachment_states_are_reduced_to_safe_categories(policy):
    now = datetime(2026, 8, 11, tzinfo=UTC)
    raw = RawSession(
        session_key="s1", agent_id="hr-bot", source_kind="metabot", channel="feishu",
        title=None, user_identity="u1", primary_sender_name="磐德",
        primary_sender_department="HR", created_at=now, last_active_at=now,
        turns=(RawTurn(
            turn_key="t1", turn_index=1, question="问题", answer="回答", created_at=now,
            attachments=(RawAttachment(
                attachment_id="a1", direction="user_input", display_name="简历.pdf",
                mime_type="application/pdf", size_bytes=100,
                received_or_generated_at=now, archive_status="available",
                delivery_status="not_applicable",
            ),),
        ),),
    )

    attachment = sanitize_session(raw, policy).turns[0].attachments[0]

    assert attachment.direction == "incoming"
    assert attachment.archive_status == "archived"
    assert attachment.delivery_status == "unavailable"
    assert attachment.display_label == "简历.pdf"


def test_management_projection_keeps_text_and_hashes_identifiers(policy):
    now = datetime(2026, 8, 11, tzinfo=UTC)
    issue = sanitize_management_projection(
        ReviewIssueProjection(
            issue_id=uuid4(),
            agent_id="hr-bot",
            status="open",
            priority="P1",
            title="联系 alice@example.com 处理项目鹰 /Users/neo/a.md",
            failure_layer="model",
            owner_display="张候选人",
            linked_turn_count=1,
            updated_at=now,
            scope_valid=True,
        ),
        policy,
        b"i" * 32,
    )
    operation = sanitize_management_projection(
        OperationEventProjection(
            event_id="raw-event-provider-on_27882925f0e4f159846581dd8144ad63",
            agent_id=None,
            event_type="data_access_recovered",
            event_family="recovery",
            severity="info",
            status="historical",
            title="flywheel data access recovered",
            summary="客户甲集团 https://example.com/private",
            source_kind="flywheel",
            occurred_at=now,
        ),
        policy,
        b"i" * 32,
    )

    assert issue["title"]["text"] == "联系 alice@example.com 处理项目鹰 /Users/neo/a.md"
    assert issue["owner_display"] == "张候选人"
    assert issue["scope_valid"] is True
    assert operation["summary"]["text"] == "客户甲集团 https://example.com/private"
    assert operation["title"]["text"] == "flywheel data access recovered"
    assert operation["agent_id"] is None
    assert operation["event_family"] == "recovery"
    assert operation["status"] == "historical"
    assert operation["source_kind"] == "flywheel"
    assert set(operation) == {
        "kind", "key", "agent_id", "occurred_at", "event_type",
        "event_family", "severity", "status", "title", "summary",
        "source_kind", "sanitizer_policy_version",
    }
    # The raw event id is still replaced by a derived stable identifier.
    assert "on_27882925f0e4f159846581dd8144ad63" not in json.dumps(
        operation, ensure_ascii=False, default=str
    )


def test_inbox_projection_carries_only_scope_valid_marker(policy):
    now = datetime(2026, 8, 11, tzinfo=UTC)
    record = sanitize_management_projection(
        ReviewInboxProjection(
            agent_id="ai-fae-agent",
            turn_key="fae:turn-1",
            feedback_count=1,
            first_feedback_at=now,
            scope_valid=True,
        ),
        policy,
        b"i" * 32,
    )

    assert record["scope_valid"] is True
    assert set(record) == {
        "kind", "key", "agent_id", "turn_key", "feedback_count",
        "first_feedback_at", "scope_valid", "sanitizer_policy_version",
    }
