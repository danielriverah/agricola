from typing import Optional
from fastapi import APIRouter, Query
import pymysql
from app.core.config import MYSQL_HOST, MYSQL_PORT, MYSQL_DB, MYSQL_USER, MYSQL_PASSWORD

router = APIRouter(prefix="/registros", tags=["registros"])


def _get_conn():
    return pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT, db=MYSQL_DB,
        user=MYSQL_USER, password=MYSQL_PASSWORD,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )


@router.get("/{produccion_id}")
def obtener_registros_clima(
    produccion_id: int,
    tipo_dato: Optional[str] = Query(None, description="historico_confirmado | actual | forecast"),
    limit: int = Query(30, ge=1, le=500),
):
    """
    Devuelve los registros de produccion_clima_diario para la producción dada,
    ordenados por fecha descendente.
    """
    where = "WHERE produccion_id = %s"
    params: list = [produccion_id]
    if tipo_dato:
        where += " AND tipo_dato = %s"
        params.append(tipo_dato)

    sql = f"""
        SELECT
            produccion_clima_diario_id, produccion_id, fecha, tipo_dato, fuente,
            fecha_consulta, horizonte_dia,
            temp_max, temp_min, temp_prom, humedad_prom,
            precipitacion_mm, lluvia_mm, probabilidad_lluvia_max,
            viento_max_kmh, radiacion_solar_mj, evapotranspiracion_mm,
            riesgo_helada, riesgo_estres_hidrico, riesgo_lluvia,
            recomendacion, bloqueado,
            created_at, updated_at
        FROM produccion_clima_diario
        {where}
        ORDER BY fecha DESC
        LIMIT %s
    """
    params.append(limit)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    # Serializar tipos date/datetime
    for row in rows:
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()

    return {"produccion_id": produccion_id, "total": len(rows), "registros": rows}
