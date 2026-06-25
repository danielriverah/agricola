"""Utilidades de tiempo y calculo de proxima ejecucion.

Todas las marcas de tiempo se almacenan en UTC (naive en UTC) en MySQL.
El calculo de hora local usa la zona horaria de la tarea.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    """Ahora en UTC, naive (sin tzinfo) para almacenar en MySQL DATETIME."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_zone(tz_name: str | None, default: str = "America/Mexico_City") -> ZoneInfo:
    try:
        return ZoneInfo(tz_name) if tz_name else ZoneInfo(default)
    except Exception:
        return ZoneInfo(default)


def parse_hhmmss(value: str) -> time:
    """Convierte 'HH:MM:SS' o 'HH:MM' a datetime.time."""
    parts = [int(p) for p in value.strip().split(":")]
    while len(parts) < 3:
        parts.append(0)
    h, m, s = parts[0], parts[1], parts[2]
    return time(hour=h, minute=m, second=s)


def to_utc_naive(dt_local: datetime, zone: ZoneInfo) -> datetime:
    """Toma un datetime local (con o sin tz) y lo devuelve en UTC naive."""
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=zone)
    return dt_local.astimezone(timezone.utc).replace(tzinfo=None)


def compute_next_daily_run(
    hora_ejecucion: str,
    tz_name: str,
    *,
    after: datetime | None = None,
    default_tz: str = "America/Mexico_City",
) -> datetime:
    """Calcula el proximo DATETIME (UTC naive) para una tarea diaria.

    Se busca el proximo instante local cuya hora == hora_ejecucion
    y que sea estrictamente > `after`.
    """
    zone = get_zone(tz_name, default_tz)
    after_utc = after or utcnow()
    # after esta en UTC naive -> lo llevamos a local con tz para comparar.
    after_local = after_utc.replace(tzinfo=timezone.utc).astimezone(zone)

    target_t = parse_hhmmss(hora_ejecucion)
    candidate_local = after_local.replace(
        hour=target_t.hour,
        minute=target_t.minute,
        second=target_t.second,
        microsecond=0,
    )
    if candidate_local <= after_local:
        candidate_local = candidate_local + timedelta(days=1)

    return candidate_local.astimezone(timezone.utc).replace(tzinfo=None)


def normalize_run_at(run_at: datetime, tz_name: str, default_tz: str = "America/Mexico_City") -> datetime:
    """Normaliza un run_at a UTC naive.

    Si llega sin tzinfo se asume que esta expresado en la zona horaria de la tarea.
    """
    zone = get_zone(tz_name, default_tz)
    return to_utc_naive(run_at, zone)
