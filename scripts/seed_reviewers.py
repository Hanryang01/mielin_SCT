"""ocr_reviewers 계정 시딩 (웹 로그인 계정 = 검수자 계정).

로그인은 03_ocr_review_schema.sql의 ocr_reviewers 테이블을 소스로 쓴다.
비밀번호는 bcrypt 해시로만 저장하며(평문 저장 금지), 해시는 SQL로 만들 수
없어서 이 스크립트가 담당한다.

    uv run python -m scripts.seed_reviewers

비밀번호는 SEED_REVIEWER_PASSWORD 환경변수(app/.env)에서 읽고, --password로
덮어쓸 수 있다. 여러 번 실행해도 안전하다 (username 기준 upsert).
기존 계정의 비밀번호를 다시 덮어쓰려면 --reset-password를 붙일 것.
"""

from __future__ import annotations

import argparse
import os
import sys

import bcrypt
import pymysql
from pymysql.cursors import DictCursor

from app.config import load_settings

# 실제 운영 계정 목록. 새 검수자가 생기면 여기에 한 줄 추가하고 다시 실행하면 된다.
# role: 'admin'은 Admin 열람 화면(§4.5) 접근 권한, 'annotator'는 검수 화면만.
ACCOUNTS = [
    {"username": "technonia01", "name": "검수자 A", "role": "annotator"},
    {"username": "technonia02", "name": "검수자 B", "role": "annotator"},
    {"username": "admin", "name": "관리자", "role": "admin"},
]

UPSERT = """
INSERT INTO ocr_reviewers (username, email, password_hash, name, role)
VALUES (%(username)s, %(email)s, %(password_hash)s, %(name)s, %(role)s)
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    role = VALUES(role),
    is_active = 1,
    is_deleted = 0
"""

UPSERT_WITH_PASSWORD = """
INSERT INTO ocr_reviewers (username, email, password_hash, name, role)
VALUES (%(username)s, %(email)s, %(password_hash)s, %(name)s, %(role)s)
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    role = VALUES(role),
    password_hash = VALUES(password_hash),
    password_changed_at = CURRENT_TIMESTAMP,
    is_active = 1,
    is_deleted = 0
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--password", default=None, help="시딩할 비밀번호 (기본: SEED_REVIEWER_PASSWORD)")
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="이미 있는 계정의 비밀번호도 덮어쓴다 (기본은 이름/역할만 갱신)",
    )
    args = parser.parse_args()

    password = args.password or os.getenv("SEED_REVIEWER_PASSWORD", "")
    if not password:
        print("ERROR: 비밀번호가 없습니다. --password 또는 app/.env의 SEED_REVIEWER_PASSWORD를 설정하세요.")
        return 1

    review = load_settings().review_mysql
    if not review.configured:
        print("ERROR: REVIEW_MYSQL_* 설정이 비어 있습니다 (app/.env 확인).")
        return 1

    sql = UPSERT_WITH_PASSWORD if args.reset_password else UPSERT

    connection = pymysql.connect(
        host=review.host,
        port=review.port,
        user=review.user,
        password=review.password,
        database=review.database,
        charset=review.charset,
        cursorclass=DictCursor,
        autocommit=True,
    )
    with connection:
        with connection.cursor() as cursor:
            for account in ACCOUNTS:
                # bcrypt salt는 계정마다 새로 뽑는다 (같은 비밀번호여도 해시가 달라짐)
                password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
                cursor.execute(
                    sql,
                    {
                        **account,
                        "email": f"{account['username']}@technonia.com",
                        "password_hash": password_hash,
                    },
                )
                print(f"  seeded {account['username']} ({account['role']})")

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, username, name, role, is_active FROM ocr_reviewers ORDER BY id"
            )
            rows = cursor.fetchall()

    known = {a["username"] for a in ACCOUNTS}
    print("\n현재 ocr_reviewers:")
    for row in rows:
        mark = "" if row["username"] in known else "   <-- ACCOUNTS에 없는 계정 (정리 대상인지 확인)"
        print(f"  #{row['id']} {row['username']} / {row['name']} / {row['role']}{mark}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
