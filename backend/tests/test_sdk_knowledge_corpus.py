"""Pinned, synthetic KB fixtures; never modify or execute frozen SDK inputs."""
import copy
import hashlib
import importlib
import json
from pathlib import Path

import pytest
import yaml


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


@pytest.fixture
def api():
    return importlib.import_module('app.sdk_knowledge.corpus')


@pytest.fixture
def package(tmp_path):
    kb = tmp_path / 'sdk源码/OpenOrbbecSDKGroup/docs/knowledge-base'
    art = tmp_path / 'artifacts/sdk-platform/knowledge-base/semantic-2026-10-22'
    kb.mkdir(parents=True)
    (art / 'reviews').mkdir(parents=True)
    review_raw = encoded({'reviewer': 'independent'})
    (art / 'reviews/result.json').write_bytes(review_raw)
    baseline = {'baseline_id': 'TEST-01', 'repositories': [
        {'repository': 'SDK', 'relative_path': 'SDK', 'head': 'a' * 40},
        {'repository': 'Group', 'relative_path': '.', 'head': 'b' * 40},
    ]}
    files = {'_meta/baseline.json': encoded(baseline), 'README.md': b'# Maintenance only'}
    pages = []
    for i in range(3):
        pid = f'KB-TEST-{i}'
        path = f'01-system/{pid}.md'
        conditions = {'devices': ['Gemini2'] if i == 0 else ['all_applicable'] if i == 1 else [],
                      'platforms': ['Linux'] if i != 2 else ['unspecified']}
        evidence = [{'evidence_id': f'{pid}-E', 'repository': 'SDK', 'path': 'SDK/src/Pipeline.cpp',
                     'commit': 'a' * 40, 'symbol': 'Pipeline::start', 'sha256': 'c' * 64,
                     'line_start': 2, 'line_end': 8}]
        claims = [{'claim_id': f'{pid}-C', 'text': 'Pipeline::start 管线启动停止',
                   'verification': 'future_verification', 'conditions': ['running'],
                   'evidence_ids': [f'{pid}-E']},
                  {'claim_id': f'{pid}-C2', 'text': 'Unrelated flush contract',
                   'verification': 'declared', 'conditions': ['idle'], 'evidence_ids': []}]
        record = {'schema_version': '2.0', 'page_id': pid, 'path': path, 'baseline_id': 'TEST-01',
                  'conditions': conditions, 'claims': claims, 'evidence': evidence,
                  'unknowns': [{'classification': 'runtime_not_run', 'status': 'open'}],
                  'source_files_reviewed': [{'path': 'SDK/src/Pipeline.cpp', 'ranges': [[2, 8]]}]}
        body = f'\n# Pipeline 管线 {i}\n\nRuntime not run.\n'
        body_sha = digest(body.encode())
        semantic_sha = digest(encoded(record))
        reviews = [{'reviewer': 'independent', 'result': 'accepted_with_external_boundaries',
                    'reviewed_body_sha256': body_sha, 'reviewed_semantic_sha256': semantic_sha,
                    'review_artifact': 'reviews/result.json', 'review_artifact_sha256': digest(review_raw)}]
        record['review_records'] = reviews
        meta = {'id': pid, 'title': f'Pipeline 管线 {i}', 'baseline_id': 'TEST-01',
                'semantic_record': f'_meta/semantic-pages/{pid}.json', 'conditions': conditions,
                'semantic_review': 'independently_reviewed', 'review_records': reviews,
                'status': 'source_reviewed', 'runtime_validation': 'not_run'}
        files[path] = ('---\n' + yaml.safe_dump(meta, allow_unicode=True) + '---' + body).encode()
        files[meta['semantic_record']] = encoded(record)
        pages.append({'page_id': pid, 'path': path, 'body_sha256': body_sha,
                      'semantic_sha256': semantic_sha, 'reviewers': ['independent']})
    for path, raw in files.items():
        p = kb / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    manifest = {'baseline_id': 'TEST-01', 'pages': pages,
                'knowledge_base_files': [{'path': p, 'sha256': digest(raw)} for p, raw in files.items()],
                'artifacts': [{'path': 'reviews/result.json', 'sha256': digest(review_raw)}]}
    manifest_path = art / 'reviews/manifest.json'

    def pin():
        manifest_path.write_bytes(encoded(manifest))
        return digest(manifest_path.read_bytes())

    return {'root': tmp_path, 'kb': kb, 'art': art, 'manifest': manifest,
            'manifest_path': manifest_path, 'pin': pin, 'sha': pin()}


