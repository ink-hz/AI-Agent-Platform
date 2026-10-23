"""Compile a caller-pinned read-only SDK semantic package to an explicit output."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from app.sdk_knowledge.corpus import KnowledgeUnavailable, build_index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform-root', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--expected-manifest-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        bundle = build_index(args.platform_root, manifest_path=args.manifest,
                             expected_manifest_sha256=args.expected_manifest_sha256)
        raw = (json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()
        with args.output.open('xb') as stream:
            stream.write(raw)
    except (KnowledgeUnavailable, OSError):
        parser.exit(1, 'Knowledge index build failed; verify pinned inputs and output destination.\n')
    print(json.dumps({'schema_version': 1, 'baseline_id': bundle['baseline_id'],
                      'pages': len(bundle['pages']), 'repositories': len(bundle['repositories']),
                      'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
