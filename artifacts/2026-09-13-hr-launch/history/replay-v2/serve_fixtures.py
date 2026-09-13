"""Serve only manifest-pinned synthetic research pages. Never proxy the internet."""
import argparse
import hashlib
import json
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parent
SOURCES={urlsplit(s['url_template'].replace('{fixture_origin}','http://fixture.invalid')).path:s for c in json.loads((ROOT/'corpus.json').read_text())['cases'] for s in c['sources']}
HASHES={a['path']:a['sha256'] for a in json.loads((ROOT/'manifest.json').read_text())['assets']}
COUNTS=Counter(); LOCK=Lock()
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path=urlsplit(self.path).path
        if self.path!=path or path not in SOURCES:
            self.send_error(403);return
        source=SOURCES[path]
        with LOCK:
            index=COUNTS[path]; COUNTS[path]+=1
        status=source['response_statuses'][min(index,len(source['response_statuses'])-1)]
        body=(ROOT/source['asset']).read_bytes()
        if hashlib.sha256(body).hexdigest()!=HASHES[source['asset']]:self.send_error(500);return
        if status!=200:self.send_error(status,'synthetic transient source failure');return
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self, format, *args): pass
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8765);args=parser.parse_args()
    ThreadingHTTPServer((args.host,args.port),Handler).serve_forever()
