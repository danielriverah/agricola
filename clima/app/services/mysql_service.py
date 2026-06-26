import logging
from contextlib import contextmanager
from datetime import date, datetime
from typing import Optional, List

import pymysql
from pymysql.cursors import DictCursor

from app.core.config import MYSQL_HOST, MYSQL_PORT, MYSQL_DB, MYSQL_USER, MYSQL_PASSWORD
from app.models.produccion_clima import ClimadiarioRecord

logger = logging.getLogger(__name__)


@contextmanager
def get_connection():
    conn = pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        db=MYSQL_DB,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=10,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def obtener_poligono(produccion_id: int) -> Optional[str]:
    """
    Retorna el campo 'poligono' de asignaciones_zonas_producciones para la producción dada.
    Formato esperado: 'lat,lon|lat,lon|...'
    """
    sql = """
        SELECT azp.poligono
        FROM producciones p
        JOIN asignaciones_zonas_producciones azp
            ON azp.produccion_id = p.produccion_id
        WHERE p.produccion_id = %s
        LIMIT 1
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (produccion_id,))
            row = cur.fetchone()
    return row[0] if row else None


def obtener_producciones_monitoreadas() -> list[dict]:
    """
    Retorna las producciones con monitoring = 1 desde MySQL.
    Esta consulta es la base del flujo: primero MySQL, luego DynamoDB para completar datos faltantes.
    """
    sql = """
        SELECT
            p.produccion_id,
            p.estatus,
            p.monitoring,
            p.fecha,
            (
                SELECT azp.poligono
                FROM asignaciones_zonas_producciones azp
                WHERE azp.produccion_id = p.produccion_id
                LIMIT 1
            ) AS poligono,
            (
                SELECT azp.area
                FROM asignaciones_zonas_producciones azp
                WHERE azp.produccion_id = p.produccion_id
                LIMIT 1
            ) AS area_asig
        FROM producciones p
        WHERE p.monitoring = 1
        ORDER BY p.produccion_id DESC
    """
    with get_connection() as conn:
        with conn.cursor(DictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchall() or []
    return rows


def contar_producciones_monitoreadas() -> int:
    sql = """
        SELECT COUNT(*) AS total
        FROM producciones p
        WHERE p.monitoring = 1
    """
    with get_connection() as conn:
        with conn.cursor(DictCursor) as cur:
            cur.execute(sql)
            row = cur.fetchone() or {}
    return int(row.get("total") or 0)


def obtener_producciones_monitoreadas_paginadas(page: int, page_size: int) -> list[dict]:
    offset = (page - 1) * page_size
    sql = """
        SELECT
            p.produccion_id,
            p.estatus,
            p.monitoring,
            p.fecha,
            (
                SELECT azp.poligono
                FROM asignaciones_zonas_producciones azp
                WHERE azp.produccion_id = p.produccion_id
                LIMIT 1
            ) AS poligono,
            (
                SELECT azp.area
                FROM asignaciones_zonas_producciones azp
                WHERE azp.produccion_id = p.produccion_id
                LIMIT 1
            ) AS area_asig
        FROM producciones p
        WHERE p.monitoring = 1
        ORDER BY p.produccion_id DESC
        LIMIT %s OFFSET %s
    """
    with get_connection() as conn:
        with conn.cursor(DictCursor) as cur:
            cur.execute(sql, (page_size, offset))
            rows = cur.fetchall() or []
    return rows


def obtener_ultima_fecha_confirmada(produccion_id: int) -> Optional[date]:
    """
    Retorna la fecha más reciente bloqueada (historico_confirmado) para la producción.
    """
    sql = """
        SELECT MAX(fecha)
        FROM produccion_clima_diario
        WHERE produccion_id = %s
          AND tipo_dato = 'historico_confirmado'
          AND bloqueado = 1
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (produccion_id,))
            row = cur.fetchone()
    if row and row[0]:
        return row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0]))
    return None


def obtener_ultimas_fechas_confirmadas(produccion_ids: list[int]) -> dict[int, date]:
    if not produccion_ids:
        return {}
    placeholders = ",".join(["%s"] * len(produccion_ids))
    sql = f"""
        SELECT produccion_id, MAX(fecha) AS fecha
        FROM produccion_clima_diario
        WHERE produccion_id IN ({placeholders})
          AND tipo_dato = 'historico_confirmado'
          AND bloqueado = 1
        GROUP BY produccion_id
    """
    with get_connection() as conn:
        with conn.cursor(DictCursor) as cur:
            cur.execute(sql, tuple(produccion_ids))
            rows = cur.fetchall() or []
    out: dict[int, date] = {}
    for row in rows:
        pid = int(row["produccion_id"])
        value = row["fecha"]
        if value:
            out[pid] = value if isinstance(value, date) else date.fromisoformat(str(value))
    return out