def build(api, package):
    return api.build_index(package['root'], manifest_path=package['manifest_path'],
                           expected_manifest_sha256=package['sha'])


def repin_file(package, relative):
    for row in package['manifest']['knowledge_base_files']:
        if row['path'] == relative:
            row['sha256'] = digest((package['kb'] / relative).read_bytes())
    package['sha'] = package['pin']()


def test_build_formal_pages_only_and_preserve_fields(api, package):
    bundle = build(api, package)
    assert bundle['schema_version'] == 1
    assert len(bundle['pages']) == 3
    assert bundle['input_manifest_sha256'] == package['sha']
    assert bundle['repositories'][0] == {'repository': 'SDK', 'relative_path': 'SDK', 'commit': 'a' * 40}
    page = bundle['pages'][0]
    assert page['claims'][0]['verification'] == 'future_verification'
    assert page['unknowns'][0]['classification'] == 'runtime_not_run'
    assert page['runtime_validation'] == 'not_run'
    assert page['evidence'][0]['path'] == 'SDK/src/Pipeline.cpp'
    assert page['review_status'] == 'independently_reviewed'


def test_manifest_pin_required(api, package):
    package['sha'] = '0' * 64
    with pytest.raises(api.KnowledgeUnavailable):
        build(api, package)


@pytest.mark.parametrize('kind', ['raw_body', 'body', 'semantic', 'review', 'review_artifact', 'maintenance'])
def test_tampering_fails_even_when_raw_hash_rebound(api, package, kind):
    pid = 'KB-TEST-0'
    md = '01-system/' + pid + '.md'
    sem = '_meta/semantic-pages/' + pid + '.json'
    if kind in ('raw_body', 'body'):
        p = package['kb'] / md
        p.write_bytes(p.read_bytes() + b'altered')
        if kind == 'body':
            repin_file(package, md)
    elif kind in ('semantic', 'review'):
        p = package['kb'] / sem
        r = json.loads(p.read_bytes())
        if kind == 'semantic':
            r['claims'][0]['text'] = 'changed'
        else:
            r['review_records'][0]['reviewed_semantic_sha256'] = '0' * 64
        p.write_bytes(encoded(r))
        repin_file(package, sem)
    elif kind == 'review_artifact':
        (package['art'] / 'reviews/result.json').write_text('changed')
    else:
        (package['kb'] / 'README.md').write_text('changed')
    with pytest.raises(api.KnowledgeUnavailable):
        build(api, package)


@pytest.mark.parametrize('unsafe', ['../outside.md', '/tmp/outside.md', '01-system/../../outside.md'])
def test_manifest_path_escape_rejected(api, package, unsafe):
    package['manifest']['knowledge_base_files'][0]['path'] = unsafe
    package['sha'] = package['pin']()
    with pytest.raises(api.KnowledgeUnavailable):
        build(api, package)


def test_symlink_file_and_parent_rejected(api, package):
    p = package['kb'] / 'README.md'
    outside = package['root'] / 'outside.md'
    outside.write_bytes(p.read_bytes())
    p.unlink()
    p.symlink_to(outside)
    with pytest.raises(api.KnowledgeUnavailable):
        build(api, package)
    p.unlink()
    p.write_bytes(outside.read_bytes())
    directory = package['kb'] / '01-system'
    moved = package['root'] / 'moved'
    directory.rename(moved)
    directory.symlink_to(moved, target_is_directory=True)
    with pytest.raises(api.KnowledgeUnavailable):
        build(api, package)


def test_duplicate_ids_rejected_in_manifest_and_bundle(api, package):
    bundle = build(api, package)
    bundle['pages'].append(copy.deepcopy(bundle['pages'][0]))
    with pytest.raises(api.KnowledgeUnavailable):
        api.KnowledgeCorpus(bundle)
    package['manifest']['pages'].append(copy.deepcopy(package['manifest']['pages'][0]))
    package['sha'] = package['pin']()
    with pytest.raises(api.KnowledgeUnavailable):
        build(api, package)


