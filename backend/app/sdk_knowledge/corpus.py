"""Compile pinned semantic records and retrieve lexical evidence candidates.

Integrity checks do not establish semantic truth or device support. No source-side
scripts, app startup, network access, or source filesystem writes are performed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


class KnowledgeUnavailable(ValueError):
    """The configured knowledge package is absent, unsafe, or inconsistent."""


_KB = 'sdk源码/OpenOrbbecSDKGroup/docs/knowledge-base'
_ART = 'artifacts/sdk-platform/knowledge-base/semantic-2026-10-22'
_ACCEPTED = {'accepted', 'passed', 'accepted_with_external_boundaries'}
_PAGE_FIELDS = ('page_id', 'title', 'path', 'body_sha256', 'semantic_sha256',
                'review_status', 'conditions', 'claims', 'evidence', 'unknowns',
                'source_files_reviewed', 'runtime_validation')
_MAX_CLAIMS = 8
_MAX_EVIDENCE = 32
_MAX_QUERY_LENGTH = 200
_MAX_QUERY_TERMS = 64


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any, *, dot: bool = False) -> str:
    if dot and value == '.':
        return value
    if (not isinstance(value, str) or not value or '\\' in value or '\x00' in value
            or value.startswith('/') or any(p in ('', '.', '..') for p in value.split('/'))):
        raise KnowledgeUnavailable('Unsafe knowledge package path')
    return str(PurePosixPath(value))


def _safe_read(root: Path, relative: str) -> bytes:
    """Walk directories with openat/NOFOLLOW; reject symlinks including parents."""
    relative = _canonical(relative)
    absolute = Path(os.path.abspath(root))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:] + tuple(relative.split('/')[:-1]):
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        leaf = os.open(relative.split('/')[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(leaf, 'rb') as stream:
            import stat
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise KnowledgeUnavailable('Knowledge input must be a regular file')
            return stream.read()
    except OSError:
        raise KnowledgeUnavailable('Knowledge input missing or unsafe') from None
    finally:
        os.close(fd)


def _json(raw: bytes) -> Any:
    return json.loads(raw.decode('utf-8'))


def _semantic_sha(record: dict) -> str:
    return _sha(json.dumps({k: v for k, v in record.items() if k != 'review_records'},
                           sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())


def _pinned_files(root: Path, entries: list) -> dict[str, bytes]:
    files = {}
    for entry in entries:
        path = _canonical(entry['path'])
        if path in files:
            raise KnowledgeUnavailable('Duplicate frozen input path')
        raw = _safe_read(root, path)
        if _sha(raw) != entry['sha256']:
            raise KnowledgeUnavailable('Frozen knowledge input changed')
        files[path] = raw
    return files


def build_index(platform_root: Path, *, manifest_path: Path,
                expected_manifest_sha256: str) -> dict:
    """Build version 1 only from caller-pinned frozen formal page entries."""
    try:
        platform_root = Path(os.path.abspath(platform_root))
        manifest_path = Path(os.path.abspath(manifest_path))
        relative = manifest_path.relative_to(platform_root).as_posix()
        raw = _safe_read(platform_root, relative)
        if _sha(raw) != expected_manifest_sha256:
            raise KnowledgeUnavailable('Knowledge manifest digest mismatch')
        manifest = _json(raw)
        baseline_id = manifest['baseline_id']
        kb = _pinned_files(platform_root / _KB, manifest['knowledge_base_files'])
        artifacts = _pinned_files(platform_root / _ART, manifest['artifacts'])
        baseline = _json(kb['_meta/baseline.json'])
        if baseline['baseline_id'] != baseline_id:
            raise KnowledgeUnavailable('Knowledge baseline mismatch')
        repositories = [{'repository': r['repository'],
                         'relative_path': _canonical(r['relative_path'], dot=True),
                         'commit': r['head']} for r in baseline['repositories']]
        pages = []
        ids = set()
        paths = set()
        for formal in manifest['pages']:
            pid = formal['page_id']
            path = _canonical(formal['path'])
            if not isinstance(pid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', pid):
                raise KnowledgeUnavailable('Invalid formal page identity')
            if pid in ids or path in paths:
                raise KnowledgeUnavailable('Duplicate formal page identity')
            ids.add(pid)
            paths.add(path)
            text = kb[path].decode('utf-8')
            parts = text.split('---', 2)
            if len(parts) != 3 or parts[0] or not text.startswith('---\n'):
                raise KnowledgeUnavailable('Formal page frontmatter missing')
            meta = yaml.safe_load(parts[1])
            semantic_path = f'_meta/semantic-pages/{pid}.json'
            if meta.get('semantic_record') != semantic_path:
                raise KnowledgeUnavailable('Semantic record identity mismatch')
            record = _json(kb[semantic_path])
            body_sha = _sha(parts[2].encode())
            semantic_sha = _semantic_sha(record)
            if (meta['id'] != pid or record['page_id'] != pid or record['path'] != path
                    or meta['baseline_id'] != baseline_id or record['baseline_id'] != baseline_id
                    or formal['body_sha256'] != body_sha or formal['semantic_sha256'] != semantic_sha):
                raise KnowledgeUnavailable('Formal page binding changed')
            reviews = record['review_records']
            if meta.get('review_records') != reviews:
                raise KnowledgeUnavailable('Independent review metadata mismatch')
            accepted = [review for review in reviews if review.get('result') in _ACCEPTED]
            if not accepted or not set(formal['reviewers']).issubset({r['reviewer'] for r in accepted}):
                raise KnowledgeUnavailable('Independent review binding missing')
            for review in accepted:
                artifact_path = _canonical(review['review_artifact'])
                artifact = artifacts[artifact_path]
                if (review['reviewed_body_sha256'] != body_sha
                        or review['reviewed_semantic_sha256'] != semantic_sha
                        or _sha(artifact) != review['review_artifact_sha256']):
                    raise KnowledgeUnavailable('Independent review binding changed')
            if meta.get('conditions') != record['conditions']:
                raise KnowledgeUnavailable('Page conditions inconsistent')
            pages.append({'page_id': pid, 'title': meta['title'], 'path': path,
                          'body_sha256': body_sha, 'semantic_sha256': semantic_sha,
                          'review_status': meta['semantic_review'],
                          'runtime_validation': meta['runtime_validation'],
                          **{field: record[field] for field in ('conditions', 'claims', 'evidence',
                                                              'unknowns', 'source_files_reviewed')}})
        bundle = {'schema_version': 1, 'baseline_id': baseline_id,
                  'input_manifest_sha256': expected_manifest_sha256,
                  'repositories': repositories, 'pages': pages}
        KnowledgeCorpus(bundle)
        return bundle
    except KnowledgeUnavailable:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError):
        raise KnowledgeUnavailable('Knowledge package invalid or incomplete') from None


class KnowledgeCorpus:
    """In-memory lexical retrieval; no authorization is stored or implied here."""

    def __init__(self, bundle: dict):
        try:
            if bundle['schema_version'] != 1 or not isinstance(bundle['baseline_id'], str):
                raise KnowledgeUnavailable('Unsupported knowledge bundle')
            self._bundle = copy.deepcopy(bundle)
            self._pages = {}
            self._sources: dict[tuple[str, str], list[dict]] = {}
            self._repositories = self._bundle['repositories']
            names = set()
            for repository in self._repositories:
                name = repository['repository']
                if name in names or not re.fullmatch(r'[0-9a-f]{40}', repository['commit']):
                    raise KnowledgeUnavailable('Invalid repository baseline')
                _canonical(repository['relative_path'], dot=True)
                names.add(name)
            all_claim_ids = set()
            for page in self._bundle['pages']:
                if any(field not in page for field in _PAGE_FIELDS):
                    raise KnowledgeUnavailable('Incomplete knowledge page')
                pid = page['page_id']
                _canonical(page['path'])
                if pid in self._pages:
                    raise KnowledgeUnavailable('Duplicate knowledge page')
                if not all(isinstance(page[f], list) for f in ('claims', 'evidence', 'unknowns', 'source_files_reviewed')):
                    raise KnowledgeUnavailable('Invalid semantic page fields')
                if not isinstance(page['conditions'], dict):
                    raise KnowledgeUnavailable('Invalid page conditions')
                self._pages[pid] = page
                evidence_ids = {e['evidence_id'] for e in page['evidence']}
                if len(evidence_ids) != len(page['evidence']):
                    raise KnowledgeUnavailable('Duplicate evidence identity')
                claim_ids = [claim['claim_id'] for claim in page['claims']]
                if len(claim_ids) != len(set(claim_ids)) or all_claim_ids.intersection(claim_ids):
                    raise KnowledgeUnavailable('Duplicate claim identity')
                all_claim_ids.update(claim_ids)
                for claim in page['claims']:
                    if not set(claim['evidence_ids']).issubset(evidence_ids):
                        raise KnowledgeUnavailable('Unresolved claim evidence')
                page_sources = set()
                for evidence in page['evidence']:
                    source = (evidence['repository'], _canonical(evidence['path']))
                    if source not in page_sources:
                        self._sources.setdefault(source, []).append(page)
                        page_sources.add(source)
        except KnowledgeUnavailable:
            raise
        except (TypeError, KeyError, ValueError, AttributeError):
            raise KnowledgeUnavailable('Invalid knowledge bundle') from None

    @property
    def baseline_id(self) -> str:
        return self._bundle['baseline_id']

    @property
    def repositories(self) -> list[dict]:
        return copy.deepcopy(self._repositories)

    @classmethod
    def from_file(cls, path: Path, *, expected_sha256: str):
        try:
            path = Path(os.path.abspath(path))
            raw = _safe_read(path.parent, path.name)
            if _sha(raw) != expected_sha256:
                raise KnowledgeUnavailable('Knowledge bundle digest mismatch')
            return cls(_json(raw))
        except KnowledgeUnavailable:
            raise
        except (ValueError, OSError, TypeError):
            raise KnowledgeUnavailable('Knowledge bundle unavailable') from None

    def page(self, page_id: str) -> dict | None:
        return copy.deepcopy(self._pages.get(page_id))

    def source_pages(self, repository: str, path: str) -> list[dict]:
        return copy.deepcopy(self._sources.get((repository, path), []))

    def search(self, q: str = '', *, repository: str | None = None,
               path: str | None = None, symbol: str | None = None,
               device: str | None = None, platform: str | None = None,
               limit: int = 10) -> dict:
        """Exact path/condition filters; casefold lexical query candidates only.

        Generic conditions remain explicit; they do not become device support
        assertions. Latin identifiers are mandatory literal tokens, while CJK
        pairs allow partial recall weighted by frequency, headings and coverage.
        Queries exceeding 200 characters or 64 distinct Latin/CJK terms are
        rejected before corpus scanning; no query terms are silently discarded.
        Limits also bound pages and per-page claim/evidence fan-out.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('limit must be between 1 and 100')
        if not isinstance(q, str) or len(q) > _MAX_QUERY_LENGTH:
            raise ValueError('knowledge_query_too_complex')
        latin_terms, cjk_terms, phrases = _query_terms(q)
        if len(latin_terms) + len(cjk_terms) > _MAX_QUERY_TERMS:
            raise ValueError('knowledge_query_too_complex')
        documents = []
        for page in self._pages.values():
            conditions = page['conditions']
            if device is not None and not _condition_matches(conditions.get('devices'), device):
                continue
            if platform is not None and not _condition_matches(conditions.get('platforms'), platform):
                continue
            candidates = [e for e in page['evidence'] if
                          (repository is None or e['repository'] == repository)
                          and (path is None or e['path'] == path)
                          and (symbol is None or symbol.casefold() in str(e.get('symbol', '')).casefold())]
            if any(value is not None for value in (repository, path, symbol)) and not candidates:
                continue
            heading = ' '.join(str(page[k]) for k in ('page_id', 'title', 'path')).casefold()
            evidence = {e['evidence_id']: e for e in candidates}
            claim_texts = []
            for claim in page['claims']:
                linked = [evidence[eid] for eid in claim['evidence_ids'] if eid in evidence]
                if any(value is not None for value in (repository, path, symbol)) and not linked:
                    continue
                text = json.dumps([claim, linked], ensure_ascii=False).casefold()
                claim_texts.append((claim, text))
            text = heading + ' ' + ' '.join(t for _, t in claim_texts)
            latin_hits = [term for term in latin_terms if _identifier_matches(term, text)]
            cjk_hits = [term for term in cjk_terms if term in text]
            if len(latin_hits) != len(latin_terms) or (cjk_terms and not cjk_hits):
                continue
            documents.append((page, heading, text, claim_texts, latin_hits, cjk_hits))
        # Query-local document frequency downweights ubiquitous character pairs;
        # no business vocabulary or inferred applicability enters recall.
        frequencies = {term: sum(term in doc[2] for doc in documents) for term in cjk_terms}
        found = []
        for page, heading, text, claim_texts, latin_hits, cjk_hits in documents:
            matching = [claim for claim, claim_text in claim_texts
                        if (any(term in claim_text for term in cjk_terms) if cjk_terms
                            else all(_identifier_matches(term, claim_text) for term in latin_terms))]
            selected = []
            linked_ids = set()
            for claim in matching:
                combined = linked_ids.union(claim['evidence_ids'])
                if len(selected) < _MAX_CLAIMS and len(combined) <= _MAX_EVIDENCE:
                    selected.append(claim)
                    linked_ids = combined
            linked_evidence = [e for e in page['evidence'] if e['evidence_id'] in linked_ids]
            all_matching_ids = {eid for claim in matching for eid in claim['evidence_ids']}
            item = {k: page[k] for k in ('page_id', 'title', 'path', 'body_sha256',
                                        'semantic_sha256', 'review_status', 'conditions', 'runtime_validation')}
            item.update(claims=selected, evidence=linked_evidence,
                        matching_claims_total=len(matching), matching_evidence_total=len(all_matching_ids),
                        truncated=len(selected) < len(matching), retrieval_kind='lexical_candidate')
            phrase_hits = [phrase for phrase in phrases if phrase in text]
            coverage = (len(latin_hits) + len(cjk_hits)) / max(1, len(latin_terms) + len(cjk_terms))
            score = sum(5 * bool(_identifier_matches(term, heading))
                        + min(3, len(re.findall(r'(?<![a-z0-9_])' + re.escape(term)
                                               + r'(?![a-z0-9_])', text))) for term in latin_terms)
            score += sum((1 + math.log((1 + len(documents)) / (1 + frequencies[term])))
                         * (1 + min(2, text.count(term)) * 0.1 + 3 * (term in heading))
                         for term in cjk_hits)
            score += sum(len(phrase) * (1 + 2 * (phrase in heading)) for phrase in phrase_hits)
            score += 4 * coverage
            if q.strip().casefold() == str(page['title']).casefold():
                score += 20
            item['match'] = {'latin_terms': latin_hits, 'cjk_terms': cjk_hits,
                             'cjk_phrases': phrase_hits, 'coverage': round(coverage, 6),
                             'score': round(score, 6)}
            found.append((score, page['page_id'], item))
        found.sort(key=lambda row: (-row[0], row[1]))
        return {'baseline_id': self.baseline_id, 'total': len(found),
                'items': copy.deepcopy([row[2] for row in found[:limit]])}


def _query_terms(q: str) -> tuple[list[str], list[str], list[str]]:
    text = q.casefold()
    latin = re.findall(r'[a-z0-9_]+(?:(?:::|[.:/\-])[a-z0-9_]+)*(?:\+\+|\+|#)?', text)
    segments = re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+', text)
    pairs = [segment[i:i + 2] for segment in segments for i in range(max(1, len(segment) - 1))]
    return (list(dict.fromkeys(latin)), list(dict.fromkeys(pairs)),
            list(dict.fromkeys(segment for segment in segments if len(segment) > 2)))


def _identifier_matches(term: str, text: str) -> bool:
    return bool(re.search(r'(?<![a-z0-9_])' + re.escape(term) + r'(?![a-z0-9_])', text))


def _condition_matches(values: Any, value: str) -> bool:
    if isinstance(values, str):
        values = [values]
    return isinstance(values, list) and any(str(v).casefold() == value.casefold() for v in values)
