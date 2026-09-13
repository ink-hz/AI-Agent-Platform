"""Offline integrity and executable-input checks, not professional/model acceptance."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def validate(root=ROOT):
    manifest=json.loads((root/'manifest.json').read_text())
    corpus=json.loads((root/'corpus.json').read_text())
    assert sha(root/'corpus.json')==manifest['corpus_sha256']
    assert sha(root/'build_corpus.py')==manifest['builder_sha256']
    for name,value in manifest['frozen_v1'].items(): assert sha(root.parent/name)==value,name
    assert [c['id'] for c in corpus['cases']]==[f'H{i:02d}' for i in range(1,23)]
    assets={a['path']:a for a in manifest['assets']}
    assert len(assets)==len(manifest['assets'])
    for path,asset in assets.items():
        assert (root/path).resolve().is_relative_to(root.resolve())
        assert sha(root/path)==asset['sha256'],path
        assert asset['version']=='synthetic-v2.1'
    for case in corpus['cases']:
        raw={k:v for k,v in case.items() if k!='content_sha256'}
        assert hashlib.sha256(json.dumps(raw,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()==case['content_sha256']
        for n,t in enumerate(case['turns'],1):
            assert t['number']==n and t['depends_on_turn']==(n-1 if n>1 else None)
            assert t['context_fixture'] in assets
            assert t['text'] and all(p in assets for p in t['attach_assets'])
            assert not any('review-only' in p for p in t['attach_assets'])
            assert set(t['allowed_source_urls']) <= {s['url_template'] for s in case['sources']}
        for source in case['sources']: assert source['asset'] in assets and source['synthetic']
    h17=corpus['cases'][16]
    assert '公司A' in h17['turns'][0]['text'] and '公司B' in h17['turns'][1]['text']
    images=h17['turns'][1]['attach_assets'];assert len(images)==9
    for p in images:assert (root/p).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    for i in (15,16):
        f=corpus['cases'][i]['fault_spec']
        assert f['kind']=='owned_local_worker_process_sigkill_restart' and not f['production_fault_injection_authorized']
        assert any('SIGKILL' in s for s in f['steps']) and any('lease' in s for s in f['steps'])
    assert corpus['coverage']['messages']==242 and corpus['coverage']['selected_unique_messages']==68
    print(json.dumps({'status':'offline_self_check_pass','cases':22,'turns':sum(len(c['turns']) for c in corpus['cases']),'assets':len(assets),'model_calls':0,'http_business_replays':0,'independent_review':'pending'}))
if __name__=='__main__': validate()
