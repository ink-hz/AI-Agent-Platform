import base64
import json

import pytest

from app.hr.structured_output import encode_hr_envelope, extract_hr_envelope


def _comment(document: object) -> str:
    encoded = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")
    return f"<!-- platform-hr-v1:{token} -->"


def _position_payload() -> dict[str, object]:
    return {
        "title": "高级结构工程师",
        "modules": {
            "mission": {"text": "负责喷嘴与挤出系统"},
            "jd": {"text": "负责喷嘴与挤出系统结构设计。"},
            "jr": {"text": "具备五年以上精密结构量产经验。"},
        },
    }


def test_position_package_round_trips_without_changing_visible_markdown() -> None:
    payload = _position_payload()

    suffix = encode_hr_envelope("position_package", payload)
    parsed = extract_hr_envelope(f"岗位方案如下。\n\n{suffix}", "position_package")

    assert parsed is not None
    assert parsed.payload == payload
    assert parsed.visible_markdown == "岗位方案如下。"


@pytest.mark.parametrize(
    "markdown",
    [
        "岗位方案\n<!-- platform-hr-v1:e30= -->",
        "岗位方案\n<!-- platform-hr-v1:e30 -->\n<!-- platform-hr-v1:e30 -->",
        "岗位方案\n<!-- platform-hr-v1:_w -->",
    ],
)
def test_extract_rejects_malformed_or_ambiguous_envelopes(markdown: str) -> None:
    assert extract_hr_envelope(markdown, "position_package") is None


@pytest.mark.parametrize(
    "document",
    [
        {
            "schema_version": 1,
            "kind": "position_package",
            "payload": _position_payload(),
            "extra": True,
        },
        {
            "schema_version": 1,
            "kind": "candidate_match",
            "payload": _position_payload(),
        },
    ],
)
def test_extract_rejects_unknown_envelope_keys_and_kind_mismatches(
    document: dict[str, object],
) -> None:
    assert extract_hr_envelope(_comment(document), "position_package") is None


@pytest.mark.parametrize("missing_module", ["mission", "jd", "jr"])
def test_position_package_requires_each_core_module(missing_module: str) -> None:
    payload = _position_payload()
    modules = dict(payload["modules"])
    del modules[missing_module]
    payload["modules"] = modules

    assert (
        extract_hr_envelope(
            _comment(
                {"schema_version": 1, "kind": "position_package", "payload": payload}
            ),
            "position_package",
        )
        is None
    )


def test_extract_rejects_decoded_documents_larger_than_512_kib() -> None:
    payload = _position_payload()
    payload["title"] = "x" * (512 * 1024)

    assert (
        extract_hr_envelope(
            _comment(
                {"schema_version": 1, "kind": "position_package", "payload": payload}
            ),
            "position_package",
        )
        is None
    )


def test_extract_rejects_noncanonical_json() -> None:
    document = json.dumps(
        {
            "kind": "position_package",
            "schema_version": 1,
            "payload": _position_payload(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(document).decode("ascii").rstrip("=")

    assert (
        extract_hr_envelope(f"<!-- platform-hr-v1:{token} -->", "position_package")
        is None
    )


def test_candidate_envelopes_require_their_exact_payload_shapes() -> None:
    candidate_match = {
        "summary": "总体匹配",
        "dimensions": {},
        "evidence": [],
        "gaps": [],
        "risks": [],
        "unknowns": [],
        "verification_questions": [],
    }
    interview_plan = {
        "title": "结构工程师面试题",
        "questions": [
            {
                "verification_goal": "验证量产经验",
                "candidate_reason": "简历提及量产",
                "question": "请说明量产挑战。",
                "follow_ups": [],
                "strong_evidence": [],
                "risk_signals": [],
            }
        ],
    }

    assert (
        extract_hr_envelope(
            _comment(
                {
                    "schema_version": 1,
                    "kind": "candidate_match",
                    "payload": candidate_match,
                }
            ),
            "candidate_match",
        )
        is not None
    )
    assert (
        extract_hr_envelope(
            _comment(
                {
                    "schema_version": 1,
                    "kind": "candidate_interview_plan",
                    "payload": interview_plan,
                }
            ),
            "candidate_interview_plan",
        )
        is not None
    )
    del interview_plan["questions"][0]["risk_signals"]
    assert (
        extract_hr_envelope(
            _comment(
                {
                    "schema_version": 1,
                    "kind": "candidate_interview_plan",
                    "payload": interview_plan,
                }
            ),
            "candidate_interview_plan",
        )
        is None
    )
