"""Conexion MySQL (pool) y creacion de esquema."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import mysql.connector
from mysql.connector import pooling
from mysql.connector.connection import MySQLConnection

from app.core.app_config import MySQLConfig

logger = logging.getLogger(__name__)

_POOL: pooling.MySQLConnectionPool | None = None


def init_pool(cfg: MySQLConfig, pool_size: int = 5) -> None:
    """Inicializa el pool global de conexiones MySQL."""
    global _POOL
    _POOL = pooling.MySQLConnectionPool(
        pool_name="schedule_pool",
        pool_size=pool_size,
        host=cfg.host,
        port=cfg.port,
        database=cfg.database,
        user=cfg.user,
        password=cfg.password,
        autocommit=False,
        charset="utf8mb4",
        collation="utf8mb4_unicode_ci",
    
    )
    logger.info("Pool MySQL inicializado host=%s db=%s", cfg.host, cfg.database)


def get_pool() -> pooling.MySQLConnectionPool:
    if _POOL is None:
        raise RuntimeError("Pool MySQL no inicializado. Llama init_pool primero.")
    return _POOL


@contextmanager
def get_connection() -> Iterator[MySQLConnection]:
    """Entrega una conexion del pool y la devuelve al terminar."""
    conn = get_pool().get_connection()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_cursor(dictionary: bool = True, commit: bool = False) -> Iterator:
    """Cursor con manejo de commit/rollback."""
    with get_connection() as conn:
        cursor = conn.cursor(dictionary=dictionary)
        try:
            yield cursor
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS schedule_task (
        schedule_task_id        BIGINT AUTO_INCREMENT PRIMARY KEY,
        task_name               VARCHAR(255) NOT NULL,
        enabled                 TINYINT(1) NOT NULL DEFAULT 1,
        completed               TINYINT(1) NOT NULL DEFAULT 0,
        service_name            VARCHAR(255) NULL,
        visibility              VARCHAR(20) NOT NULL DEFAULT 'public',
        endpoint                VARCHAR(1024) NOT NULL,
        method                  VARCHAR(10) NOT NULL DEFAULT 'POST',
        query_json              JSON NULL,
        body_json               JSON NULL,
        auth_type               VARCHAR(20) NOT NULL DEFAULT 'none',
        auth_header_name        VARCHAR(255) NULL,
        auth_token              TEXT NULL,
        trae_job_id             TINYINT(1) NOT NULL DEFAULT 0,
        ruta_job_id             VARCHAR(512) NULL,
        hora_ejecucion          VARCHAR(8) NULL,
        zona_horaria            VARCHAR(64) NULL,
        daily                   TINYINT(1) NOT NULL DEFAULT 0,
        run_at                  DATETIME NULL,
        cantidad_ejecuciones    INT NULL,
        ejecuciones_realizadas  INT NOT NULL DEFAULT 0,
        last_run_at             DATETIME NULL,
        next_run_at             DATETIME NULL,
        running                 TINYINT(1) NOT NULL DEFAULT 0,
        locked_at               DATETIME NULL,
        created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_next_run (enabled, completed, next_run_at),
        INDEX idx_running (running, locked_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS schedule_historia (
        schedule_historia_id    BIGINT AUTO_INCREMENT PRIMARY KEY,
        schedule_task_id        BIGINT NULL,
        fecha_ejecucion         DATETIME NOT NULL,
        service_name            VARCHAR(255) NULL,
        visibility              VARCHAR(20) NULL,
        endpoint                VARCHAR(1024) NULL,
        final_url               VARCHAR(2048) NULL,
        method                  VARCHAR(10) NULL,
        http_status_code        INT NULL,
        status                  VARCHAR(20) NOT NULL,
        job_id                  VARCHAR(512) NULL,
        observaciones           TEXT NULL,
        request_json            JSON NULL,
        response_json           JSON NULL,
        error_message           TEXT NULL,
        created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_task (schedule_task_id),
        INDEX idx_fecha (fecha_ejecucion),
        CONSTRAINT fk_hist_task FOREIGN KEY (schedule_task_id)
            REFERENCES schedule_task (schedule_task_id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def create_schema() -> None:
    """Crea las tablas si no existen."""
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            for stmt in SCHEMA_STATEMENTS:
                cursor.execute(stmt)
            conn.commit()
            logger.info("Esquema MySQL verificado/creado.")
        finally:
            cursor.close()
