"""Actual HTTP/auth/HR grant; disposable database, no model or production call."""
import socket
import threading
from time import monotonic, sleep

import httpx
import psycopg
import uvicorn

from test_hr_company_intelligence_http import _actual_app, web_loop
from test_hr_turn_scope_v6_database import control_database, scoped_database


def test_research_http_authorization_and_fixed_content(web_loop, monkeypatch, tmp_path):
    app = _actual_app(web_loop, monkeypatch, tmp_path, tmp_path)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{sock.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = monotonic() + 15
    while not server.started and thread.is_alive() and monotonic() < deadline:
        sleep(.02)
    assert server.started
    try:
        with httpx.Client(base_url=origin, timeout=30) as client:
            assert client.get('/api/hr/panorama/research').status_code == 401
            client.cookies.update({'__Host-platform_session': web_loop.token, '__Host-platform_csrf': web_loop.csrf})
            client.headers.update({'Origin': 'https://localhost', 'X-CSRF-Token': web_loop.csrf})
            response = client.get('/api/hr/panorama/research')
            assert response.status_code == 200, response.text
            assert response.headers['cache-control'] == 'private, no-store'
            catalog = response.json()
            assert len(catalog['articles']) == 38
            for article in catalog['articles']:
                response = client.get(f"/api/hr/panorama/research/{article['id']}", params={'edition': catalog['edition']})
                assert response.status_code == 200, response.text
                assert response.json()['sha256'] == article['sha256']
            path = f"/api/hr/panorama/research/{catalog['articles'][0]['id']}"
            assert client.get(path, params={'edition': 'wrong-edition'}).status_code == 404
            assert client.get(path).status_code == 422
            client.cookies.clear()
            assert client.get(path, params={'edition': catalog['edition']}).status_code == 401
            client.cookies.update({'__Host-platform_session': web_loop.token, '__Host-platform_csrf': web_loop.csrf})
            # Revoke the real grant; the existing session must not bypass HR access.
            with psycopg.connect(web_loop.environment['admin']) as connection:
                connection.execute("delete from platform_control.agent_use_grants where target_internal_user_id=%s and agent_id='hr-bot'", (web_loop.owner_id,))
            assert client.get('/api/hr/panorama/research').status_code == 403
            assert client.get(path, params={'edition': catalog['edition']}).status_code == 403
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
