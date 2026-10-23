"""Local read-only SDK change acceptance. Never writes source or business state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

import httpx

from app.agent_brain.anthropic_adapter import AnthropicMessagesAdapter
from app.agent_brain.model_adapter import BrainModelError
from app.sdk_knowledge.analysis import AnalysisFailure, ChangeAnalyzer, canonical
from app.sdk_knowledge.corpus import KnowledgeCorpus, KnowledgeUnavailable
from app.sdk_knowledge.git_source import GitSourceReader, SourceObservationError
from app.sdk_knowledge.service import KnowledgeService


class ObservedStream(httpx.SyncByteStream):
    """Observe safe metadata and public tool arguments, never thinking text."""
    def __init__(self, stream, metrics):
        self.stream, self.metrics = stream, metrics

    def __iter__(self):
        pending = b''
        self.metrics.setdefault('usage_status', 'partial')
        for chunk in self.stream:
            pending += chunk
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                if not line.startswith(b'data: '):
                    continue
                try:
                    event = json.loads(line[6:])
                    kind = event.get('type', 'unknown')
                    counts = self.metrics.setdefault('events', {})
                    counts[kind] = counts.get(kind, 0) + 1
                    message = event.get('message', {})
                    if kind == 'message_start':
                        self.metrics['provider_request_id'] = message.get('id')
                    if kind == 'message_delta':
                        self.metrics['stop_reason'] = event.get('delta', {}).get('stop_reason')
                    if kind == 'content_block_start' and event.get('content_block', {}).get('type') == 'tool_use':
                        block = event['content_block']
                        self.metrics.setdefault('public_tool_calls', {})[str(event['index'])] = {
                            'id': block.get('id'), 'name': block.get('name'), 'input_json': ''}
                    if kind == 'content_block_delta' and event.get('delta', {}).get('type') == 'input_json_delta':
                        block = self.metrics.setdefault('public_tool_calls', {}).get(str(event['index']))
                        if block is not None:
                            value = block['input_json'] + event['delta'].get('partial_json', '')
                            block['input_json'] = value[:128 * 1024]
                            block['recording_truncated'] = len(value) > 128 * 1024
                    for source in (message.get('usage', {}), event.get('usage', {})):
                        for key in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'):
                            value = source.get(key)
                            if type(value) is int and value >= 0:
                                self.metrics.setdefault('usage', {})[key] = value
                    if kind == 'message_stop':
                        self.metrics['usage_status'] = 'complete'
                except (ValueError, TypeError, AttributeError):
                    self.metrics['invalid_event_seen'] = True
            yield chunk

    def close(self):
        self.stream.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('index', 'index-sha256', 'source-root', 'repository', 'base-commit', 'target-commit', 'output'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--objective', default='分析该固定提交变更的直接影响、知识过期与回归验证建议。')
    parser.add_argument('--provider-profile', help='Explicit operator-selected Anthropic profile; credentials are never copied.')
    parser.add_argument('--inspect-only', action='store_true')
    args = parser.parse_args(argv)
    started, packet = time.monotonic(), None
    receipt = {'status': 'failed', 'runtime_validation': 'not_run',
               'source_request': {'repository': args.repository, 'base_commit': args.base_commit,
                                  'target_commit': args.target_commit}, 'max_provider_attempts': 1}
    # Reserve evidence before any provider call, preserving failed batches as well.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        try:
            corpus = KnowledgeCorpus.from_file(Path(args.index), expected_sha256=args.index_sha256)
            source = GitSourceReader(Path(args.source_root), corpus.repositories)
            packet = KnowledgeService(corpus, source).inspect_change(**receipt['source_request'])
            receipt.update(source_packet=packet, objective=args.objective,
                           input_sha256=hashlib.sha256(canonical({'objective': args.objective, 'source_packet': packet})).hexdigest(),
                           index_sha256=args.index_sha256)
            if not args.inspect_only:
                if not args.provider_profile:
                    raise ValueError('analysis_provider_profile_required')
                profile = json.loads(Path(args.provider_profile).read_text())
                if profile.get('protocol') != 'anthropic_messages_sse':
                    raise ValueError('analysis_provider_protocol_unsupported')
                endpoint = profile['endpoint']
                if not endpoint.endswith('/v1/messages'):
                    raise ValueError('analysis_provider_endpoint_invalid')
                timeout = profile.get('timeout_seconds', 120)
                if type(timeout) not in (int, float) or not 1 <= timeout <= 600:
                    raise ValueError('analysis_provider_timeout_invalid')
                receipt['configured_timeout_seconds'] = timeout

                def count_request(_request):
                    receipt['provider_http_requests'] = receipt.get('provider_http_requests', 0) + 1

                def observe_response(response):
                    metrics = receipt.setdefault('provider_metrics', {})
                    metrics['http_status'] = response.status_code
                    response.stream = ObservedStream(response.stream, metrics)

                with httpx.Client(timeout=timeout, follow_redirects=False,
                                  event_hooks={'request': [count_request], 'response': [observe_response]}) as client:
                    model = AnthropicMessagesAdapter.from_secret_file(
                        base_url=endpoint.removesuffix('/v1/messages'), api_key_file=profile['credential_file'],
                        auth_scheme=profile.get('auth_scheme', 'x-api-key'), client=client, max_attempts=1)
                    receipt['analysis'] = ChangeAnalyzer(model, model_id=profile['model']).analyze(packet, args.objective)
            receipt['status'] = 'passed'
        except (AnalysisFailure, SourceObservationError, KnowledgeUnavailable, BrainModelError, ValueError, OSError, KeyError) as error:
            receipt.update(failure_type=type(error).__name__, failure_code=(str(error) if isinstance(error, (AnalysisFailure, SourceObservationError, BrainModelError)) else 'sdk_knowledge_acceptance_failed'))
            if isinstance(error, AnalysisFailure) and error.response is not None:
                receipt['model_failure'] = {'provider_request_id': error.response.provider_request_id,
                                            'usage': asdict(error.response.usage),
                                            'public_blocks': list(error.response.public_blocks)}
        receipt['elapsed_ms'] = round((time.monotonic() - started) * 1000)
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'status': receipt['status'], 'elapsed_ms': receipt['elapsed_ms'],
                      'index_sha256': args.index_sha256, 'model_called': receipt.get('provider_http_requests', 0) > 0}))
    return 0 if receipt['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
