import logging
from datetime import date, datetime
from typing import Dict, List, Optional

import httpx

from app.core.config import OPENMETEO_FORECAST_URL, OPENMETEO_ARCHIVE_URL, OPENMETEO_TIMEZONE
from app.models.produccion_clima import ClimadiarioRecord

logger = logging.getLogger(__name__)

# ─── Parámetros de petición ───────────────────────────────────────────────────

# La API histórica no tiene precipitation_probability_max (es un dato de pronóstico)
_DAILY_HISTORICAL = (
    "temperature_2m_max,temperature_2m_min,precipitation_sum,"
    "rain_sum,wind_speed_10m_max,shortwave_radiation_sum,"
    "et0_fao_evapotranspiration_sum"
)
_DAILY_FORECAST = (
    "temperature_2m_max,temperature_2m_min,precipitation_sum,"
    "precipitation_probability_max,wind_speed_10m_max,"
    "shortwave_radiation_sum,et0_fao_evapotranspiration_sum"
)

# relative_humidity_2m solo existe a nivel hourly en Open-Meteo.
# La pedimos en ambas APIs y calculamos el promedio diario de las 24 horas.
_HOURLY_HUMIDITY = "relative_humidity_2m"


# ─── Utilidades ───────────────────────────────────────────────────────────────

def _safe_float(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _calcular_humedad_diaria(hourly: dict, fechas_daily: List[str]) -> Dict[str, Optional[float]]:
    """
    Agrupa los valores horarios de relative_humidity_2m por día y calcula el promedio.

    Open-Meteo devuelve las horas en formato "YYYY-MM-DDTHH:MM".
    Cada día tiene exactamente 24 entradas. El resultado es un dict
    {fecha_iso: humedad_prom} donde fecha_iso == "YYYY-MM-DD".

    Si la respuesta no contiene datos horarios, retorna {fecha: None} para cada día.
    """
    valores_hora = hourly.get("relative_humidity_2m", [])
    tiempos_hora = hourly.get("time", [])

    if not valores_hora or not tiempos_hora:
        return {f: None for f in fechas_daily}

    # Agrupa: {fecha_str: [val1, val2, ...24 vals]}
    por_dia: Dict[str, list] = {}
    for t, v in zip(tiempos_hora, valores_hora):
        fecha_str = t[:10]  # "YYYY-MM-DD"
        if fecha_str in set(fechas_daily):
            por_dia.setdefault(fecha_str, [])
            if v is not None:
                por_dia[fecha_str].append(float(v))

    resultado: Dict[str, Optional[float]] = {}
    for f in fechas_daily:
        vals = por_dia.get(f)
        resultado[f] = round(sum(vals) / len(vals), 2) if vals else None

    return resultado


def _record_from_daily(
    produccion_id: int,
    fecha: date,
    tipo_dato: str,
    fuente: str,
    horizonte_dia: Optional[int],
    bloqueado: int,
    dia_data: dict,
    fecha_consulta: datetime,
    humedad_prom: Optional[float] = None,
) -> ClimadiarioRecord:
    temp_max = _safe_float(dia_data.get("temperature_2m_max"))
    temp_min = _safe_float(dia_data.get("temperature_2m_min"))
    temp_prom = (
        round((temp_max + temp_min) / 2, 2)
        if temp_max is not None and temp_min is not None
        else None
    )

    rec = ClimadiarioRecord(
        produccion_id=produccion_id,
        fecha=fecha,
        tipo_dato=tipo_dato,
        fuente=fuente,
        fecha_consulta=fecha_consulta,
        horizonte_dia=horizonte_dia,
        temp_max=temp_max,
        temp_min=temp_min,
        temp_prom=temp_prom,
        humedad_prom=humedad_prom,       # ← viene de los datos horarios
        precipitacion_mm=_safe_float(dia_data.get("precipitation_sum")),
        lluvia_mm=_safe_float(dia_data.get("rain_sum")),
        # precipitation_probability_max solo existe en forecast_api; en historical_api es None
        probabilidad_lluvia_max=_safe_float(dia_data.get("precipitation_probability_max")),
        viento_max_kmh=_safe_float(dia_data.get("wind_speed_10m_max")),
        radiacion_solar_mj=_safe_float(dia_data.get("shortwave_radiation_sum")),
        evapotranspiracion_mm=_safe_float(dia_data.get("et0_fao_evapotranspiration_sum")),
        bloqueado=bloqueado,
        raw_json=dia_data,
    )
    rec.calcular_riesgos()
    return rec


# ─── Consultas públicas ───────────────────────────────────────────────────────

def consultar_historico(
    produccion_id: int,
    lat: float,
    lon: float,
    fecha_inicio: date,
    fecha_fin: date,
) -> List[ClimadiarioRecord]:
    """
    Consulta Open-Meteo Archive API para el rango [fecha_inicio, fecha_fin].
    Todos los registros se guardan como historico_confirmado, bloqueado=1.

    Nota: precipitation_probability_max NO existe en la API histórica,
    por lo que probabilidad_lluvia_max quedará NULL en todos los registros históricos.
    """
    if fecha_inicio > fecha_fin:
        return []

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": fecha_inicio.isoformat(),
        "end_date": fecha_fin.isoformat(),
        "daily": _DAILY_HISTORICAL,
        "hourly": _HOURLY_HUMIDITY,
        "timezone": OPENMETEO_TIMEZONE,
    }

    logger.debug("Histórico request produccion_id=%d %s→%s", produccion_id, fecha_inicio, fecha_fin)
    with httpx.Client(timeout=60) as client:
        response = client.get(OPENMETEO_ARCHIVE_URL, params=params)
        response.raise_for_status()

    data = response.json()
    daily = data.get("daily", {})
    hourly = data.get("hourly", {})
    fechas: List[str] = daily.get("time", [])

    humedad_por_dia = _calcular_humedad_diaria(hourly, fechas)
    fecha_consulta = datetime.utcnow()
    registros: List[ClimadiarioRecord] = []

    for i, fecha_str in enumerate(fechas):
        fecha = date.fromisoformat(fecha_str)
        dia_data = {k: v[i] for k, v in daily.items() if k != "time" and isinstance(v, list)}
        rec = _record_from_daily(
            produccion_id=produccion_id,
            fecha=fecha,
            tipo_dato="historico_confirmado",
            fuente="historical_api",
            horizonte_dia=None,
            bloqueado=1,
            dia_data=dia_data,
            fecha_consulta=fecha_consulta,
            humedad_prom=humedad_por_dia.get(fecha_str),
        )
        registros.append(rec)

    logger.info(
        "Histórico produccion_id=%d: %d días obtenidos (%s→%s)",
        produccion_id, len(registros), fecha_inicio, fecha_fin,
    )
    return registros


