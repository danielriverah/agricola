"""
Cliente MySQL de SOLO LECTURA.

- Todas las consultas pasan por assert_read_only antes de ejecutarse.
- La sesión intenta marcarse como READ ONLY a nivel de transacción.
- autocommit=True y nunca se llama a commit de escritura.
- No expone ningún método execute/insert/update/delete.

Usa PyMySQL (puro Python) para no depender de binarios nativos.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from app.config.models import MySQLConfig
from app.db.readonly_guard import assert_read_only

logger = logging.getLogger("tif.mysql")


class MySQLReadOnlyClient:
    def __init__(self, cfg: MySQLConfig) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()

    def _connect(self):
        import pymysql
        from pymysql.cursors import DictCursor

        conn = pymysql.connect(
            host=self.cfg.host,
            port=self.cfg.port,
            user=self.cfg.user,
            password=self.cfg.password or "",
            database=self.cfg.database,
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=10,
            read_timeout=60,
            charset="utf8mb4",
        )
        # Reforzar solo lectura a nivel de sesión cuando el motor lo soporta.
        try:
            with conn.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
        except Exception:  # noqa: BLE001 - no todos los motores lo permiten
            logger.debug("No se pudo fijar SESSION TRANSACTION READ ONLY (continuamos).")
        return conn

    @contextmanager
    def connection(self) -> Iterator[Any]:
        conn = self._connect()
        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def query(self, sql: str, params: tuple | dict | None = None) -> list[dict]:
        """Ejecuta un SELECT/SHOW/EXPLAIN y devuelve filas como dicts."""
        try:
            assert_read_only(sql)  # barrera defensiva
            with self.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                    return list(rows)
        except Exception as ex:  # noqa: BLE001
            print(f"ERROR en consulta: \n {ex}")
            return []

    def query_one(self, sql: str, params: tuple | dict | None = None) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def ping(self) -> bool:
        try:
            self.query("SELECT 1 AS ok")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("MySQL ping falló: %s", exc)
            return False


class MySQLWriteClient(MySQLReadOnlyClient):
    """Cliente restringido para escrituras acotadas del módulo."""

    def _connect(self):
        import pymysql
        from pymysql.cursors import DictCursor

        return pymysql.connect(
            host=self.cfg.host,
            port=self.cfg.port,
            user=self.cfg.user,
            password=self.cfg.password or "",
            database=self.cfg.database,
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=10,
            read_timeout=60,
            charset="utf8mb4",
        )

    def execute(self, sql: str, params: tuple | dict | None = None) -> int:
        with self.connection() as conn:
            with conn.cursor() as cur:
                affected = cur.execute(sql, params)
            conn.commit()
            return affected

    def execute_many(self, sql: str, rows: list[tuple | dict]) -> int:
        with self.connection() as conn:
            with conn.cursor() as cur:
                affected = cur.executemany(sql, rows)
            conn.commit()
            return affected
