"""FastAPI 라우트 — 루트 리다이렉트, 인증 차단, 로그인 흐름.

app.main을 그대로 import해서 TestClient로 띄운다. app.main의 review_client/
client/s3_client는 (mysql_reader/review_client가 lazy connect라서) import
시점에는 실제 DB에 붙지 않는다 — 로그인처럼 실제로 쿼리를 실행하는 경로만
몽키패치로 대체해 DB 접속 자체가 일어나지 않게 막는다.
"""

from __future__ import annotations

import bcrypt
from fastapi.testclient import TestClient

from app import main


def _client() -> TestClient:
    return TestClient(main.app)


def test_unauthenticated_api_access_is_blocked():
    with _client() as client:
        response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_root_redirects_unauthenticated_to_review():
    with _client() as client:
        response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/review"


def test_review_redirects_unauthenticated_to_login_with_next():
    with _client() as client:
        response = client.get("/review", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login?next=/review"


def test_admin_page_blocks_unauthenticated():
    with _client() as client:
        response = client.get("/admin", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login?next=/admin"


def _login(client: TestClient, monkeypatch, *, role: str) -> None:
    password = "correct-password"
    reviewer_row = {
        "id": 42,
        "username": "reviewer-a",
        "name": "테스트 계정",
        "role": role,
        "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        "is_deleted": False,
        "is_active": True,
    }
    monkeypatch.setattr(
        main.review_client, "fetch_reviewer_by_username", lambda username: reviewer_row
    )
    monkeypatch.setattr(main.review_client, "record_login_success", lambda **kwargs: None)
    monkeypatch.setattr(main, "_require_review_db", lambda: None)

    response = client.post(
        "/api/auth/login", json={"username": "reviewer-a", "password": password}
    )
    assert response.status_code == 200


def test_login_then_root_redirects_reviewer_to_review(monkeypatch):
    with _client() as client:
        _login(client, monkeypatch, role="reviewer")
        response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/review"


def test_login_then_root_redirects_admin_to_admin(monkeypatch):
    with _client() as client:
        _login(client, monkeypatch, role="admin")
        response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/admin"


def test_login_then_me_returns_session_reviewer(monkeypatch):
    with _client() as client:
        _login(client, monkeypatch, role="reviewer")
        response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["username"] == "reviewer-a"


def test_wrong_password_returns_401(monkeypatch):
    reviewer_row = {
        "id": 42,
        "username": "reviewer-a",
        "name": "테스트 계정",
        "role": "reviewer",
        "password_hash": bcrypt.hashpw(b"correct-password", bcrypt.gensalt()).decode(),
        "is_deleted": False,
        "is_active": True,
    }
    with _client() as client:
        monkeypatch.setattr(
            main.review_client, "fetch_reviewer_by_username", lambda username: reviewer_row
        )
        monkeypatch.setattr(main.review_client, "record_login_failure", lambda **kwargs: None)
        monkeypatch.setattr(main, "_require_review_db", lambda: None)
        response = client.post(
            "/api/auth/login", json={"username": "reviewer-a", "password": "wrong"}
        )
    assert response.status_code == 401
