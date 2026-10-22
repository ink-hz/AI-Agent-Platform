"""Synthetic fixtures only: never mutate or export the frozen source tree."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest

MODULE = 'tools.sdk_admin.build_content_release'
KB = Path('sdk源码/OpenOrbbecSDKGroup/docs/knowledge-base')
FROZEN = Path('artifacts/sdk-platform/knowledge-base/semantic-2026-10-22/reviews/final-review-input-manifest.json')
CURATED = [
    ('DOC-PLATFORM-PROCESS', 'docs/sdk-platform/03-平台建设方案.md'),
    ('DOC-OBJECT-STATES', 'docs/sdk-platform/design/B-01-对象与状态映射.md'),
    ('DOC-RELATION-RULES', 'docs/sdk-platform/design/B-01-关联ID与冲突规则.md'),
    ('DOC-ANALYSIS-STATUS', 'docs/sdk-platform/analysis/阶段性现状盘点-2026-10-22.md'),
    ('DOC-ANALYSIS-GIT', 'docs/sdk-platform/analysis/Git变更覆盖与需求节奏复核-2026-10-22.md'),
    ('DOC-KB-ACCEPTANCE', 'artifacts/sdk-platform/knowledge-base/semantic-2026-10-22/final-acceptance.md'),
    ('DOC-PORTAL-DESIGN', 'docs/sdk-platform/design/B-04-sdk-admin功能架构与智能化设计.md'),
    ('DOC-SDK-R0-RELEASE', 'docs/sdk-platform/execution/2026-10-22-sdk-r0-production/上线记录.md'),
    ('DOC-EXECUTION-LEDGER', 'docs/sdk-platform/任务执行台账.md'),
]
BODY = '\n# 全量正文\n\n设备/平台/固件/状态条件均须保留。\n\n[目录](../README.md#入口) [原始实现](../../src/a.cpp#L3)\n[编码](<../README.md#%E5%85%A5%E5%8F%A3>)\n[引用][root]\n[root]: ../README.md "目录"\n[本页](#详情)\n'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def store(root, path, raw):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)


def write_manifest(root, entries, *, pages=None):
    if pages is None:
        pages = [{'page_id': 'KB-API-001', 'path': '02-public-api/KB-API-001.md',
                  'body_sha256': digest(BODY.encode()), 'semantic_sha256': '0' * 64,
                  'reviewers': ['synthetic-independent-reviewer']}]
    raw = json.dumps({'baseline_id': 'SDK-TEST', 'pages': pages, 'knowledge_base_files': entries}).encode()
    store(root, FROZEN, raw)
    return digest(raw)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'platform'
    page = ('---\nid: KB-API-001\ntitle: API 条件\nlevel: L4\nbaseline_id: SDK-TEST\n'
            'status: source_reviewed\nsemantic_review: independently_reviewed\nruntime_validation: not_run\n'
            'conditions: {devices: [test-device], states: [streaming]}\n---\n' + BODY).encode()
    entries = []
    for path, raw in [('02-public-api/KB-API-001.md', page), ('README.md', b'# Root\n\n[API](02-public-api/KB-API-001.md)\n'), ('_meta/README.md', b'# Machine metadata\n')]:
        store(root, KB / path, raw)
        entries.append({'path': path, 'sha256': digest(raw)})
    store(root, KB / 'unlisted.md', b'# not frozen\n')
    for doc_id, path in CURATED:
        store(root, Path(path), f'# {doc_id}\n\n完整原文、适用条件和限制。\n'.encode())
    return root, entries, write_manifest(root, entries)


def build(source, output):
    root, _, expected = source
    return importlib.import_module(MODULE).build_release(root, output, expected_manifest_sha256=expected)


def test_export_full_bodies_conditions_hashes_navigation_and_links(source, tmp_path):
    output = tmp_path / 'release'
    summary = build(source, output)
    raw = (output / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    assert (output / 'manifest.sha256').read_text() == digest(raw) + '\n'
    assert summary['manifest_sha256'] == digest(raw)
    assert summary['counts'] == {'knowledge_pages': 1, 'knowledge_navigation': 2, 'documents': 12}
    assert manifest['schema_version'] == 1
    assert manifest['baseline_id'] == 'SDK-TEST'
    docs = {d['id']: d for d in manifest['documents']}
    assert set(docs) == {'KB-API-001', 'KB-INDEX-ROOT', 'KB-META-README', *(x[0] for x in CURATED)}
    page = docs['KB-API-001']
    assert (output / page['file']).read_text() == BODY
    assert page['sha256'] == digest(BODY.encode())
    assert page['source_sha256'] == digest((source[0] / page['source_path']).read_bytes())
    assert page['conditions'] == {'devices': ['test-device'], 'states': ['streaming']}
    assert page['content_state'] == 'accepted_static'
    assert page['review_status'] == 'independently_reviewed'
    assert page['runtime_validation'] == 'not_run'
    assert page['links'] == {
        '../README.md#入口': {'document_id': 'KB-INDEX-ROOT', 'fragment': '入口'},
        '../README.md#%E5%85%A5%E5%8F%A3': {'document_id': 'KB-INDEX-ROOT', 'fragment': '入口'},
        '../README.md': {'document_id': 'KB-INDEX-ROOT', 'fragment': ''},
        '#详情': {'document_id': 'KB-API-001', 'fragment': '详情'},
    }
    assert docs['KB-META-README']['content_state'] == 'navigation'
    assert docs['DOC-PORTAL-DESIGN']['content_state'] == 'design_proposal'
    assert docs['DOC-ANALYSIS-STATUS']['content_state'] == 'phase_report'
    assert docs['DOC-SDK-R0-RELEASE']['content_state'] == 'execution_record'
    assert manifest['ai']['available'] is False
    assert any(s.get('href') == '/sdk/' for s in manifest['sources'])
    for doc in docs.values():
        assert digest((output / doc['file']).read_bytes()) == doc['sha256']
        if doc['kind'] != 'knowledge':
            assert (output / doc['file']).read_bytes() == (source[0] / doc['source_path']).read_bytes()


def test_default_rejects_untrusted_manifest(source, tmp_path):
    with pytest.raises(ValueError, match='manifest.*digest'):
        importlib.import_module(MODULE).build_release(source[0], tmp_path / 'release')
    assert not (tmp_path / 'release').exists()


def test_rejects_changed_frozen_page(source, tmp_path):
    (source[0] / KB / 'README.md').write_text('# changed')
    with pytest.raises(ValueError, match='source.*digest'):
        build(source, tmp_path / 'release')
    assert not (tmp_path / 'release').exists()


@pytest.mark.parametrize('path', ['../escape.md', '/tmp/escape.md', 'a/../../escape.md', r'a\escape.md'])
def test_rejects_manifest_path_escape(source, tmp_path, path):
    root, entries, _ = source
    entries.append({'path': path, 'sha256': '0' * 64})
    source = root, entries, write_manifest(root, entries)
    with pytest.raises(ValueError, match='path'):
        build(source, tmp_path / 'release')
    assert not (tmp_path / 'release').exists()


@pytest.mark.parametrize('ancestor', [False, True])
def test_rejects_symlink_sources_even_inside_root(source, tmp_path, ancestor):
    root, _, _ = source
    if ancestor:
        folder = root / KB / '02-public-api'
        real = folder.with_name('real-api')
        folder.rename(real)
        folder.symlink_to(real, target_is_directory=True)
    else:
        page = root / KB / 'README.md'
        real = page.with_name('real.md')
        page.rename(real)
        page.symlink_to(real)
    with pytest.raises(ValueError, match='symlink'):
        build(source, tmp_path / 'release')


def test_rejects_duplicate_id_and_leaves_no_partial_output(source, tmp_path):
    root, entries, _ = source
    raw = (root / KB / '02-public-api/KB-API-001.md').read_bytes()
    store(root, KB / 'duplicate.md', raw)
    entries.append({'path': 'duplicate.md', 'sha256': digest(raw)})
    source = root, entries, write_manifest(root, entries)
    with pytest.raises(ValueError, match='duplicate.*ID'):
        build(source, tmp_path / 'release')
    assert not (tmp_path / 'release').exists()
    assert not list(tmp_path.glob('.release.*'))


def test_refuses_existing_output_without_modifying_it(source, tmp_path):
    output = tmp_path / 'release'
    output.mkdir()
    (output / 'marker').write_text('keep')
    with pytest.raises(FileExistsError):
        build(source, output)
    assert (output / 'marker').read_text() == 'keep'
    assert list(output.iterdir()) == [output / 'marker']


def test_atomic_publish_never_replaces_concurrent_empty_directory(source, tmp_path, monkeypatch):
    mod = importlib.import_module(MODULE)
    publish = mod._publish_directory
    output = tmp_path / 'release'
    def concurrent_writer(staging, target):
        target.mkdir()
        return publish(staging, target)
    monkeypatch.setattr(mod, '_publish_directory', concurrent_writer)
    with pytest.raises(FileExistsError):
        build(source, output)
    assert list(output.iterdir()) == []
    assert not list(tmp_path.glob('.release.*'))


def test_refuses_output_inside_frozen_sources(source):
    with pytest.raises(ValueError, match='output'):
        build(source, source[0] / KB / 'new-release')


def test_curated_missing_fails_before_publication(source, tmp_path):
    (source[0] / CURATED[0][1]).unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        build(source, tmp_path / 'release')
    assert not (tmp_path / 'release').exists()


@pytest.mark.parametrize('document_id', ['../escape', '.hidden', 'a/b', 'a\\b', 'X' * 161, ''])
def test_rejects_ids_that_cannot_be_safe_document_names(source, tmp_path, document_id):
    root, entries, _ = source
    path = root / KB / '02-public-api/KB-API-001.md'
    raw = path.read_bytes().replace(b'id: KB-API-001\n', ('id: ' + json.dumps(document_id) + '\n').encode())
    path.write_bytes(raw)
    entries[0]['sha256'] = digest(raw)
    with pytest.raises(ValueError, match='document ID'):
        build((root, entries, write_manifest(root, entries)), tmp_path / 'release')


def test_rejects_duplicate_frozen_source_path(source, tmp_path):
    root, entries, _ = source
    entries.append(entries[0].copy())
    with pytest.raises(ValueError, match='duplicate source path'):
        build((root, entries, write_manifest(root, entries)), tmp_path / 'release')


def test_rejects_curated_source_symlink(source, tmp_path):
    root, _, _ = source
    path = root / CURATED[0][1]
    real = path.with_suffix('.original.md')
    path.rename(real)
    path.symlink_to(real)
    with pytest.raises(ValueError, match='symlink'):
        build(source, tmp_path / 'release')


def test_staging_write_failure_does_not_publish_a_partial_package(source, tmp_path, monkeypatch):
    original = Path.write_bytes
    def fail_document_write(path, data):
        if path.parent.name == 'documents':
            raise OSError('simulated full disk')
        return original(path, data)
    monkeypatch.setattr(Path, 'write_bytes', fail_document_write)
    with pytest.raises(OSError, match='simulated full disk'):
        build(source, tmp_path / 'release')
    assert not (tmp_path / 'release').exists()
    assert not list(tmp_path.glob('.release.*'))


def test_existing_symlink_output_cannot_redirect_publication(source, tmp_path):
    output = tmp_path / 'release'
    target = tmp_path / 'external'
    target.mkdir()
    output.symlink_to(target, target_is_directory=True)
    with pytest.raises(FileExistsError):
        build(source, output)
    assert output.is_symlink()
    assert list(target.iterdir()) == []


def test_source_count_and_runtime_claims_fail_closed(source, tmp_path):
    root, entries, _ = source
    path = root / KB / '02-public-api/KB-API-001.md'
    raw = path.read_bytes().replace(b'runtime_validation: not_run', b'runtime_validation: passed')
    path.write_bytes(raw)
    entries[0]['sha256'] = digest(raw)
    with pytest.raises(ValueError, match='metadata inconsistent'):
        build((root, entries, write_manifest(root, entries)), tmp_path / 'release')


def test_acceptance_report_does_not_claim_its_own_independent_review(source, tmp_path):
    output = tmp_path / 'release'
    build(source, output)
    documents = json.loads((output / 'manifest.json').read_bytes())['documents']
    acceptance = next(document for document in documents if document['id'] == 'DOC-KB-ACCEPTANCE')
    assert acceptance['content_state'] == 'accepted_static'
    assert acceptance['review_status'] == 'acceptance_report'


@pytest.mark.parametrize('pages', [
    [{'page_id': 'KB-API-999', 'path': '02-public-api/KB-API-001.md'}],
    [{'page_id': 'KB-API-001', 'path': '02-public-api/different.md'}],
    [{'page_id': 'KB-API-001', 'path': 'README.md'}],
    [],
])
def test_rejects_formal_page_identity_mismatch_even_with_equal_count(source, tmp_path, pages):
    root, entries, _ = source
    expected = write_manifest(root, entries, pages=pages)
    with pytest.raises(ValueError, match='page identit'):
        build((root, entries, expected), tmp_path / 'release')
    assert not (tmp_path / 'release').exists()


@pytest.mark.parametrize('duplicate', ['pair', 'id', 'path'])
def test_rejects_duplicate_frozen_formal_page_identity(source, tmp_path, duplicate):
    root, entries, _ = source
    first = {'page_id': 'KB-API-001', 'path': '02-public-api/KB-API-001.md'}
    second = first.copy()
    if duplicate == 'id':
        second['path'] = '02-public-api/another.md'
    elif duplicate == 'path':
        second['page_id'] = 'KB-API-002'
    expected = write_manifest(root, entries, pages=[first, second])
    with pytest.raises(ValueError, match='duplicate.*page identit'):
        build((root, entries, expected), tmp_path / 'release')
    assert not (tmp_path / 'release').exists()


def test_integer_page_count_cannot_replace_bound_page_identities(source, tmp_path):
    root, entries, _ = source
    expected = write_manifest(root, entries, pages=1)
    with pytest.raises(ValueError, match='page identit'):
        build((root, entries, expected), tmp_path / 'release')
    assert not (tmp_path / 'release').exists()
