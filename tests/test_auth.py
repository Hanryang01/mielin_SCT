"""app.auth — safe_next_path(오픈 리다이렉트 방지)와 authenticate() 로직.

authenticate()는 ReviewDbClient를 인자로 받으므로, 실제 DB 대신 인터페이스만
흉내 낸 가짜 객체를 넘겨 DB 접속 없이 검증한다.
"""

from __future__ import annotations

import bcrypt
import pytest

from app.auth import AuthError, authenticate, safe_next_path


class _FakeReviewClient:
    def __init__(self, reviewer: dict | None):
        self._reviewer = reviewer
        self.success_calls: list[dict] = []
        self.failure_calls: list[dict] = []

    def fetch_reviewer_by_username(self, username: str):
        return self._reviewer

    def record_login_success(self, *, reviewer_id, ip):
        self.success_calls.append({"reviewer_id": reviewer_id, "ip": ip})

    def record_login_failure(self, *, reviewer_id):
        self.failure_calls.append({"reviewer_id": reviewer_id})


def _reviewer(password: str, **overrides) -> dict:
    base = {
        "id": 1,
        "username": "reviewer-a",
        "name": "테스트 검수자",
        "role": "reviewer",
        "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        "is_deleted": False,
        "is_active": True,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "/"),
        ("", "/"),
        ("relative/path", "/"),
        ("//evil.com", "/"),
        ("//evil.com/phish", "/"),
        ("/review", "/review"),
        ("/admin?tab=1", "/admin?tab=1"),
    ],
)
def test_safe_next_path(raw, expected):
    assert safe_next_path(raw) == expected


def test_authenticate_succeeds_with_correct_password():
    client = _FakeReviewClient(_reviewer("correct-password"))
    result = authenticate(client, username="reviewer-a", password="correct-password", ip="127.0.0.1")
    assert result == {"id": 1, "username": "reviewer-a", "name": "테스트 검수자", "role": "reviewer"}
    assert len(client.success_calls) == 1


def test_authenticate_fails_with_wrong_password():
    client = _FakeReviewClient(_reviewer("correct-password"))
    with pytest.raises(AuthError):
        authenticate(client, username="reviewer-a", password="wrong-password", ip=None)
    assert len(client.failure_calls) == 1


def test_authenticate_fails_for_unknown_username():
    client = _FakeReviewClient(None)
    with pytest.raises(AuthError):
        authenticate(client, username="nobody", password="anything", ip=None)


def test_authenticate_fails_for_deleted_account():
    client = _FakeReviewClient(_reviewer("correct-password", is_deleted=True))
    with pytest.raises(AuthError):
        authenticate(client, username="reviewer-a", password="correct-password", ip=None)


def test_authenticate_fails_for_inactive_account():
    client = _FakeReviewClient(_reviewer("correct-password", is_active=False))
    with pytest.raises(AuthError):
        authenticate(client, username="reviewer-a", password="correct-password", ip=None)