def consultar_forecast(
    produccion_id: int,
    lat: float,
    lon: float,
) -> List[ClimadiarioRecord]:
    """
    Consulta Open-Meteo Forecast API (hoy + 5 días).
    Hoy → tipo_dato='actual', días siguientes → tipo_dato='forecast'.

    precipitation_probability_max SÍ existe en la forecast API y se almacena
    en probabilidad_lluvia_max. Los valores altos (80-100%) durante temporada
    de lluvias son correctos y esperados.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": _DAILY_FORECAST,
        "hourly": _HOURLY_HUMIDITY,
        "forecast_days": 6,
        "timezone": OPENMETEO_TIMEZONE,
    }

    logger.debug("Forecast request produccion_id=%d", produccion_id)
    with httpx.Client(timeout=30) as client:
        response = client.get(OPENMETEO_FORECAST_URL, params=params)
        response.raise_for_status()

    data = response.json()
    daily = data.get("daily", {})
    hourly = data.get("hourly", {})
    fechas: List[str] = daily.get("time", [])

    humedad_por_dia = _calcular_humedad_diaria(hourly, fechas)
    hoy = date.today()
    fecha_consulta = datetime.utcnow()
    registros: List[ClimadiarioRecord] = []

    for i, fecha_str in enumerate(fechas):
        fecha = date.fromisoformat(fecha_str)
        horizonte = (fecha - hoy).days
        tipo = "actual" if horizonte == 0 else "forecast"
        dia_data = {k: v[i] for k, v in daily.items() if k != "time" and isinstance(v, list)}
        rec = _record_from_daily(
            produccion_id=produccion_id,
            fecha=fecha,
            tipo_dato=tipo,
            fuente="forecast_api",
            horizonte_dia=horizonte,
            bloqueado=0,
            dia_data=dia_data,
            fecha_consulta=fecha_consulta,
            humedad_prom=humedad_por_dia.get(fecha_str),
        )
        registros.append(rec)

    logger.info(
        "Forecast produccion_id=%d: %d días obtenidos", produccion_id, len(registros)
    )
    return registros
