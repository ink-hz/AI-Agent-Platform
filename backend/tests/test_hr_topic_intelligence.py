from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.hr.company_intelligence import project_company
from app.hr.panorama_repository import PanoramaNotFound, PanoramaUnavailable
from app.hr.topic_intelligence import project_topic, project_topics


def record():
    unit_id = str(uuid4())
    return {
        'bundle_id': uuid4(), 'generated_at': datetime.now(timezone.utc),
        'source_catalog': {'companies': [
            {'company_key': 'a', 'canonical_name': '甲公司', 'aliases': []},
            {'company_key': 'b', 'canonical_name': '乙公司', 'aliases': []},
        ], 'topics': [{
            'topic_id': 'hiring-location', 'title': '招聘布局', 'question': '岗位分布在哪里？',
            'scope': {'description': '两家公司公开样本', 'company_keys': ['a', 'b'], 'tracks': []},
            'analysis_state': 'limited', 'unit_ids': [unit_id],
            'discussed_companies': [{'company_key': 'a', 'unit_id': unit_id,
                                     'claim_ids': ['I-1'], 'explanation': '正文讨论甲公司的地域分布。'}],
            'limitations': ['单次观察'],
        }]},
        'source_coverage': {'companies': []}, 'aggregates': {},
        'analysis': [{'unit_id': unit_id, 'kind': 'topic', 'scope_key': 'geography',
                      'request': {'private': 'must not leak'}, 'usage': {'cost': 99},
                      'response': {'summary': '甲公司岗位分布', 'confidence': 'low',
                                   'facts': [], 'inferences': [{'inference_id': 'I-1', 'text': '甲公司地域分布', 'basis_fact_ids': []}],
                                   'recommendations': [], 'alternatives': [], 'unknowns': ['覆盖不完整']}}],
    }


def test_topic_directory_uses_persisted_business_metadata_without_full_units():
    result = project_topics(record())
    assert result['state'] == 'available'
    assert result['items'][0]['title'] == '招聘布局'
    assert result['items'][0]['summary'] == '甲公司岗位分布'
    assert 'units' not in result and 'request' not in str(result)
    assert 'inferences' not in result['items'][0]


def test_legacy_topics_are_metadata_missing_and_not_invented_from_scope_key():
    value = record()
    del value['source_catalog']['topics']
    assert project_topics(value)['state'] == 'metadata_missing'
    assert project_topics(value)['items'] == []
    with pytest.raises(PanoramaNotFound):
        project_topic(value, 'geography')


def test_topic_detail_retains_claims_and_only_explicit_discussion_relationships():
    value = record()
    result = project_topic(value, 'hiring-location')
    assert result['topic']['scope']['company_keys'] == ['a', 'b']
    assert result['companies'] == [{'company_key': 'a', 'canonical_name': '甲公司'}]
    assert result['units'][0]['response'] == value['analysis'][0]['response']
    assert result['units'][0]['kind'] == 'topic'
    assert 'request' not in result['units'][0] and 'usage' not in result['units'][0]
    assert 'units' not in result['topic']


def test_dangling_published_unit_is_unavailable_not_empty_ready_page():
    value = record()
    value['analysis'] = []
    with pytest.raises(PanoramaUnavailable):
        project_topic(value, 'hiring-location')


def test_company_backlinks_are_projected_from_narrow_relationship_query():
    value = record()
    value['related_topics'] = [{'topic_id': 'hiring-location', 'title': '招聘布局', 'summary': '甲公司岗位分布'}]
    assert project_company(value, 'a')['related_topics'] == value['related_topics']
    value['related_topics'] = []
    assert project_company(value, 'b')['related_topics'] == []
