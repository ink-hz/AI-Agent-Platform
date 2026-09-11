"""Read-only, edition-pinned prose. No model calls and no legacy bundle mutation."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .panorama_repository import PanoramaNotFound, PanoramaUnavailable


class ResearchLibrary:
    def __init__(self, root: Path | None = None):
        self.root = root or Path(__file__).with_name('research_content')

    def _manifest(self):
        try:
            value = json.loads((self.root / 'manifest.json').read_text())
            edition = value.pop('edition')
            digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if edition != 'research-' + digest[:20]:
                raise ValueError('manifest identity mismatch')
            value['edition'] = edition
            return value
        except (OSError, ValueError, KeyError, TypeError):
            raise PanoramaUnavailable('research publication unavailable') from None

    def catalog(self):
        value = self._manifest()
        documents = value.pop('documents')
        value['articles'] = [item for item in documents if item['kind'] == 'analysis']
        return value

    def document(self, document_id: str, edition: str):
        value = self._manifest()
        if edition != value['edition']:
            raise PanoramaNotFound('research edition unavailable')
        entry = next((item for item in value['documents'] if item['id'] == document_id), None)
        if entry is None:
            raise PanoramaNotFound('research document unavailable')
        path = self.root / entry['path']
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
                raise ValueError('invalid document path')
            body = path.read_bytes()
            if len(body) != entry['size_bytes'] or hashlib.sha256(body).hexdigest() != entry['sha256']:
                raise ValueError('document identity mismatch')
            text = body.decode('utf-8')
        except (OSError, ValueError):
            raise PanoramaUnavailable('research document unavailable') from None
        paths = {item['path']: item['id'] for item in value['documents']}
        links = {}
        for href in re.findall(r'\]\(([^)]+)\)', text):
            parsed = urlsplit(href)
            if parsed.scheme or parsed.netloc:
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(entry['path']), unquote(parsed.path)))
            if resolved in paths:
                links[href] = paths[resolved]
        return {**entry, 'edition': edition, 'markdown': text, 'links': links}
