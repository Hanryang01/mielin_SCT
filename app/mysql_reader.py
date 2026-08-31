from __future__ import annotations

import threading
from typing import Any

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ModuleNotFoundError:  # pragma: no cover - depends on runtime environment
    pymysql = None
    DictCursor = None

from .config import Settings


class MysqlReader:
    """Own one read-only connection and serialize every query on it.

    Mirrors the pattern used by mielin_mon's MysqlReader: a single reused
    connection (ping-and-reconnect) plus a code-level guard that only allows
    SELECT statements, as defense in depth alongside the SELECT-only DB user.
    """

    def __init__(self, settings: Settings, *, read_timeout_seconds: int | None = None) -> None:
        self.settings = settings
        self.read_timeout_seconds = read_timeout_seconds or settings.request_timeout_seconds
        self._connection: Any | None = None
        self._lock = threading.RLock()

    def select_all(
        self, sql: str, params: dict[str, Any] | list[Any] | None = None
    ) -> list[dict[str, Any]]:
        normalized = sql.lstrip().lower()
        if not normalized.startswith("select"):
            raise RuntimeError("only SELECT statements are allowed")

        with self._lock:
            connection = self._get_connection()
            with connection.cursor() as cursor:
                cursor.execute(sql, params if params is not None else {})
                rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            if self._connection is None:
                return
            self._connection.close()
            self._connection = None

    def _get_connection(self) -> Any:
        if pymysql is None or DictCursor is None:
            raise RuntimeError("PyMySQL is required. Install it with `uv sync`.")

        mysql = self.settings.mysql
        if not mysql.host or not mysql.user or not mysql.database:
            raise RuntimeError("MYSQL_HOST, MYSQL_USER, and MYSQL_DATABASE must be set")

        if self._connection is not None:
            self._connection.ping(reconnect=True)
            return self._connection

        self._connection = pymysql.connect(
            host=mysql.host,
            port=mysql.port,
            user=mysql.user,
            password=mysql.password,
            database=mysql.database,
            charset=mysql.charset,
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=self.settings.request_timeout_seconds,
            read_timeout=self.read_timeout_seconds,
            write_timeout=self.settings.request_timeout_seconds,
        )
        return self._connection
