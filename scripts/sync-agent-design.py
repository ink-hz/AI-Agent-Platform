#!/usr/bin/env python3
"""Explicitly snapshot the maintained Agent design; never modify the source repository."""
import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--source-repo', type=Path, required=True)
parser.add_argument('--agent', choices=['hr', 'fae'], default='hr')
args = parser.parse_args()
repo = args.source_repo.resolve()
label = args.agent.upper()
name = f'{label}总体架构设计.md'
source = repo / name
body = source.read_bytes()
body.decode('utf-8')
commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
committed = subprocess.check_output(['git', '-C', str(repo), 'show', f'HEAD:{name}'])
folder = Path(__file__).resolve().parents[1] / 'backend/app/agent_designs/content'
folder.mkdir(parents=True, exist_ok=True)
entry = {'slug': args.agent, 'agent': f'{label} Agent', 'title': '总体架构设计', 'source': {
    'repository': f'AI-{label}-Agent', 'path': name, 'commit': commit,
    'working_tree_modified': body != committed, 'sha256': hashlib.sha256(body).hexdigest(),
    'captured_at': datetime.now(timezone.utc).isoformat(),
}}
index_path = folder / 'index.json'
index = json.loads(index_path.read_text()) if index_path.exists() else {'documents': []}
entries = index['documents']
for position, item in enumerate(entries):
    if item['slug'] == args.agent:
        entries[position] = entry
        break
else:
    entries.append(entry)
(folder / (args.agent + '.md')).write_bytes(body)
(folder / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(index, ensure_ascii=False))
