"""Package existing reviewed prose; never synthesize or rewrite analysis."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'backend/app/hr/research_content'
COMPANIES = [
    ('robosense', '速腾聚创'), ('shining3d', '先临三维'), ('creality', '创想三维'),
    ('agibot', '智元集团招聘集合'), ('bambu-lab', '拓竹'), ('insta360', '影石'), ('hesai', '禾赛'),
]
QUESTIONS = ['工作分工', '责任层级', '要求与任务', '产品阶段与交付', '客户与应用场景',
             '薪酬与责任', '热招与持续招聘', '商业责任比较', '技术意图', '能力缺口与边界',
             '岗位内部矛盾', '技术解法比较']
ROOT_COMPANIES = {
    1: ['robosense'], 2: ['robosense'], 3: ['robosense'], 4: ['robosense'],
    5: ['robosense', 'shining3d', 'creality'], 6: ['shining3d', 'creality'],
    7: ['shining3d', 'creality'], 8: ['shining3d', 'creality'],
    9: ['robosense', 'shining3d', 'creality'], 10: ['robosense', 'shining3d', 'creality'],
    11: ['robosense', 'shining3d', 'creality'], 12: ['robosense', 'shining3d'],
}
documents = []
for file in sorted(ROOT.rglob('*.md')):
    relative = file.relative_to(ROOT).as_posix()
    body = file.read_bytes()
    text = body.decode('utf-8')
    question = re.match(r'q(\d+)-', file.stem)
    number = int(question[1]) if question else None
    companies = []
    if number:
        if file.parent == ROOT:
            companies = ROOT_COMPANIES[number]
        elif file.parent.name == 'comparisons':
            companies = ['bambu-lab', 'insta360'] if number == 8 else ['hesai', 'insta360']
        else:
            companies = [file.parent.name]
    title = next(line[2:].strip() for line in text.splitlines() if line.startswith('# '))
    paragraphs = text.split('\n\n')
    excerpt = next((p for p in paragraphs[1:] if p.strip() and not p.startswith(('#', '<', '|'))), '')
    excerpt = re.sub(r'\[([^]]+)\]\([^)]+\)', r'\1', excerpt)
    excerpt = re.sub(r'[*`\n]', '', excerpt).strip()
    documents.append(dict(
        id=relative.removesuffix('.md').replace('/', '--').lower(), path=relative,
        title=title, question=number, companies=companies, kind='analysis' if number else 'support',
        excerpt=excerpt[:160] + ('…' if len(excerpt) > 160 else ''),
        sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body),
    ))
payload = dict(schema_version=1, analyzed_at='2026-09-09', observed_at='2026-09-06',
    covered_job_identities=3355, source_job_identities=3437,
    source_archive_id='2ed2262c-63a0-47e5-996c-7d00eb0be1a0',
    companies=[dict(id=k, name=n) for k,n in COMPANIES],
    questions=[dict(id=i+1, name=n) for i,n in enumerate(QUESTIONS)], documents=documents)
edition = 'research-' + hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:20]
payload['edition'] = edition
(ROOT/'manifest.json').write_text(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
print(edition, len([d for d in documents if d['kind']=='analysis']), 'articles')
