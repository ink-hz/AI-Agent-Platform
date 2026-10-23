"""Opt-in acceptance of frozen knowledge plus actual read-only SDK Git objects."""
import json
import os
from pathlib import Path

import pytest

from app.sdk_knowledge.corpus import KnowledgeCorpus
from app.sdk_knowledge.git_source import GitSourceReader
from app.sdk_knowledge.service import KnowledgeService

pytestmark = pytest.mark.skipif(not os.getenv('SDK_KNOWLEDGE_REAL_INDEX'), reason='explicit frozen SDK input required')


@pytest.fixture(scope='module')
def knowledge():
    corpus = KnowledgeCorpus.from_file(Path(os.environ['SDK_KNOWLEDGE_REAL_INDEX']),
                                      expected_sha256=os.environ['SDK_KNOWLEDGE_REAL_INDEX_SHA256'])
    source = GitSourceReader(Path(os.environ['SDK_KNOWLEDGE_REAL_SOURCE_ROOT']), corpus.repositories)
    return corpus, KnowledgeService(corpus, source)


def test_actual_api_identifier_and_natural_question_have_reviewed_candidates(knowledge):
    corpus, _ = knowledge
    result = corpus.search('ob_pipeline_stop')
    assert result['items'][0]['page_id'] == 'KB-API-C-ob_pipeline_stop'
    natural = corpus.search('如何停止Pipeline并等待回调退出')
    assert natural['items'][0]['page_id'] == 'KB-FLOW-024'
    for row in (result['items'][0], natural['items'][0]):
        assert row['review_status'] == 'independently_reviewed'
        assert row['runtime_validation'] == 'not_run'
        assert row['claims'] and row['evidence']


def test_actual_conditions_unknown_answer_and_wrong_repository_are_preserved(knowledge):
    corpus, _ = knowledge
    page = corpus.page('KB-PROP-OB_DEVICE_AUTO_CAPTURE_ENABLE_BOOL')
    assert '10510' in json.dumps(page['conditions']['firmware'])
    assert page['runtime_validation'] == 'not_run'
    assert corpus.search('ob_pipeline_stpo')['items'] == []
    assert corpus.search(repository='FrameProcessor', path='OpenOrbbecSDK/CMakeLists.txt')['items'] == []


def test_real_version_diff_and_reverse_source_mismatch_are_not_runtime_results(knowledge):
    _, service = knowledge
    old, new = '7e7738d4edc4a14ca22249167e3b5db473884aac', 'ad7518704a7ab630719a691748c4f36473a416bb'
    forward = service.inspect_change('OpenOrbbecSDK', old, new)
    assert len(forward['observation']['files']) == 1
    assert forward['observation']['files'][0]['hunks'] == [{'old_start': 14, 'old_lines': 1, 'new_start': 14, 'new_lines': 1}]
    assert {p['page_id'] for p in forward['affected_pages']} == {'KB-ENG-002', 'KB-ENG-007', 'KB-ENG-012', 'KB-ENG-014', 'KB-PAL-022'}
    pal = next(p for p in forward['affected_pages'] if p['page_id'] == 'KB-PAL-022')
    assert pal['changed_references'][0]['line_overlap'] is False
    reverse = service.inspect_change('OpenOrbbecSDK', new, old)
    assert all(p['knowledge_state'] == 'needs_revalidation' for p in reverse['affected_pages'])
    assert reverse['runtime_validation'] == forward['runtime_validation'] == 'not_run'
