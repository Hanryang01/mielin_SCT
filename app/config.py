from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent

load_dotenv(APP_DIR / ".env")


@dataclass(frozen=True)
class MySQLSettings:
    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str


@dataclass(frozen=True)
class ReviewMySQLSettings:
    """OCR 검수 DB 접속 정보. mielin(MYSQL_*, 읽기 전용)과 분리된 별도 DB로,
    검수자 의견을 쓰기(INSERT/UPDATE)까지 하므로 계정 권한도 별도로 관리한다.
    (03_ocr_review_schema.sql, OCR 검수 시나리오.md 참고)
    """

    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.database)


@dataclass(frozen=True)
class AuthSettings:
    """Monitor Console 로그인 세션 설정.

    계정 목록/비밀번호는 여기 없다 — 검수 DB의 ocr_reviewers 테이블이 계정
    소스다 (app/auth.py 참고). 여기 남는 건 세션 쿠키 서명 키뿐이다.
    """

    session_secret: str
    session_max_age_seconds: int


@dataclass(frozen=True)
class S3Settings:
    """S3 이미지 프리사인드 URL 설정. 자격증명이 비어 있으면 이미지 기능은 자동으로 꺼진다."""

    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    bucket: str
    presign_expires_seconds: int

    @property
    def configured(self) -> bool:
        return bool(self.aws_access_key_id and self.aws_secret_access_key and self.bucket)


@dataclass(frozen=True)
class Settings:
    mysql: MySQLSettings
    review_mysql: ReviewMySQLSettings
    auth: AuthSettings
    s3: S3Settings
    request_timeout_seconds: int
    max_page_size: int


def _int_env(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if minimum is not None:
        return max(minimum, value)
    return value


def load_settings() -> Settings:
    return Settings(
        mysql=MySQLSettings(
            host=os.getenv("MYSQL_HOST", "").strip(),
            port=_int_env("MYSQL_PORT", default=3306, minimum=1),
            user=os.getenv("MYSQL_USER", "").strip(),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "").strip(),
            charset=os.getenv("MYSQL_CHARSET", "utf8mb4").strip() or "utf8mb4",
        ),
        review_mysql=ReviewMySQLSettings(
            host=os.getenv("REVIEW_MYSQL_HOST", "").strip(),
            port=_int_env("REVIEW_MYSQL_PORT", default=3306, minimum=1),
            user=os.getenv("REVIEW_MYSQL_USER", "").strip(),
            password=os.getenv("REVIEW_MYSQL_PASSWORD", ""),
            database=os.getenv("REVIEW_MYSQL_DATABASE", "").strip(),
            charset=os.getenv("REVIEW_MYSQL_CHARSET", "utf8mb4").strip() or "utf8mb4",
        ),
        auth=AuthSettings(
            session_secret=os.getenv("SESSION_SECRET_KEY", "").strip(),
            session_max_age_seconds=_int_env(
                "SESSION_MAX_AGE_SECONDS", default=60 * 60 * 12, minimum=60
            ),
        ),
        s3=S3Settings(
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "").strip(),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
            aws_region=os.getenv("AWS_REGION", "ap-northeast-2").strip(),
            bucket=os.getenv("S3_BUCKET", "").strip(),
            presign_expires_seconds=_int_env(
                "S3_PRESIGN_EXPIRES_SECONDS", default=300, minimum=30
            ),
        ),
        request_timeout_seconds=_int_env(
            "REQUEST_TIMEOUT_SECONDS", default=10, minimum=3
        ),
        max_page_size=_int_env("MAX_PAGE_SIZE", default=100, minimum=1),
    )
