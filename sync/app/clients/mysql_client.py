"""
Cliente MySQL (PyMySQL).

Reglas del README respetadas a nivel de codigo:
  - Las consultas GET usan SOLO SELECT (nunca escriben).
  - Los INSERT/UPDATE reales se hacen por lotes (executemany).
  - El servicio NO altera la estructura: jamas emite DDL (CREATE/ALTER/DROP).
"""
from contextlib import contextmanager
from typing import Any, Iterable, Optional

import pymysql
from pymysql.cursors import DictCursor

from app.core.logging_config import get_logger
from app.services.config_service import get_runtime_config

log = get_logger(__name__)

# Sentinela: ninguna sentencia DDL debe pasar por aqui.
_FORBIDDEN_DDL = ("CREATE ", "ALTER ", "DROP ", "TRUNCATE ", "RENAME ")


def _assert_no_ddl(sql: str) -> None:
    head = sql.lstrip().upper()
    for kw in _FORBIDDEN_DDL:
        if head.startswith(kw):
            raise RuntimeError(
                f"Operacion DDL bloqueada por politica del servicio: {kw.strip()}"
            )


class MySQLClient:
    def _connect(self):
        cfg = get_runtime_config()
        return pymysql.connect(
            host=cfg.mysql_host,
            port=cfg.mysql_port,
            user=cfg.mysql_user,
            password=cfg.mysql_password,
            database=cfg.mysql_database,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )

    @contextmanager
    def connection(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def fetch_all(self, sql: str, params: tuple | dict | None = None) -> list[dict]:
        _assert_no_ddl(sql)
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()

    def fetch_one(self, sql: str, params: tuple | dict | None = None) -> Optional[dict]:
        _assert_no_ddl(sql)
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()

    def execute(self, sql: str, params: tuple | dict | None = None) -> int:
        _assert_no_ddl(sql)
        with self.connection() as conn:
            with conn.cursor() as cur:
                affected = cur.execute(sql, params)
                conn.commit()
                return affected

    def execute_many(self, sql: str, seq_params: Iterable[tuple]) -> int:
        """INSERT/UPDATE por lote. Devuelve filas afectadas."""
        _assert_no_ddl(sql)
        rows = list(seq_params)
        if not rows:
            return 0
        with self.connection() as conn:
            with conn.cursor() as cur:
                affected = cur.executemany(sql, rows)
                conn.commit()
                return affected

    def ping(self) -> bool:
        try:
            with self.connection() as conn:
                conn.ping(reconnect=True)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("MySQL ping fallo: %s", exc)
            return False


_client: Optional[MySQLClient] = None


def get_mysql() -> MySQLClient:
    global _client
    if _client is None:
        _client = MySQLClient()
    return _client
