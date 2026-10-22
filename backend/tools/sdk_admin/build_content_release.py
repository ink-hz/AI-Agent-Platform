"""Export the hash-bound SDK reading snapshot into a new immutable release.

No SDK execution, remote access or source writes. Run from backend with:
python -m tools.sdk_admin.build_content_release --platform-root PATH --output PATH
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import sys
import tempfile
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import yaml

FROZEN_MANIFEST = Path('artifacts/sdk-platform/knowledge-base/semantic-2026-10-22/reviews/final-review-input-manifest.json')
EXPECTED_MANIFEST_SHA256 = '34d378c35118f151caf109910d5abc3af92e1062b6f59f06d437dcdf7c8dc260'
KNOWLEDGE_ROOT = Path('sdk源码/OpenOrbbecSDKGroup/docs/knowledge-base')
ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}\Z')
SHA_PATTERN = re.compile(r'[0-9a-f]{64}\Z')
# The nine selected sources are deliberately explicit, never discovered by glob.
CURATED = (
    ('DOC-PLATFORM-PROCESS', 'docs/sdk-platform/03-平台建设方案.md', 'process', 'design_proposal'),
    ('DOC-OBJECT-STATES', 'docs/sdk-platform/design/B-01-对象与状态映射.md', 'process', 'design_proposal'),
    ('DOC-RELATION-RULES', 'docs/sdk-platform/design/B-01-关联ID与冲突规则.md', 'process', 'design_proposal'),
    ('DOC-ANALYSIS-STATUS', 'docs/sdk-platform/analysis/阶段性现状盘点-2026-10-22.md', 'analysis', 'phase_report'),
    ('DOC-ANALYSIS-GIT', 'docs/sdk-platform/analysis/Git变更覆盖与需求节奏复核-2026-10-22.md', 'analysis', 'phase_report'),
    ('DOC-KB-ACCEPTANCE', 'artifacts/sdk-platform/knowledge-base/semantic-2026-10-22/final-acceptance.md', 'acceptance', 'accepted_static'),
    ('DOC-PORTAL-DESIGN', 'docs/sdk-platform/design/B-04-sdk-admin功能架构与智能化设计.md', 'design', 'design_proposal'),
    ('DOC-SDK-R0-RELEASE', 'docs/sdk-platform/execution/2026-10-22-sdk-r0-production/上线记录.md', 'execution', 'execution_record'),
    ('DOC-EXECUTION-LEDGER', 'docs/sdk-platform/任务执行台账.md', 'execution', 'execution_record'),
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_relative(value: str) -> Path:
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise ValueError('invalid source path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('', '.', '..') for part in value.split('/')):
        raise ValueError('invalid source path')
    return Path(*path.parts)


def _read_source(root: Path, relative: Path) -> bytes:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('source symlink is forbidden')
    if not current.is_file():
        raise ValueError('source file unavailable')
    return current.read_bytes()


def _parse_markdown(raw: bytes) -> tuple[dict, str]:
    text = raw.decode('utf-8')
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip('\r\n') != '---':
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].rstrip('\r\n') == '---':
            metadata = yaml.safe_load(''.join(lines[1:index]))
            if not isinstance(metadata, dict):
                raise ValueError('invalid YAML metadata')
            return metadata, ''.join(lines[index + 1:])
    raise ValueError('unterminated YAML metadata')


def _title(metadata: dict, body: str) -> str:
    value = metadata.get('title')
    if isinstance(value, str) and value.strip():
        return value
    heading = re.search(r'^#{1,6}\s+(.+?)\s*#*\s*$', body, re.MULTILINE)
    if not heading:
        raise ValueError('document title missing')
    return heading.group(1)


def _navigation_id(path: Path) -> str:
    if path.as_posix() == '_meta/README.md':
        return 'KB-META-README'
    if path.name != 'README.md':
        raise ValueError('knowledge document ID missing')
    suffix = 'ROOT' if path.parent == Path('.') else path.parent.as_posix().replace('/', '-').upper()
    return 'KB-INDEX-' + suffix


def _document(raw: bytes, source_path: Path, *, baseline_id: str, kind: str,
              group: str, document_id: str | None = None, content_state: str | None = None) -> tuple[dict, str]:
    metadata, body = _parse_markdown(raw)
    formal = kind == 'knowledge' and 'id' in metadata
    if formal:
        if (metadata.get('status') != 'source_reviewed'
                or metadata.get('semantic_review') != 'independently_reviewed'
                or metadata.get('runtime_validation') != 'not_run'
                or metadata.get('baseline_id') != baseline_id):
            raise ValueError('frozen knowledge metadata inconsistent')
        document_id = metadata['id']
        content_state = 'accepted_static'
    elif kind == 'knowledge':
        document_id = _navigation_id(source_path.relative_to(KNOWLEDGE_ROOT))
        content_state = 'navigation'
    if not isinstance(document_id, str) or not ID_PATTERN.fullmatch(document_id):
        raise ValueError('invalid document ID')
    conditions = metadata.get('conditions', {})
    if not isinstance(conditions, dict):
        raise ValueError('invalid document conditions')
    # Reject non-JSON conditions rather than silently dropping or rewriting them.
    json.dumps(conditions, allow_nan=False)
    return {
        'id': document_id, 'title': _title(metadata, body), 'kind': kind, 'group': group,
        'level': metadata.get('level'),
        'baseline_id': baseline_id if kind == 'knowledge' or kind == 'acceptance' else metadata.get('baseline_id'),
        'content_state': content_state, 'runtime_validation': 'not_run',
        'source_path': source_path.as_posix(), 'source_sha256': _sha256(raw),
        'review_status': ('independently_reviewed' if formal else 'acceptance_report' if kind == 'acceptance'
                          else 'not_applicable' if kind == 'knowledge' else 'source_record'),
        'file': f'documents/{document_id}.md', 'sha256': _sha256(body.encode('utf-8')),
        'conditions': conditions, 'links': {},
    }, body


def _markdown_destinations(body: str) -> set[str]:
    """Collect inline and reference destinations without rewriting reading text.

    Balanced parentheses support source paths such as foo(bar).md. Angle quoted
    destinations and reference definitions preserve the href used by Markdown.
    """
    destinations = set()
    starts = [match.end() for match in re.finditer(r'!?\[[^\]\n]*\]\(\s*', body)]
    starts += [match.end() for match in re.finditer(r'^ {0,3}\[[^\]\n]+\]:\s*', body, re.MULTILINE)]
    for start in starts:
        if start >= len(body):
            continue
        if body[start] == '<':
            end = body.find('>', start + 1)
            if end != -1:
                destinations.add(body[start + 1:end])
            continue
        depth, index = 0, start
        while index < len(body):
            char = body[index]
            if char == '\\' and index + 1 < len(body):
                index += 2
                continue
            if char == '(':
                depth += 1
            elif char == ')':
                if depth == 0:
                    break
                depth -= 1
            elif char.isspace() and depth == 0:
                break
            index += 1
        if index > start:
            destinations.add(re.sub(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\]\\^_`{|}~])', r'\1', body[start:index]))
    return {html.unescape(value) for value in destinations}


def _links(body: str, source_path: str, source_ids: dict[str, str]) -> dict:
    result = {}
    for href in sorted(_markdown_destinations(body)):
        try:
            parsed = urlsplit(href)
        except ValueError:
            continue
        if parsed.scheme or parsed.netloc or parsed.query or parsed.path.startswith('/') or '\\' in parsed.path:
            continue
        target = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), unquote(parsed.path))) if parsed.path else source_path
        if target in source_ids:
            result[href] = {'document_id': source_ids[target], 'fragment': unquote(parsed.fragment)}
    return result


def _publish_directory(staging: Path, output: Path) -> None:
    """Atomic no-replace directory rename on the two supported dev platforms."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(staging), os.fsencode(output), 4)  # RENAME_EXCL
    elif sys.platform.startswith('linux'):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, os.fsencode(staging), -100, os.fsencode(output), 1)  # RENAME_NOREPLACE
    else:
        raise OSError('atomic no-replace publication unsupported on this platform')
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def build_release(platform_root: Path, output: Path, *,
                  expected_manifest_sha256: str = EXPECTED_MANIFEST_SHA256) -> dict:
    """Validate selected immutable inputs and publish once; return release summary.

    expected_manifest_sha256 permits small synthetic fixtures. CLI always uses
    the reviewed production digest; it has no option to bypass that trust root.
    """
    root = Path(platform_root).resolve(strict=True)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError('output already exists')
    output = output.parent.resolve() / output.name
    if output.is_relative_to(root / KNOWLEDGE_ROOT):
        raise ValueError('output cannot be inside frozen knowledge sources')
    frozen_raw = _read_source(root, FROZEN_MANIFEST)
    if not SHA_PATTERN.fullmatch(expected_manifest_sha256) or _sha256(frozen_raw) != expected_manifest_sha256:
        raise ValueError('frozen manifest digest mismatch')
    frozen = json.loads(frozen_raw)
    baseline_id = frozen.get('baseline_id')
    entries = frozen.get('knowledge_base_files')
    if not isinstance(baseline_id, str) or not baseline_id or not isinstance(entries, list):
        raise ValueError('invalid frozen manifest')
    pages = frozen.get('pages')
    if not isinstance(pages, list) or not pages:
        raise ValueError('invalid frozen page identities')
    expected_identities, expected_ids, expected_paths = set(), set(), set()
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get('page_id'), str):
            raise ValueError('invalid frozen page identity')
        page_id = page['page_id']
        page_path = _safe_relative(page.get('path')).as_posix()
        if not ID_PATTERN.fullmatch(page_id) or not page_path.endswith('.md'):
            raise ValueError('invalid frozen page identity')
        if page_id in expected_ids or page_path in expected_paths:
            raise ValueError('duplicate frozen page identity')
        expected_ids.add(page_id)
        expected_paths.add(page_path)
        expected_identities.add((page_id, page_path))
    documents, bodies, source_ids, seen_paths = [], {}, {}, set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('invalid frozen manifest entry')
        relative = _safe_relative(entry.get('path'))
        if relative.as_posix() in seen_paths:
            raise ValueError('duplicate source path')
        seen_paths.add(relative.as_posix())
        if relative.suffix != '.md':
            continue
        source_path = KNOWLEDGE_ROOT / relative
        raw = _read_source(root, source_path)
        if not isinstance(entry.get('sha256'), str) or not SHA_PATTERN.fullmatch(entry['sha256']) or _sha256(raw) != entry['sha256']:
            raise ValueError('source digest mismatch')
        group = relative.parts[0] if len(relative.parts) > 1 else 'root'
        document, body = _document(raw, source_path, baseline_id=baseline_id, kind='knowledge', group=group)
        documents.append(document)
        if document['id'] in bodies:
            raise ValueError('duplicate document ID')
        bodies[document['id']] = body
        source_ids[source_path.as_posix()] = document['id']
    formal_count = sum(d['content_state'] == 'accepted_static' for d in documents)
    navigation_count = len(documents) - formal_count
    actual_identities = {
        (document['id'], Path(document['source_path']).relative_to(KNOWLEDGE_ROOT).as_posix())
        for document in documents if document['content_state'] == 'accepted_static'
    }
    if formal_count != len(pages) or actual_identities != expected_identities:
        raise ValueError('frozen knowledge page identity mismatch')
    for document_id, path, kind, state in CURATED:
        source_path = _safe_relative(path)
        document, body = _document(_read_source(root, source_path), source_path, baseline_id=baseline_id,
                                   kind=kind, group=kind, document_id=document_id, content_state=state)
        if document_id in bodies:
            raise ValueError('duplicate document ID')
        documents.append(document)
        bodies[document_id] = body
        source_ids[path] = document_id
    documents.sort(key=lambda document: document['id'])
    for document in documents:
        document['links'] = _links(bodies[document['id']], document['source_path'], source_ids)
    generated_at = datetime.now(timezone.utc).isoformat()
    release_id = 'sdk-admin-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex[:12]
    manifest = {
        'schema_version': 1, 'release_id': release_id, 'baseline_id': baseline_id, 'generated_at': generated_at,
        'documents': documents,
        'sources': [
            {'id': 'knowledge', 'label': 'SDK 源码知识库', 'state': 'accepted_static', 'description': f'{formal_count} 篇正式页及 {navigation_count} 篇导航，锁定源码静态独立复核；设备与运行验证 not_run。'},
            {'id': 'process', 'label': '流程与对象规则', 'state': 'design_proposal', 'description': '建设方案与 B-01 离线契约草案，可阅读；全链业务映射和运行尚待验证。'},
            {'id': 'analysis', 'label': '历史过程分析', 'state': 'phase_report', 'description': '阶段性现状及 Git 覆盖复核；不能推为完整效率评价或历史完成率。'},
            {'id': 'zentao', 'label': '禅道工作区', 'state': 'instance_not_verified', 'description': '保留现有工作区入口；当前实例、工作身份和权限仍需校准。', 'href': '/sdk/'},
            {'id': 'sdk-r0', 'label': 'R0 有限发布记录', 'state': 'limited_release_record', 'description': '记录特定后端模块发布；不能替代真实采集链、构建、设备与本门户生产验收。'},
            {'id': 'technical-diagnostics', 'label': 'SDK 技术诊断', 'state': 'not_registered', 'description': '已有并行工作尚未定位稳定成果入口，待登记版本、验证范围与责任工作线。'},
        ],
        'ai': {'available': False, 'reason': 'SDK Agent 注册、授权知识检索、引用及完整任务质量验证尚未接通；本期不调用真实模型。'},
        'featured_ids': [value for value in ['KB-INDEX-ROOT', 'KB-NAV-004', 'KB-SYS-001', 'DOC-PLATFORM-PROCESS', 'DOC-ANALYSIS-STATUS', 'DOC-KB-ACCEPTANCE'] if value in bodies],
    }
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')
    manifest_sha = _sha256(manifest_raw)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.' + output.name + '.', dir=output.parent))
    try:
        (staging / 'documents').mkdir()
        for document in documents:
            (staging / document['file']).write_bytes(bodies[document['id']].encode('utf-8'))
        (staging / 'manifest.json').write_bytes(manifest_raw)
        (staging / 'manifest.sha256').write_text(manifest_sha + '\n', encoding='ascii')
        _publish_directory(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {
        'release_id': release_id, 'baseline_id': baseline_id, 'generated_at': generated_at,
        'manifest_sha256': manifest_sha,
        'counts': {'knowledge_pages': formal_count, 'knowledge_navigation': navigation_count, 'documents': len(documents)},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    try:
        summary = build_release(arguments.platform_root, arguments.output)
    except (ValueError, OSError, yaml.YAMLError) as error:
        parser.exit(1, f'Content release failed: {error}\n')
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
