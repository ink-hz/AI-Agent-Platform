import hashlib
from copy import deepcopy

import pytest

from app.sdk_knowledge.service import KnowledgeService

OLD, NEW = 'a' * 40, 'b' * 40
OLD_HASH, NEW_HASH = hashlib.sha256(b'old').hexdigest(), hashlib.sha256(b'new').hexdigest()


class Corpus:
    baseline_id = 'locked-fixture'

    def source_pages(self, repository, path):
        if (repository, path) != ('SDK', 'SDK/version.cpp'):
            return []
        return deepcopy([{
            'page_id': 'KB-VER-001', 'title': '版本', 'conditions': {'devices': ['USB设备'], 'firmware': ['未核验']},
            'claims': [{'claim_id': 'KB-VER-001-C1', 'text': 'old', 'verification': 'source_verified', 'evidence_ids': ['E1']}],
            'evidence': [{'evidence_id': 'E1', 'repository': 'SDK', 'commit': OLD, 'path': path,
                          'line_start': 10, 'line_end': 20, 'sha256': OLD_HASH}],
            'unknowns': [{'classification': 'runtime_not_run'}], 'runtime_validation': 'not_run',
            'review_status': 'independently_reviewed', 'body_sha256': 'c' * 64, 'semantic_sha256': 'd' * 64,
        }])


class Source:
    def compare(self, repository, base_commit, target_commit):
        return {'repository': repository, 'base_commit': base_commit, 'target_commit': target_commit,
                'files': [{'path': 'SDK/version.cpp', 'old_sha256': OLD_HASH, 'new_sha256': NEW_HASH,
                           'hunks': [{'old_start': 15, 'old_lines': 1, 'new_start': 15, 'new_lines': 1}],
                           'diff': '-old\n+new', 'binary': False}]}

    def blob_sha256(self, repository, commit, path):
        return OLD_HASH if commit == OLD else NEW_HASH


def test_stale_evidence_is_reported_separately_from_direct_line_overlap():
    result = KnowledgeService(Corpus(), Source()).inspect_change('SDK', OLD, NEW)
    page = result['affected_pages'][0]
    assert page['source_checks'][0]['state'] == 'content_changed'
    assert page['changed_references'][0]['line_overlap'] is True
    assert page['changed_references'][0]['line_basis'] == 'base_commit'
    assert page['knowledge_state'] == 'needs_revalidation'
    assert page['claims'][0]['verification'] == 'source_verified'
    assert page['claims'][0]['text'] == 'old'
    assert page['runtime_validation'] == 'not_run'
    assert page['conditions']['firmware'] == ['未核验']
    assert result['coverage']['indirect_dependencies'] == 'not_computed'
    assert result['runtime_validation'] == 'not_run'


def test_baseline_content_match_does_not_become_an_entire_version_approval():
    source = Source()
    source.blob_sha256 = lambda *args: OLD_HASH
    result = KnowledgeService(Corpus(), source).inspect_change('SDK', NEW, OLD)
    page = result['affected_pages'][0]
    assert page['source_checks'][0]['state'] == 'content_matches'
    assert page['knowledge_state'] == 'cited_content_matches_target'
    assert result['coverage']['other_repositories'] == 'not_observed'
    assert 'promotion_eligible' not in result


def test_file_hash_change_outside_reviewed_lines_is_still_a_revalidation_candidate():
    source = Source()
    compare = source.compare
    source.compare = lambda *args: {**compare(*args), 'files': [{**compare(*args)['files'][0], 'hunks': [
        {'old_start': 50, 'old_lines': 1, 'new_start': 50, 'new_lines': 1}]}]}
    page = KnowledgeService(Corpus(), source).inspect_change('SDK', OLD, NEW)['affected_pages'][0]
    assert page['changed_references'][0]['line_overlap'] is False
    assert page['knowledge_state'] == 'needs_revalidation'


def test_unmapped_paths_and_missing_sources_remain_explicit():
    source = Source()
    source.blob_sha256 = lambda *args: None
    page = KnowledgeService(Corpus(), source).inspect_change('SDK', OLD, NEW)['affected_pages'][0]
    assert page['source_checks'][0]['state'] == 'source_missing'
    corpus = Corpus()
    corpus.source_pages = lambda *args: []
    result = KnowledgeService(corpus, source).inspect_change('SDK', OLD, NEW)
    assert result['affected_pages'] == []
    assert result['coverage']['unmapped_paths'] == ['SDK/version.cpp']


def test_source_not_configured_is_an_explicit_failure():
    with pytest.raises(ValueError, match='source_not_configured'):
        KnowledgeService(Corpus(), None).inspect_change('SDK', OLD, NEW)