def test_search_casefold_chinese_matching_claims_and_bounds(api, package):
    corpus = api.KnowledgeCorpus(build(api, package))
    result = corpus.search('pIpElInE::START', limit=1)
    assert result['baseline_id'] == 'TEST-01'
    assert result['total'] == 3 and len(result['items']) == 1
    item = result['items'][0]
    assert len(item['claims']) == 1
    assert item['claims'][0]['text'] == 'Pipeline::start 管线启动停止'
    assert len(item['evidence']) == 1
    assert corpus.search('管线 启动')['total'] == 3
    assert corpus.search('not-present')['items'] == []
    assert corpus.search('not-present')['total'] == 0
    with pytest.raises(ValueError):
        corpus.search(limit=0)
    with pytest.raises(ValueError):
        corpus.search(limit=101)


def test_explicit_filters_do_not_invent_generic_applicability(api, package):
    corpus = api.KnowledgeCorpus(build(api, package))
    assert corpus.search(device='Gemini2')['total'] == 1
    assert corpus.search(device='Gemini')['total'] == 0
    assert corpus.search(platform='linux')['total'] == 2
    assert corpus.search(device='all_applicable')['items'][0]['conditions']['devices'] == ['all_applicable']
    assert corpus.search(repository='SDK', path='SDK/src/Pipeline.cpp', symbol='pipeline::START')['total'] == 3
    assert corpus.search(repository='Group')['total'] == 0
    assert corpus.search(path='src/Pipeline.cpp')['total'] == 0
    assert corpus.search(path='SDK/src/Pipeline.cpp.bak')['total'] == 0


def test_source_lookup_exact_and_copies_are_defensive(api, package):
    original = build(api, package)
    corpus = api.KnowledgeCorpus(original)
    original['pages'][0]['claims'][0]['text'] = 'external mutation'
    result = corpus.source_pages('SDK', 'SDK/src/Pipeline.cpp')
    assert len(result) == 3
    result[0]['claims'][0]['text'] = 'mutated'
    assert corpus.page('KB-TEST-0')['claims'][0]['text'] == 'Pipeline::start 管线启动停止'
    assert corpus.source_pages('SDK', 'src/Pipeline.cpp') == []
    assert corpus.source_pages('Group', 'SDK/src/Pipeline.cpp') == []
    assert corpus.page('unknown') is None
    page = corpus.page('KB-TEST-0')
    page['claims'].clear()
    assert len(corpus.page('KB-TEST-0')['claims']) == 2
    corpus.search()['items'][0]['claims'].clear()
    assert len(corpus.page('KB-TEST-0')['claims']) == 2


def test_bundle_file_pin_schema_and_sanitized_errors(api, package):
    p = package['root'] / 'bundle.json'
    p.write_bytes(encoded(build(api, package)))
    corpus = api.KnowledgeCorpus.from_file(p, expected_sha256=digest(p.read_bytes()))
    assert corpus.page('KB-TEST-0')
    with pytest.raises(api.KnowledgeUnavailable) as exc:
        api.KnowledgeCorpus.from_file(p, expected_sha256='0' * 64)
    assert str(package['root']) not in str(exc.value)
    p.write_text('{bad json')
    with pytest.raises(api.KnowledgeUnavailable):
        api.KnowledgeCorpus.from_file(p, expected_sha256=digest(p.read_bytes()))
    with pytest.raises(api.KnowledgeUnavailable):
        api.KnowledgeCorpus({'schema_version': 9})


def test_read_only_baseline_and_repository_properties(api, package):
    corpus = api.KnowledgeCorpus(build(api, package))
    assert corpus.baseline_id == 'TEST-01'
    repos = corpus.repositories
    repos[0]['commit'] = '0' * 40
    assert corpus.repositories[0]['commit'] == 'a' * 40
    with pytest.raises(AttributeError):
        corpus.baseline_id = 'changed'


def test_global_duplicate_claim_identity_rejected(api, package):
    bundle = build(api, package)
    bundle['pages'][1]['claims'][0]['claim_id'] = bundle['pages'][0]['claims'][0]['claim_id']
    with pytest.raises(api.KnowledgeUnavailable):
        api.KnowledgeCorpus(bundle)