_UPSERT_SQL = """
    INSERT INTO produccion_clima_diario (
        produccion_id, fecha, tipo_dato, fuente, fecha_consulta, horizonte_dia,
        temp_max, temp_min, temp_prom, humedad_prom,
        precipitacion_mm, lluvia_mm, probabilidad_lluvia_max,
        viento_max_kmh, radiacion_solar_mj, evapotranspiracion_mm,
        riesgo_helada, riesgo_estres_hidrico, riesgo_lluvia,
        riesgo_helada_pct, riesgo_estres_hidrico_pct, riesgo_lluvia_pct,
        riesgo_viento_pct, riesgo_enfermedad_pct, riesgo_plaga_pct,
        recomendacion, raw_json, bloqueado,
        created_at, updated_at
    ) VALUES (
        %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        NOW(), NOW()
    )
    ON DUPLICATE KEY UPDATE
        tipo_dato             = IF(bloqueado = 1, tipo_dato, VALUES(tipo_dato)),
        fuente                = IF(bloqueado = 1, fuente, VALUES(fuente)),
        fecha_consulta        = IF(bloqueado = 1, fecha_consulta, VALUES(fecha_consulta)),
        horizonte_dia         = IF(bloqueado = 1, horizonte_dia, VALUES(horizonte_dia)),
        temp_max              = IF(bloqueado = 1, temp_max, VALUES(temp_max)),
        temp_min              = IF(bloqueado = 1, temp_min, VALUES(temp_min)),
        temp_prom             = IF(bloqueado = 1, temp_prom, VALUES(temp_prom)),
        humedad_prom          = IF(bloqueado = 1, humedad_prom, VALUES(humedad_prom)),
        precipitacion_mm      = IF(bloqueado = 1, precipitacion_mm, VALUES(precipitacion_mm)),
        lluvia_mm             = IF(bloqueado = 1, lluvia_mm, VALUES(lluvia_mm)),
        probabilidad_lluvia_max = IF(bloqueado = 1, probabilidad_lluvia_max, VALUES(probabilidad_lluvia_max)),
        viento_max_kmh        = IF(bloqueado = 1, viento_max_kmh, VALUES(viento_max_kmh)),
        radiacion_solar_mj    = IF(bloqueado = 1, radiacion_solar_mj, VALUES(radiacion_solar_mj)),
        evapotranspiracion_mm = IF(bloqueado = 1, evapotranspiracion_mm, VALUES(evapotranspiracion_mm)),
        riesgo_helada         = IF(bloqueado = 1, riesgo_helada, VALUES(riesgo_helada)),
        riesgo_estres_hidrico = IF(bloqueado = 1, riesgo_estres_hidrico, VALUES(riesgo_estres_hidrico)),
        riesgo_lluvia         = IF(bloqueado = 1, riesgo_lluvia, VALUES(riesgo_lluvia)),
        riesgo_helada_pct     = IF(bloqueado = 1, riesgo_helada_pct, VALUES(riesgo_helada_pct)),
        riesgo_estres_hidrico_pct = IF(bloqueado = 1, riesgo_estres_hidrico_pct, VALUES(riesgo_estres_hidrico_pct)),
        riesgo_lluvia_pct      = IF(bloqueado = 1, riesgo_lluvia_pct, VALUES(riesgo_lluvia_pct)),
        riesgo_viento_pct      = IF(bloqueado = 1, riesgo_viento_pct, VALUES(riesgo_viento_pct)),
        riesgo_enfermedad_pct  = IF(bloqueado = 1, riesgo_enfermedad_pct, VALUES(riesgo_enfermedad_pct)),
        riesgo_plaga_pct       = IF(bloqueado = 1, riesgo_plaga_pct, VALUES(riesgo_plaga_pct)),
        recomendacion         = IF(bloqueado = 1, recomendacion, VALUES(recomendacion)),
        raw_json              = IF(bloqueado = 1, raw_json, VALUES(raw_json)),
        bloqueado             = IF(bloqueado = 1, bloqueado, VALUES(bloqueado)),
        updated_at            = IF(bloqueado = 1, updated_at, NOW())
"""


