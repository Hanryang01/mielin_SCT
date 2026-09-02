"""실제 프로세스 기동 시점의 fail-fast를 검증한다.

app.config/app.main은 import되는 순간 검증하고 실패한다 (모듈 최상단 코드).
같은 프로세스 안에서 import 성공/실패를 반복 검증하려면 모듈 캐시 때문에
importlib.reload가 필요해 번거롭고 깨지기 쉽다 — 대신 별도 파이썬 프로세스를
띄워 "정말로 기동이 실패하는지"를 그대로 확인한다.

개발자 로컬에 실제 app/.env가 있어도 결과가 달라지지 않도록, 검사 대상 값은
빈 문자열로 명시적으로 덮어써서 넘긴다 (python-dotenv는 이미 설정된 키를
덮어쓰지 않으므로, 비워서 넘기면 실제 .env 파일 값이 끼어들지 못한다).
"""

from __future__ import annotations

import os
import subprocess
import sys

from tests.conftest import REPO_ROOT, REQUIRED_TEST_ENV


def _run(module: str, overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(REQUIRED_TEST_ENV)
    env.update(overrides)
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_missing_app_level_fails_startup():
    result = _run("app.config", {"APP_LEVEL": ""})
    assert result.returncode != 0
    assert "APP_LEVEL" in result.stderr


def test_invalid_app_level_fails_startup():
    result = _run("app.config", {"APP_LEVEL": "production"})
    assert result.returncode != 0
    assert "APP_LEVEL" in result.stderr


def test_valid_app_level_dev_starts_config():
    result = _run("app.config", {"APP_LEVEL": "dev"})
    assert result.returncode == 0, result.stderr


def test_valid_app_level_prod_starts_config():
    result = _run("app.config", {"APP_LEVEL": "prod"})
    assert result.returncode == 0, result.stderr


def test_missing_session_secret_key_fails_main_startup():
    result = _run("app.main", {"SESSION_SECRET_KEY": ""})
    assert result.returncode != 0
    assert "SESSION_SECRET_KEY" in result.stderr


def test_full_valid_env_starts_main():
    result = _run("app.main", {})
    assert result.returncode == 0, result.stderr
    # 시작 로그에 APP_LEVEL과 host/database는 보이되 비밀번호는 없어야 한다.
    assert "APP_LEVEL=dev" in result.stdout
    assert REQUIRED_TEST_ENV["MYSQL_PASSWORD"] not in result.stdout
    assert REQUIRED_TEST_ENV["REVIEW_MYSQL_PASSWORD"] not in result.stdout
