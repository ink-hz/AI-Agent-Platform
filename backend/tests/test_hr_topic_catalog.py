from copy import deepcopy

import pytest

from app.hr import topic_catalog

UNIT = "00000000-0000-4000-8000-000000000002"


def topic_inputs():
    topic = {
        "topic_id": "campus-recruiting",
        "title": "校招布局",
        "question": "当前校招岗位分布在哪里？",
        "scope": {
            "description": "已采集公司的校招岗位",
            "company_keys": ["hesai", "other"],
            "tracks": ["campus"],
        },
        "analysis_state": "limited",
        "unit_ids": [UNIT],
        "discussed_companies": [
            {
                "company_key": "hesai",
                "unit_id": UNIT,
                "claim_ids": ["I-campus-1"],
                "explanation": "该判断列明禾赛校招分布。",
            }
        ],
        "limitations": ["公开岗位不等于编制。"],
    }
    catalog = {
        "companies": [{"company_key": "hesai"}, {"company_key": "other"}],
        "topics": [topic],
    }
    analyses = [
        {
            "unit_id": UNIT,
            "kind": "track",
            "response": {
                "inferences": [
                    {"inference_id": "I-campus-1", "text": "禾赛公开校招分布。"}
                ]
            },
        }
    ]
    return catalog, analyses


def test_catalog_preserves_declared_scope_separately_from_discussed_companies():
    catalog, analyses = topic_inputs()
    result = topic_catalog.validate_topic_catalog(catalog, analyses)
    assert result == catalog["topics"]
    assert result is not catalog["topics"]
    assert result[0]["scope"]["company_keys"] == ["hesai", "other"]
    assert [x["company_key"] for x in result[0]["discussed_companies"]] == ["hesai"]
    assert topic_catalog.validate_topic_catalog({"companies": []}, []) == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c["topics"].append(deepcopy(c["topics"][0])),
        lambda c: c["topics"][0].update(topic_id="../escape"),
        lambda c: c["topics"][0].update(title=" "),
        lambda c: c["topics"][0].update(question=""),
        lambda c: c["topics"][0].update(unit_ids=[]),
        lambda c: c["topics"][0].update(analysis_state="missing"),
        lambda c: c["topics"][0]["scope"].update(company_keys=["absent"]),
        lambda c: c["topics"][0]["scope"].update(company_keys=["other"]),
        lambda c: c["topics"][0]["discussed_companies"][0].update(claim_ids=["absent"]),
        lambda c: c["topics"][0]["discussed_companies"][0].update(explanation=""),
        lambda c: c["topics"][0]["discussed_companies"].append(
            deepcopy(c["topics"][0]["discussed_companies"][0])
        ),
        lambda c: c["topics"][0]["discussed_companies"][0].update(
            claim_ids=["I-campus-1", "I-campus-1"]
        ),
    ],
)
def test_catalog_rejects_unusable_or_dangling_declarations(mutation):
    catalog, analyses = topic_inputs()
    mutation(catalog)
    with pytest.raises(ValueError, match="topic"):
        topic_catalog.validate_topic_catalog(catalog, analyses)


def test_company_unit_cannot_masquerade_as_topic_and_missing_is_explicit():
    catalog, analyses = topic_inputs()
    analyses[0]["kind"] = "company"
    with pytest.raises(ValueError, match="topic"):
        topic_catalog.validate_topic_catalog(catalog, analyses)
    topic = catalog["topics"][0]
    topic.update(analysis_state="missing", unit_ids=[], discussed_companies=[])
    assert (
        topic_catalog.validate_topic_catalog(catalog, analyses)[0]["analysis_state"]
        == "missing"
    )
