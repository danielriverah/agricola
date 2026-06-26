import logging
from datetime import date, timedelta
from typing import Optional

from app.models.produccion_clima import ProduccionMonitoreo
from app.services.mysql_service import (
    obtener_poligono,
    obtener_ultima_fecha_confirmada,
    upsert_registros_clima,
)
from app.services.openmeteo_service import consultar_historico, consultar_forecast
from app.utils.geo_utils import primer_punto_poligono

logger = logging.getLogger(__name__)


def _resolver_fecha_inicio_historico(
    produccion_id: int, fecha_siembra: date
) -> date:
    ultima = obtener_ultima_fecha_confirmada(produccion_id)
    if ultima:
        return ultima + timedelta(days=1)
    return fecha_siembra


def _historial_ya_bloqueado_hasta_ayer(produccion_id: int) -> bool:
    ultima = obtener_ultima_fecha_confirmada(produccion_id)
    if not ultima:
        return False
    ayer = date.today() - timedelta(days=1)
    return ultima >= ayer


def sincronizar_produccion(prod: ProduccionMonitoreo) -> dict:
    """
    Ejecuta el ciclo completo de sincronización climática para una producción.
    Retorna un resumen con métricas del proceso.
    """
    produccion_id = prod.produccion_id
    resultado = {
        "produccion_id": produccion_id,
        "ok": False,
        "historico_dias": 0,
        "forecast_dias": 0,
        "filas_afectadas": 0,
        "stage": "init",
        "historico_intentado": False,
        "forecast_intentado": False,
        "error": None,
    }

    try:
        # 1. Obtener coordenadas desde MySQL
        resultado["stage"] = "mysql_polygon"
        poligono = obtener_poligono(produccion_id)
        if not poligono:
            raise ValueError(f"Sin polígono en MySQL para produccion_id={produccion_id}")

        coords = primer_punto_poligono(poligono)
        if not coords:
            raise ValueError(f"Polígono con formato inválido para produccion_id={produccion_id}: {poligono[:60]}")

        lat, lon = coords
        logger.info("Produccion %d → lat=%.6f lon=%.6f", produccion_id, lat, lon)

        # 2. Calcular rango histórico faltante
        hoy = date.today()
        ayer = hoy - timedelta(days=1)
        registros_historico = []
        if _historial_ya_bloqueado_hasta_ayer(produccion_id):
            logger.debug(
                "Produccion %d: ayer ya está bloqueado, se omite histórico y solo se actualiza forecast.",
                produccion_id,
            )
        else:
            fecha_inicio = _resolver_fecha_inicio_historico(produccion_id, prod.fecha_siembra)
            if fecha_inicio <= ayer:
                resultado["stage"] = "historico"
                resultado["historico_intentado"] = True
                registros_historico = consultar_historico(
                    produccion_id=produccion_id,
                    lat=lat,
                    lon=lon,
                    fecha_inicio=fecha_inicio,
                    fecha_fin=ayer,
                )
            else:
                logger.debug(
                    "Produccion %d: historial al día, no hay días nuevos que confirmar.",
                    produccion_id,
                )

        # 3. Forecast (hoy + 5 días)
        resultado["stage"] = "forecast"
        resultado["forecast_intentado"] = True
        registros_forecast = consultar_forecast(
            produccion_id=produccion_id,
            lat=lat,
            lon=lon,
        )

        # 4. Upsert en MySQL
        resultado["stage"] = "mysql_upsert"
        todos = registros_historico + registros_forecast
        filas = upsert_registros_clima(todos)

        resultado.update({
            "ok": True,
            "historico_dias": len(registros_historico),
            "forecast_dias": len(registros_forecast),
            "filas_afectadas": filas,
            "stage": "done",
        })

    except Exception as e:
        logger.error("Error sincronizando produccion_id=%d: %s", produccion_id, e, exc_info=True)
        resultado["error"] = f"[{resultado['stage']}] {e}"

    return resultado
