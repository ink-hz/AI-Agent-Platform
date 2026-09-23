#!/usr/bin/env python3
"""Explicitly snapshot the maintained HR design; never modify the source repository."""
import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--source-repo', type=Path, required=True)
args = parser.parse_args()
repo = args.source_repo.resolve()
name = 'HR总体架构设计.md'
source = repo / name
body = source.read_bytes()
body.decode('utf-8')
commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
committed = subprocess.check_output(['git', '-C', str(repo), 'show', f'HEAD:{name}'])
folder = Path(__file__).resolve().parents[1] / 'backend/app/agent_designs/content'
folder.mkdir(parents=True, exist_ok=True)
entry = {'slug': 'hr', 'agent': 'HR Agent', 'title': '总体架构设计', 'source': {
    'repository': 'AI-HR-Agent', 'path': name, 'commit': commit,
    'working_tree_modified': body != committed, 'sha256': hashlib.sha256(body).hexdigest(),
    'captured_at': datetime.now(timezone.utc).isoformat(),
}}
index_path = folder / 'index.json'
index = json.loads(index_path.read_text()) if index_path.exists() else {'documents': []}
index['documents'] = [item for item in index['documents'] if item['slug'] != 'hr'] + [entry]
(folder / 'hr.md').write_bytes(body)
(folder / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(index, ensure_ascii=False))