def test_search_bounds_keep_claim_evidence_references_resolvable(api, package):
    bundle = build(api, package)
    page = bundle['pages'][0]
    template = page['evidence'][0]
    page['evidence'] = [dict(template, evidence_id=f'fanout-{i}') for i in range(50)]
    page['claims'][0]['evidence_ids'] = [e['evidence_id'] for e in page['evidence']]
    corpus = api.KnowledgeCorpus(bundle)
    item = next(i for i in corpus.search()['items'] if i['page_id'] == page['page_id'])
    assert len(item['claims']) <= 8
    assert len(item['evidence']) <= 32
    ids = {e['evidence_id'] for e in item['evidence']}
    assert all(set(c['evidence_ids']).issubset(ids) for c in item['claims'])
    assert item['truncated'] is True
    assert len(corpus.page(page['page_id'])['evidence']) == 50


def test_cli_builds_only_explicit_output_without_app_startup(api, package):
    import os
    import subprocess
    import sys
    output = package['root'] / 'compiled.json'
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    command = [sys.executable, '-m', 'tools.sdk_knowledge.build_index',
               '--platform-root', str(package['root']), '--manifest', str(package['manifest_path']),
               '--expected-manifest-sha256', package['sha'], '--output', str(output)]
    completed = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary['pages'] == 3
    assert summary['sha256'] == digest(output.read_bytes())
    again = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    assert again.returncode != 0
    assert str(package['root']) not in again.stderr
    isolated_import = subprocess.run(
        [sys.executable, '-c',
         "import sys; import app.sdk_knowledge.corpus; import tools.sdk_knowledge.build_index; assert 'app.main' not in sys.modules"],
        env=env, text=True, capture_output=True, check=False)
    assert isolated_import.returncode == 0, isolated_import.stderr


@pytest.mark.parametrize('query,expected', [
    ('如何停止Pipeline并等待回调退出', 'KB-TEST-0'),
    ('设备自动曝光的固件条件', 'KB-TEST-1'),
])
def test_natural_chinese_and_mixed_queries_recall_candidates(api, package, query, expected):
    bundle = build(api, package)
    bundle['pages'][0]['claims'][0]['text'] = 'Pipeline::stop 停止流水线，等待回调退出'
    bundle['pages'][1]['claims'][0]['text'] = '设备自动曝光受固件版本限制，具体条件仍须核对'
    corpus = api.KnowledgeCorpus(bundle)
    result = corpus.search(query)
    assert result['total'] > 0
    assert result['items'][0]['page_id'] == expected
    assert result['items'][0]['retrieval_kind'] == 'lexical_candidate'
    assert result['items'][0]['match']['cjk_terms']
    assert 0 < result['items'][0]['match']['coverage'] <= 1


def test_identifier_constraints_and_exact_title_rank_above_incidental_mentions(api, package):
    bundle = build(api, package)
    bundle['pages'][0]['title'] = 'Pipeline control'
    bundle['pages'][0]['claims'][0]['text'] = 'ob_pipeline_stop stops. ob_pipeline_stop ends. ob_pipeline_stop returns.'
    bundle['pages'][1]['title'] = 'ob_pipeline_stop'
    bundle['pages'][1]['claims'][0]['text'] = 'ob_pipeline_stop 停止'
    bundle['pages'][2]['claims'][0]['text'] = 'ob_pipeline_stop_extended 停止'
    corpus = api.KnowledgeCorpus(bundle)
    assert corpus.search('ob_pipeline_stop')['items'][0]['page_id'] == 'KB-TEST-1'
    assert corpus.search('ob_pipeline_stop')['total'] == 2
    assert corpus.search('停止ob_pipeline_StopWrong')['total'] == 0
    assert corpus.search('停止MissingPipeline')['total'] == 0
    result = corpus.search('如何停止Pipeline并等待回调退出')
    for item in result['items']:
        assert item['conditions'] == corpus.page(item['page_id'])['conditions']
        assert item['runtime_validation'] == 'not_run'
    if result['items']:
        result['items'][0]['match']['cjk_terms'].clear()
        assert corpus.search('如何停止Pipeline并等待回调退出')['items'][0]['match']['cjk_terms']


def test_cpp_query_preserves_literal_language_identifier(api, package):
    bundle = build(api, package)
    bundle['pages'][0]['claims'][0]['text'] = 'C++ wrapper lifecycle'
    bundle['pages'][1]['claims'][0]['text'] = 'C wrapper lifecycle'
    corpus = api.KnowledgeCorpus(bundle)
    assert [i['page_id'] for i in corpus.search('C++')['items']] == ['KB-TEST-0']
