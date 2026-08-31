"""로그인 인증 — 계정 소스는 검수 DB의 ocr_reviewers 테이블이다.

별도 계정 목록(.env)을 두지 않고 ocr_reviewers를 그대로 쓰는 이유: 검수 의견
(ocr_review_comments.reviewer_id)과 admin 코멘트(ocr_admin_comments.admin_id)가
이미 이 테이블을 FK로 참조하고 있어서, 로그인 계정이 따로 있으면 "로그인한
사람"과 "의견을 남긴 사람"을 잇는 매핑을 이중으로 관리해야 하기 때문이다.
세션에 reviewer_id를 담아두면 그 매핑이 아예 필요 없어진다.

계정 등록/비밀번호 시딩은 `uv run python -m scripts.seed_reviewers` 참고.
"""

from __future__ import annotations

from typing import Any

import bcrypt

from .review_client import ReviewDbClient

# 로그인 실패 사유를 화면에 그대로 노출하지 않는다 — "그 아이디는 있는데
# 비밀번호가 틀렸다"를 알려주면 계정 존재 여부가 새어나가기 때문.
LOGIN_FAILED_MESSAGE = "아이디 또는 비밀번호가 올바르지 않습니다"

# 존재하지 않는 아이디로 로그인 시도했을 때도 bcrypt 검증과 비슷한 시간을
# 쓰도록 더미 해시를 한 번 돌린다 (응답 시간으로 계정 존재 여부를 알아내는
# 타이밍 공격 방지).
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt())


class AuthError(Exception):
    """로그인 실패 (사유는 의도적으로 하나로 뭉뚱그린다)."""


def authenticate(
    review_client: ReviewDbClient, *, username: str, password: str, ip: str | None
) -> dict[str, Any]:
    """아이디/비밀번호를 검증하고 세션에 담을 계정 정보를 돌려준다."""
    username = username.strip()
    reviewer = review_client.fetch_reviewer_by_username(username) if username else None

    if reviewer is None:
        bcrypt.checkpw(password.encode(), _DUMMY_HASH)
        raise AuthError(LOGIN_FAILED_MESSAGE)

    stored_hash = (reviewer.get("password_hash") or "").encode()
    try:
        password_ok = bcrypt.checkpw(password.encode(), stored_hash)
    except ValueError:
        # password_hash가 bcrypt 형식이 아닌 경우 (시딩 전 계정 등)
        password_ok = False

    if not password_ok:
        review_client.record_login_failure(reviewer_id=reviewer["id"])
        raise AuthError(LOGIN_FAILED_MESSAGE)

    if reviewer["is_deleted"] or not reviewer["is_active"]:
        # 비활성 계정은 비밀번호가 맞아도 로그인 불가. 위 검증을 통과한 뒤에
        # 확인해야 "비활성 계정이다"라는 응답으로 계정 존재가 드러나지 않는다.
        raise AuthError(LOGIN_FAILED_MESSAGE)

    review_client.record_login_success(reviewer_id=reviewer["id"], ip=ip)
    return {
        "id": reviewer["id"],
        "username": reviewer["username"],
        "name": reviewer["name"],
        "role": reviewer["role"],
    }


def safe_next_path(raw: str | None, default: str = "/") -> str:
    """로그인 후 되돌아갈 경로 검증 (오픈 리다이렉트 방지).

    사이트 내부 절대경로만 허용한다. `//evil.com`은 브라우저가 프로토콜 상대
    URL로 해석해 외부로 나가버리므로 반드시 같이 막아야 한다.
    """
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return default
    return raw
