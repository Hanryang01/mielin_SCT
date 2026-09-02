"""app.config: APP_LEVEL 분기, dev/prod 설정 로딩, Settings 독립성."""

from __future__ import annotations

import pytest

from app import config


def test_load_settings_dev_uses_review_mysql_values_as_is(monkeypatch):
    monkeypatch.setenv("APP_LEVEL", "dev")
    monkeypatch.setenv("MYSQL_HOST", "mysql-host")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    monkeypatch.setenv("REVIEW_MYSQL_HOST", "review-host")
    monkeypatch.setenv("REVIEW_MYSQL_PORT", "3307")

    settings = config.load_settings()

    assert settings.app_level == "dev"
    assert settings.mysql.host == "mysql-host"
    # dev에서는 REVIEW_MYSQL_HOST/PORT를 그대로 쓴다 — mysql host와 달라도 된다.
    assert settings.review_mysql.host == "review-host"
    assert settings.review_mysql.port == 3307


def test_load_settings_prod_reuses_mysql_host_and_port(monkeypatch):
    monkeypatch.setenv("APP_LEVEL", "prod")
    monkeypatch.setenv("MYSQL_HOST", "prod-mysql-host")
    monkeypatch.setenv("MYSQL_PORT", "3306")
    # prod에서는 REVIEW_MYSQL_HOST/PORT에 다른 값을 넣어도 무시되고
    # MYSQL_HOST/PORT로 강제 통일된다 (운영은 같은 MySQL의 같은 mielin DB).
    monkeypatch.setenv("REVIEW_MYSQL_HOST", "should-be-ignored")
    monkeypatch.setenv("REVIEW_MYSQL_PORT", "9999")
    monkeypatch.setenv("REVIEW_MYSQL_USER", "review_prod_user")
    monkeypatch.setenv("MYSQL_USER", "sct_prod_user")

    settings = config.load_settings()

    assert settings.app_level == "prod"
    assert settings.review_mysql.host == "prod-mysql-host"
    assert settings.review_mysql.port == 3306
    # host/port만 통일될 뿐, 계정(권한)은 여전히 독립적으로 유지된다.
    assert settings.review_mysql.user == "review_prod_user"
    assert settings.mysql.user == "sct_prod_user"
    assert settings.review_mysql.user != settings.mysql.user


def test_mysql_and_review_mysql_are_independent_settings_objects(monkeypatch):
    monkeypatch.setenv("APP_LEVEL", "dev")
    settings = config.load_settings()

    assert isinstance(settings.mysql, config.MySQLSettings)
    assert isinstance(settings.review_mysql, config.ReviewMySQLSettings)
    assert settings.mysql is not settings.review_mysql

    # 두 번 로드해도 서로 다른 인스턴스이고, 값이 같아도 별개 객체다 (합쳐지지 않음).
    settings2 = config.load_settings()
    assert settings.mysql is not settings2.mysql
    assert settings.review_mysql is not settings2.review_mysql


@pytest.mark.parametrize("bad_value", ["production", "DEV", "staging", " "])
def test_load_settings_rejects_invalid_app_level(monkeypatch, bad_value):
    monkeypatch.setenv("APP_LEVEL", bad_value)
    with pytest.raises(RuntimeError, match="APP_LEVEL"):
        config.load_settings()


def test_load_settings_rejects_missing_app_level(monkeypatch):
    monkeypatch.delenv("APP_LEVEL", raising=False)
    with pytest.raises(RuntimeError, match="APP_LEVEL"):
        config.load_settings()


def test_session_secret_key_missing_is_reported_as_unconfigured(monkeypatch):
    monkeypatch.setenv("APP_LEVEL", "dev")
    monkeypatch.setenv("SESSION_SECRET_KEY", "")
    settings = config.load_settings()
    assert settings.auth.session_secret == ""
