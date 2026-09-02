"""pytest 공통 설정.

이 프로젝트의 테스트는 실제 MySQL/AWS에 접속하지 않는다 — app.main을 import하는
순간 app.config.load_settings()가 돌면서 필요한 환경변수를 읽는데, 이 값들이
없으면 개발자 로컬의 진짜 app/.env를 읽어버리거나 import 자체가 실패한다.

그래서 conftest.py가 테스트 모듈이 import되기 전에(= app.config가 처음
import되기 전에) 모든 필수 값을 os.environ에 강제로 채워 넣는다. python-dotenv의
load_dotenv()는 기본적으로 이미 설정된 환경변수를 덮어쓰지 않으므로(override=False),
개발자 로컬에 진짜 app/.env가 있어도 여기서 정한 값이 우선한다 — 테스트 결과가
로컬 파일 상태에 좌우되지 않는다.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 실제 접속 정보처럼 보이지 않게 일부러 test- 접두사를 붙인다. 값 자체는
# 어디에도 연결되지 않는다 — DB를 실제로 건드리는 테스트는 전부 클라이언트
# 메서드를 몽키패치해서 커넥션을 열지 않는다.
REQUIRED_TEST_ENV = {
    "APP_LEVEL": "dev",
    "MYSQL_HOST": "test-mysql-host.invalid",
    "MYSQL_PORT": "3306",
    "MYSQL_USER": "test_select_only_user",
    "MYSQL_PASSWORD": "test-password",
    "MYSQL_DATABASE": "mielin_test",
    "REVIEW_MYSQL_HOST": "test-review-host.invalid",
    "REVIEW_MYSQL_PORT": "3306",
    "REVIEW_MYSQL_USER": "test_review_user",
    "REVIEW_MYSQL_PASSWORD": "test-password",
    "REVIEW_MYSQL_DATABASE": "ocr_review_test",
    "SESSION_SECRET_KEY": "test-session-secret-not-for-prod-use",
}

os.environ.update(REQUIRED_TEST_ENV)
