"""
Utilidades de geometría para TIF.

Los campos `tile_bbox` y `poligono` pueden venir en distintos formatos según el
proceso que los escribió: GeoJSON, WKT, lista [minx,miny,maxx,maxy], o string.
Este módulo normaliza todo eso a objetos de shapely.
"""

from __future__ import annotations

import json
from typing import Any

from shapely import wkt
from shapely.geometry import Polygon, box, shape
from shapely.geometry.base import BaseGeometry


def parse_bbox(value: Any) -> tuple[float, float, float, float] | None:
    """Devuelve (minx, miny, maxx, maxy) o None."""
    if value is None:
        return None

    # Lista/tupla directa.
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return tuple(float(v) for v in value)  # type: ignore[return-value]

    if isinstance(value, str):
        s = value.strip()
        # JSON array.
        if s.startswith("["):
            try:
                arr = json.loads(s)
                if isinstance(arr, list) and len(arr) == 4:
                    return tuple(float(v) for v in arr)  # type: ignore[return-value]
            except Exception:  # noqa: BLE001
                pass
        # JSON dict / GeoJSON / bbox serializado.
        if s.startswith("{"):
            try:
                obj = json.loads(s)
                parsed = parse_bbox(obj)
                if parsed is not None:
                    return parsed
                geom = shape(obj)
                return geom.bounds
            except Exception:  # noqa: BLE001
                pass
        # CSV "minx,miny,maxx,maxy".
        if "," in s:
            parts = [p.strip() for p in s.split(",")]
            if len(parts) == 4:
                try:
                    return tuple(float(p) for p in parts)  # type: ignore[return-value]
                except ValueError:
                    pass
        # WKT.
        try:
            return wkt.loads(s).bounds
        except Exception:  # noqa: BLE001
            return None

    if isinstance(value, dict):
        keys = set(value.keys())
        if {"min_lon", "min_lat", "max_lon", "max_lat"}.issubset(keys):
            try:
                return (
                    float(value["min_lon"]),
                    float(value["min_lat"]),
                    float(value["max_lon"]),
                    float(value["max_lat"]),
                )
            except Exception:  # noqa: BLE001
                pass
        if "pbox" in keys and isinstance(value.get("pbox"), (list, tuple)) and len(value["pbox"]) == 4:
            try:
                arr = value["pbox"]
                return tuple(float(v) for v in arr)  # type: ignore[return-value]
            except Exception:  # noqa: BLE001
                pass
        try:
            return shape(value).bounds
        except Exception:  # noqa: BLE001
            return None

    return None


def _latlon_points_to_polygon(points: list[Any]) -> BaseGeometry | None:
    try:
        coords: list[tuple[float, float]] = []
        for item in points:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                return None
            lat = float(item[0])
            lon = float(item[1])
            coords.append((lon, lat))
        if len(coords) < 3:
            return None
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        return Polygon(coords)
    except Exception:  # noqa: BLE001
        return None


def parse_polygon(value: Any) -> BaseGeometry | None:
    """Devuelve una geometría shapely (Polygon/MultiPolygon) o None."""
    if value is None:
        return None

    if isinstance(value, BaseGeometry):
        return value

    if isinstance(value, dict):
        try:
            return shape(value)
        except Exception:  # noqa: BLE001
            return None

    if isinstance(value, (list, tuple)):
        polygon = _latlon_points_to_polygon(list(value))
        if polygon is not None:
            return polygon
        return None

    if isinstance(value, str):
        s = value.strip()
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                polygon = parse_polygon(parsed)
                if polygon is not None:
                    return polygon
            except Exception:  # noqa: BLE001
                pass
        if s.startswith("{"):
            try:
                return shape(json.loads(s))
            except Exception:  # noqa: BLE001
                pass
        try:
            return wkt.loads(s)
        except Exception:  # noqa: BLE001
            return None

    return None


def bbox_to_geom(bbox: tuple[float, float, float, float]) -> BaseGeometry:
    return box(*bbox)
