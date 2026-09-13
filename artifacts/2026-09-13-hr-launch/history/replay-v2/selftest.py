"""Positive offline validation, tamper negative, actual local static HTTP sequencing."""
import copy
import importlib.util
import json
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
import shutil
from validate import ROOT,validate
validate()
with tempfile.TemporaryDirectory() as tmp:
    parent=Path(tmp)
    for name in ['scenarios.json','analysis.md','independent-corpus-review.md','independent-corpus-review-fingerprints.json']:shutil.copy(ROOT.parent/name,parent/name)
    clone=parent/'replay-v2';shutil.copytree(ROOT,clone)
    asset=clone/'fixtures/H01/turn-02-context.txt';asset.write_text('tampered synthetic text')
    try:validate(clone)
    except AssertionError:print('PASS: mutated fixture rejected')
    else:raise AssertionError('tampering accepted')
spec=importlib.util.spec_from_file_location('fixture_server',ROOT/'serve_fixtures.py');servermodule=importlib.util.module_from_spec(spec);spec.loader.exec_module(servermodule)
server=servermodule.ThreadingHTTPServer(('127.0.0.1',0),servermodule.Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
def status(path):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}{path}') as response:return response.status
    except urllib.error.HTTPError as error:return error.code
try:
    observed=[status(p) for p in ['/H22/campus','/H22/social','/H22/campus','/H22/forbidden','/H22/intern','/H22/root?escape=1']]
    assert observed==[503,200,200,403,200,403],observed
    print(json.dumps({'static_http_status_sequence':observed,'scope':'fixture server only; no HR API/model/worker acceptance'}))
finally:server.shutdown();server.server_close();thread.join()
