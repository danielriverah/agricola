"""
Guard de solo lectura para MySQL.

Regla absoluta del README/contrato:
    TIF NUNCA ejecuta INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, TRUNCATE ni
    equivalentes contra ninguna base de datos.

Este módulo es una barrera defensiva: toda consulta pasa por aquí antes de
ejecutarse. Si detecta una sentencia de escritura, lanza ReadOnlyViolation y la
consulta no llega a la base de datos.
"""

from __future__ import annotations

import re


class ReadOnlyViolation(RuntimeError):
    pass


# Palabras clave que implican escritura o cambio de estructura/estado.
_FORBIDDEN = {
    "INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE", "UPSERT",
    "CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME",
    "GRANT", "REVOKE", "LOCK", "UNLOCK",
    "CALL", "DO", "HANDLER", "LOAD",
    "COMMIT", "ROLLBACK", "SAVEPOINT", "START", "BEGIN",
    "SET",  # evita SET que cambie estado de sesión de forma no controlada
}

# Comentarios SQL para limpiar antes de analizar.
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"--[^\n]*")
_COMMENT_HASH = re.compile(r"#[^\n]*")


def _strip(sql: str) -> str:
    sql = _COMMENT_BLOCK.sub(" ", sql)
    sql = _COMMENT_LINE.sub(" ", sql)
    sql = _COMMENT_HASH.sub(" ", sql)
    return sql.strip()


def assert_read_only(sql: str) -> None:
    """Lanza ReadOnlyViolation si `sql` no es estrictamente de lectura."""
    cleaned = _strip(sql)
    if not cleaned:
        raise ReadOnlyViolation("Consulta vacía.")

    # Rechazar múltiples sentencias (posible inyección de escritura).
    # Permitimos un ';' final opcional.
    body = cleaned.rstrip(";")
    if ";" in body:
        raise ReadOnlyViolation("No se permiten múltiples sentencias por consulta.")

    first = re.match(r"\s*([A-Za-z]+)", cleaned)
    if not first:
        raise ReadOnlyViolation("No se pudo determinar el tipo de sentencia.")

    verb = first.group(1).upper()
    if verb not in {"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"}:
        raise ReadOnlyViolation(f"Sentencia no permitida (solo lectura): {verb}")

    # Defensa adicional: si aparece alguna palabra prohibida como token suelto.
    tokens = set(re.findall(r"[A-Za-z_]+", cleaned.upper()))
    hit = tokens & _FORBIDDEN
    # 'SET' dentro de funciones de agregación no es relevante; pero por
    # simplicidad y seguridad, lo bloqueamos como verbo inicial (ya cubierto).
    hit.discard("SET")  # SET solo se bloquea como verbo inicial
    hit.discard("START")
    hit.discard("BEGIN")
    if hit:
        raise ReadOnlyViolation(
            "La consulta contiene palabras clave de escritura: " + ", ".join(sorted(hit))
        )
