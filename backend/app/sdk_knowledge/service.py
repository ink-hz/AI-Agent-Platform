"""Join observed changes to reviewed source references; never infer test success."""
from __future__ import annotations

from copy import deepcopy


def _overlaps(start: int, end: int, changed_start: int, count: int) -> bool:
    # A zero-line hunk is an insertion boundary, potentially affecting its neighbors.
    return start <= changed_start + max(count, 1) - 1 and changed_start <= end


class KnowledgeService:
    def __init__(self, corpus, source):
        self.corpus, self.source = corpus, source

    def inspect_change(self, repository: str, base_commit: str, target_commit: str) -> dict:
        if self.source is None:
            raise ValueError('source_not_configured')
        observation = self.source.compare(repository, base_commit, target_commit)
        pages, unmapped = {}, []
        for changed in observation['files']:
            matches = self.corpus.source_pages(repository, changed['path'])
            if not matches:
                unmapped.append(changed['path'])
            for page in matches:
                pages[page['page_id']] = page
        ids = sorted(pages)
        selected, omitted = ids[:20], ids[20:]
        changed_files = {row['path']: row for row in observation['files']}
        observed, affected = {}, []
        for page_id in selected:
            page = deepcopy(pages[page_id])
            checks, changed_references = [], []
            for evidence in page['evidence']:
                source_path, source_repo = evidence['path'], evidence.get('repository')
                check = {'evidence_id': evidence['evidence_id'], 'repository': source_repo,
                         'path': source_path, 'baseline_commit': evidence.get('commit'),
                         'target_commit': target_commit if source_repo == repository else None,
                         'expected_sha256': evidence.get('sha256'), 'observed_sha256': None,
                         'state': 'not_observed'}
                if source_repo == repository and evidence.get('source_root', 'sdk_source') == 'sdk_source':
                    if source_path not in observed:
                        if len(observed) >= 100:
                            raise ValueError('source_scope_too_large')
                        observed[source_path] = self.source.blob_sha256(repository, target_commit, source_path)
                    current = observed[source_path]
                    check.update(observed_sha256=current, state='source_missing' if current is None else
                                 'content_matches' if current == evidence.get('sha256') else 'content_changed')
                checks.append(check)
                changed = changed_files.get(source_path) if source_repo == repository else None
                if changed is not None:
                    expected = evidence.get('sha256')
                    basis = ('target_commit' if expected == changed['new_sha256'] else
                             'base_commit' if expected == changed['old_sha256'] else 'unavailable')
                    line_overlap = None
                    if basis != 'unavailable' and not changed.get('binary'):
                        side = 'new' if basis == 'target_commit' else 'old'
                        line_overlap = any(_overlaps(evidence['line_start'], evidence['line_end'],
                                                     h[f'{side}_start'], h[f'{side}_lines'])
                                           for h in changed['hunks'])
                    changed_references.append({'evidence_id': evidence['evidence_id'], 'path': source_path,
                                               'line_basis': basis, 'line_overlap': line_overlap,
                                               'cited_file_changed': changed['old_sha256'] != changed['new_sha256']})
            states = {row['state'] for row in checks}
            knowledge_state = ('needs_revalidation' if states & {'content_changed', 'source_missing'} else
                               'cited_content_matches_target' if states == {'content_matches'} else
                               'partially_observed')
            affected.append({**page, 'source_checks': checks, 'changed_references': changed_references,
                             'knowledge_state': knowledge_state})
        return {'schema_version': 1, 'baseline_id': self.corpus.baseline_id,
                'observation': observation, 'affected_pages': affected,
                'runtime_validation': 'not_run',
                'coverage': {'relation': 'direct_reviewed_source_references',
                             'indirect_dependencies': 'not_computed', 'other_repositories': 'not_observed',
                             'unmapped_paths': unmapped, 'omitted_page_ids': omitted,
                             'matched_pages': len(ids), 'returned_pages': len(affected)}}