def upsert_registros_clima(registros: List[ClimadiarioRecord]) -> int:
    """
    Inserta o actualiza los registros de clima.
    Respeta bloqueado=1: el ON DUPLICATE KEY no sobreescribe filas bloqueadas.
    Retorna el número de filas afectadas.
    """
    if not registros:
        return 0

    rows = [
        (
            r.produccion_id, r.fecha, r.tipo_dato, r.fuente,
            r.fecha_consulta, r.horizonte_dia,
            r.temp_max, r.temp_min, r.temp_prom, r.humedad_prom,
            r.precipitacion_mm, r.lluvia_mm, r.probabilidad_lluvia_max,
            r.viento_max_kmh, r.radiacion_solar_mj, r.evapotranspiracion_mm,
            r.riesgo_helada, r.riesgo_estres_hidrico, r.riesgo_lluvia,
            r.riesgo_helada_pct, r.riesgo_estres_hidrico_pct, r.riesgo_lluvia_pct,
            r.riesgo_viento_pct, r.riesgo_enfermedad_pct, r.riesgo_plaga_pct,
            r.recomendacion, r.raw_json_str(), r.bloqueado,
        )
        for r in registros
    ]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, rows)
            affected = cur.rowcount

    return affected


def obtener_historial_monitoreo_producciones(
    produccion_ids: list[int],
    limit_por_produccion: int = 30,
) -> dict[int, list[dict]]:
    """
    Retorna el historial de clima por producción, ordenado por fecha descendente.
    Se usa para construir gráficas y resúmenes por producción en la vista.
    """
    if not produccion_ids:
        return {}

    placeholders = ",".join(["%s"] * len(produccion_ids))
    sql = f"""
        SELECT
            produccion_id, fecha, tipo_dato, fuente,
            temp_max, temp_min, temp_prom,
            humedad_prom, precipitacion_mm, lluvia_mm,
            probabilidad_lluvia_max, viento_max_kmh,
            radiacion_solar_mj, evapotranspiracion_mm,
            riesgo_helada, riesgo_estres_hidrico, riesgo_lluvia,
            riesgo_helada_pct, riesgo_estres_hidrico_pct, riesgo_lluvia_pct,
            riesgo_viento_pct, riesgo_enfermedad_pct, riesgo_plaga_pct,
            recomendacion, bloqueado
        FROM produccion_clima_diario
        WHERE produccion_id IN ({placeholders})
        ORDER BY produccion_id ASC, fecha DESC
    """

    with get_connection() as conn:
        with conn.cursor(DictCursor) as cur:
            cur.execute(sql, tuple(produccion_ids))
            rows = cur.fetchall() or []

    grouped: dict[int, list[dict]] = {}
    for row in rows:
        pid = int(row.get("produccion_id"))
        bucket = grouped.setdefault(pid, [])
        if len(bucket) >= limit_por_produccion:
            continue
        cleaned = {}
        for k, v in row.items():
            cleaned[k] = v.isoformat() if hasattr(v, "isoformat") else v
        bucket.append(cleaned)

    return grouped


def obtener_resumen_monitoreo_paginado(
    page: int,
    page_size: int,
    limit_historial: int = 20,
) -> tuple[int, list[dict], dict[int, list[dict]]]:
    total = contar_producciones_monitoreadas()
    rows = obtener_producciones_monitoreadas_paginadas(page=page, page_size=page_size)

    produccion_ids: list[int] = []
    base_by_id: dict[int, dict] = {}
    for row in rows:
        try:
            pid = int(row.get("produccion_id"))
        except Exception:
            continue
        produccion_ids.append(pid)
        base_by_id[pid] = row

    historiales = obtener_historial_monitoreo_producciones(produccion_ids, limit_por_produccion=limit_historial)
    resultado: list[dict] = []
    for pid in produccion_ids:
        row = base_by_id.get(pid, {})
        historial = historiales.get(pid, [])
        serie_temperatura = [
            {
                "fecha": item.get("fecha"),
                "temp_max": item.get("temp_max"),
                "temp_min": item.get("temp_min"),
                "temp_prom": item.get("temp_prom"),
                "tipo_dato": item.get("tipo_dato"),
            }
            for item in historial
        ]
        temperaturas = [item.get("temp_prom") for item in historial if item.get("temp_prom") is not None]
        ultima_temperatura = serie_temperatura[0] if serie_temperatura else None
        resultado.append({
            "produccion_id": pid,
            "estatus": row.get("estatus"),
            "monitoring": row.get("monitoring"),
            "fecha": row.get("fecha").isoformat() if row.get("fecha") else None,
            "poligono": row.get("poligono"),
            "area_asig": row.get("area_asig"),
            "total_registros_clima": len(historial),
            "temp_prom_min": min(temperaturas) if temperaturas else None,
            "temp_prom_max": max(temperaturas) if temperaturas else None,
            "temp_prom_ultima": ultima_temperatura.get("temp_prom") if ultima_temperatura else None,
            "fecha_ultimo_registro": ultima_temperatura.get("fecha") if ultima_temperatura else None,
            "serie_temperatura": serie_temperatura,
            "historial_clima": historial,
        })

    return total, resultado, historiales
