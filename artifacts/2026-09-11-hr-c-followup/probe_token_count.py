"""One same-gateway counting request; synthetic input, no generation or redirects."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

profile = json.loads((Path.home() / 'Developer/work/AI-Agent-Platform/.hr-agent/provider.json').read_text())
parsed = urlsplit(profile['endpoint'])
assert parsed.path.rstrip('/').endswith('/messages'), 'unsupported configured path'
assert not parsed.query and not parsed.fragment
url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip('/') + '/count_tokens', '', ''))
credential = Path(profile['credential_file']).read_text().strip()
headers = {'anthropic-version': '2023-06-01'}
headers.update({'Authorization': 'Bearer ' + credential} if profile.get('auth_scheme') == 'bearer' else {'x-api-key': credential})
body = {'model': profile['model'], 'messages': [{'role': 'user', 'content': '公开合成计量探针。Synthetic token counting probe.'}]}
output = {'observed_at': datetime.now(timezone.utc).isoformat(), 'configured_model': profile['model'], 'configured_profile_revision': profile['revision'], 'endpoint_relation': 'same configured origin and Messages path + /count_tokens', 'endpoint_sha256': hashlib.sha256(url.encode()).hexdigest(), 'request': body, 'generation_requested': False, 'redirects_followed': False}
try:
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.post(url, headers=headers, json=body)
    output.update(status_code=response.status_code, response_sha256=hashlib.sha256(response.content).hexdigest(), response_bytes=len(response.content))
    try:
        data = response.json()
    except ValueError:
        data = {}
    if isinstance(data, dict) and type(data.get('input_tokens')) is int:
        output['input_tokens'] = data['input_tokens']
    error = data.get('error') if isinstance(data, dict) else None
    if isinstance(error, dict) and isinstance(error.get('type'), str) and re.fullmatch('[a-z_]{1,80}', error['type']):
        output['error_type'] = error['type']
except httpx.HTTPError as exc:
    output['transport_error_class'] = type(exc).__name__
print(json.dumps(output, ensure_ascii=False, indent=2))
