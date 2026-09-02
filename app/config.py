from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent

VALID_APP_LEVELS = ("dev", "prod")

# app/.env는 dev/prod 공통 파일이다 — 파일을 나누지 않고, 그 안에 적힌
# APP_LEVEL 값(dev 또는 prod)으로 동작을 가른다. systemd 운영 서비스가
# Environment=APP_LEVEL=prod를 이미 프로세스 환경변수로 넘겨준 경우
# load_dotenv는 그 값을 덮어쓰지 않는다(기본 override=False) — 파일 안에
# 실수로 다른 값이 남아 있어도 systemd 쪽이 항상 우선한다.
load_dotenv(APP_DIR / ".env")


def _resolve_app_level() -> str:
    """실행 환경(dev/prod)을 결정한다.

    APP_LEVEL은 여기서 처음 만들어지는 게 아니라 프로세스 환경변수 또는
    app/.env 안의 APP_LEVEL=dev|prod 줄에서 온다. 기본값을 두지 않는 이유는,
    누락됐을 때 조용히 운영(prod)으로 기동되는 사고를 막기 위해서다.
    """
    app_level = os.environ.get("APP_LEVEL", "").strip()
    if app_level not in VALID_APP_LEVELS:
        raise RuntimeError(
            f"APP_LEVEL이 올바르지 않습니다 (현재: {app_level!r}). "
            f"{VALID_APP_LEVELS} 중 하나여야 합니다. app/.env에 "
            "APP_LEVEL=dev 또는 APP_LEVEL=prod를 지정하거나(로컬 개발), "
            "systemd 서비스에 Environment=APP_LEVEL=prod를 지정하세요(운영)."
        )
    return app_level


APP_LEVEL = _resolve_app_level()


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
    """OCR 검수 DB 접속 정보.

    dev에서는 mielin(MYSQL_*, 읽기 전용)과 분리된 별도 DB/계정이다. prod에서는
    load_settings()가 MYSQL_*과 같은 값(같은 서버·같은 mielin DB·같은 계정)을
    그대로 채워 넣는다 — 운영은 실제로 같은 곳을 가리키기 때문이다.
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
    app_level: str
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
    app_level = _resolve_app_level()

    mysql = MySQLSettings(
        host=os.getenv("MYSQL_HOST", "").strip(),
        port=_int_env("MYSQL_PORT", default=3306, minimum=1),
        user=os.getenv("MYSQL_USER", "").strip(),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DATABASE", "").strip(),
        charset=os.getenv("MYSQL_CHARSET", "utf8mb4").strip() or "utf8mb4",
    )

    if app_level == "prod":
        # 운영은 MYSQL_*과 REVIEW_MYSQL_*이 같은 MySQL 서버, 같은 mielin DB,
        # 같은 계정을 가리킨다 — REVIEW_MYSQL_*를 따로 읽지 않고 MYSQL_*
        # 값을 그대로 재사용한다. APP_LEVEL을 나눈 이유가 바로 이 분기다:
        # 운영자가 .env에 같은 값을 두 번 입력하다 어긋나는 사고를 막는다.
        # (연결 객체(MysqlReader/ReviewDbClient)는 여전히 분리되어 있다 —
        # 합쳐지는 건 접속 설정값뿐이다.)
        review_mysql = ReviewMySQLSettings(
            host=mysql.host,
            port=mysql.port,
            user=mysql.user,
            password=mysql.password,
            database=mysql.database,
            charset=mysql.charset,
        )
    else:
        # dev는 REVIEW_MYSQL이 운영과 무관한 별도 DB이므로 REVIEW_MYSQL_*를
        # 그대로 읽는다.
        review_mysql = ReviewMySQLSettings(
            host=os.getenv("REVIEW_MYSQL_HOST", "").strip(),
            port=_int_env("REVIEW_MYSQL_PORT", default=3306, minimum=1),
            user=os.getenv("REVIEW_MYSQL_USER", "").strip(),
            password=os.getenv("REVIEW_MYSQL_PASSWORD", ""),
            database=os.getenv("REVIEW_MYSQL_DATABASE", "").strip(),
            charset=os.getenv("REVIEW_MYSQL_CHARSET", "utf8mb4").strip() or "utf8mb4",
        )

    return Settings(
        app_level=app_level,
        mysql=mysql,
        review_mysql=review_mysql,
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
