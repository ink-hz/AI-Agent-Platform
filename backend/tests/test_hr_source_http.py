"""Actual HTTP/auth/HR grant; disposable database, no model or production call."""
# ruff: noqa: F401, F811

import socket
import threading
from time import monotonic, sleep

import httpx
import psycopg
import uvicorn
from test_hr_company_intelligence_http import _actual_app, web_loop
from test_hr_turn_scope_v6_database import control_database, scoped_database


def test_source_http_authorization_fixed_edition_pagination_and_ownership(
    web_loop, monkeypatch, tmp_path
):
    app = _actual_app(web_loop, monkeypatch, tmp_path, tmp_path)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    deadline = monotonic() + 15
    while not server.started and thread.is_alive() and monotonic() < deadline:
        sleep(0.02)
    assert server.started
    try:
        with httpx.Client(base_url=origin, timeout=30) as client:
            assert client.get("/api/hr/panorama/sources").status_code == 401
            client.cookies.update(
                {
                    "__Host-platform_session": web_loop.token,
                    "__Host-platform_csrf": web_loop.csrf,
                }
            )
            client.headers.update(
                {"Origin": "https://localhost", "X-CSRF-Token": web_loop.csrf}
            )
            response = client.get("/api/hr/panorama/sources")
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "private, no-store"
            catalog = response.json()
            assert catalog["job_count"] == 3437
            assert len(catalog["companies"]) == 12

            research_response = client.get("/api/hr/panorama/research")
            assert research_response.status_code == 200, research_response.text
            research = research_response.json()
            article = research["articles"][0]
            article_response = client.get(
                f"/api/hr/panorama/research/{article['id']}",
                params={"edition": research["edition"]},
            )
            assert article_response.status_code == 200, article_response.text
            article_body = article_response.json()
            assert article_body["sha256"] == article["sha256"]
            assert article_body["source_reference"]["edition"] == catalog["edition"]

            page_response = client.get(
                "/api/hr/panorama/sources/robosense",
                params={
                    "edition": catalog["edition"],
                    "q": "激光雷达",
                    "offset": 0,
                    "limit": 2,
                },
            )
            assert page_response.status_code == 200, page_response.text
            page = page_response.json()
            assert 1 <= len(page["items"]) <= 2
            assert page["total"] >= len(page["items"])
            assert page["offset"] == 0 and page["limit"] == 2
            job_id = page["items"][0]["job_id"]
            detail_path = f"/api/hr/panorama/sources/robosense/jobs/{job_id}"
            detail = client.get(detail_path, params={"edition": catalog["edition"]})
            assert detail.status_code == 200, detail.text
            assert detail.json()["company_key"] == "robosense"
            assert "\n" in detail.json()["duty"]

            assert (
                client.get(
                    "/api/hr/panorama/sources/hesai/jobs/" + job_id,
                    params={"edition": catalog["edition"]},
                ).status_code
                == 404
            )
            assert (
                client.get(
                    "/api/hr/panorama/sources/robosense",
                    params={"edition": "wrong-edition"},
                ).status_code
                == 404
            )
            assert client.get("/api/hr/panorama/sources/robosense").status_code == 422

            with psycopg.connect(web_loop.environment["admin"]) as connection:
                connection.execute(
                    "delete from platform_control.agent_use_grants "
                    "where target_internal_user_id=%s and agent_id='hr-bot'",
                    (web_loop.owner_id,),
                )
            assert client.get("/api/hr/panorama/sources").status_code == 403
            assert (
                client.get(
                    detail_path, params={"edition": catalog["edition"]}
                ).status_code
                == 403
            )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
