import json

import httpx

from tools.sdk_knowledge.analyze_change import ObservedStream


class Stream(httpx.SyncByteStream):
    def __iter__(self):
        event = {'type': 'message_start', 'message': {'id': 'msg-sdk', 'usage': {'input_tokens': 321}}}
        raw = ('data: ' + json.dumps(event) + '\n\n').encode()
        yield raw[:9]
        yield raw[9:]
        yield b'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"private reasoning"}}\n\n'
        yield b'data: {"type":"message_delta","usage":{"output_tokens":20},"delta":{"stop_reason":"tool_use"}}\n\n'
        yield b'data: {"type":"message_stop"}\n\n'


def test_stream_observation_preserves_bytes_and_records_only_safe_metrics():
    metrics = {}
    chunks = list(ObservedStream(Stream(), metrics))
    assert b''.join(chunks) == b''.join(Stream())
    assert metrics['provider_request_id'] == 'msg-sdk'
    assert metrics['usage']['input_tokens'] == 321 and metrics['usage']['output_tokens'] == 20
    assert metrics['usage_status'] == 'complete'
    assert metrics['events']['content_block_delta'] == 1
    assert 'private reasoning' not in str(metrics)
