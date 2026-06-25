"""
Transformadores de datos: convierten la fuente de verdad (Dynamo / S3)
a las filas que esperan las tablas MySQL.

Mapeo confirmado con los esquemas y ejemplos reales del proyecto.
"""
import hashlib
import json
import math
import os
from typing import Any, Optional


# ----------------------------------------------------------------------------
# Producciones:  production_monitoring (Dynamo) -> s3_monitoring_producciones
# ----------------------------------------------------------------------------
def production_dynamo_to_mysql(item: dict, prefix_template: str) -> dict:
    """
    Dynamo:
      produccion_id, folio, dias_max_monitoreo, estatus, fecha_siembra,
      pbox{max_lat,max_lon,min_lat,min_lon,pbox[],puntos_bbox[]},
      ultima_fecha_consultada
    MySQL s3_monitoring_producciones:
      produccion_id, prefix, monitoring, max_dias_monitoring, fecha_fin,
      fecha_plantacion, pbox, polygon_bbox, tile_*
    """
    production_id = item.get("produccion_id")
    pbox_obj = item.get("pbox") or {}
    if isinstance(pbox_obj, str):
        try:
            pbox_obj = json.loads(pbox_obj)
        except json.JSONDecodeError:
            pbox_obj = {}

    # polygon_bbox: lista de puntos del bbox si viene
    polygon_bbox = pbox_obj.get("puntos_bbox")

    monitoring = 1 if str(item.get("estatus", "")).upper() == "OPEN" else 0

    prefix = prefix_template.format(production_id=production_id)

    return {
        "produccion_id": production_id,
        "prefix": prefix,
        "monitoring": monitoring,
        "max_dias_monitoring": item.get("dias_max_monitoreo"),
        "fecha_fin": None,
        "fecha_plantacion": item.get("fecha_siembra"),
        "pbox": json.dumps(pbox_obj) if pbox_obj else None,
        "polygon_bbox": json.dumps(polygon_bbox) if polygon_bbox else None,
        "tile_bbox": None,
        "tile_center_lat": None,
        "tile_center_lon": None,
        "tile_edge_meters": None,
    }


def geometry_dynamo_to_mysql(item: dict) -> dict:
    """Extrae geometría base persistible desde production_monitoring."""
    pbox_obj = item.get("pbox") or {}
    if isinstance(pbox_obj, str):
        try:
            pbox_obj = json.loads(pbox_obj)
        except json.JSONDecodeError:
            pbox_obj = {}
    polygon_bbox = pbox_obj.get("puntos_bbox")
    return {
        "pbox": json.dumps(pbox_obj) if pbox_obj else None,
        "polygon_bbox": json.dumps(polygon_bbox) if polygon_bbox else None,
    }


def geometry_center_from_pbox(item: dict) -> dict[str, float | None]:
    """Calcula un centro simple a partir del polígono/bbox de pbox."""
    pbox_obj = item.get("pbox") or {}
    if isinstance(pbox_obj, str):
        try:
            pbox_obj = json.loads(pbox_obj)
        except json.JSONDecodeError:
            pbox_obj = {}
    points = pbox_obj.get("puntos_bbox") or []
    if not points:
        return {"tile_center_lat": None, "tile_center_lon": None}

    lats: list[float] = []
    lons: list[float] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            lats.append(float(point[0]))
            lons.append(float(point[1]))
        except (TypeError, ValueError):
            continue

    if not lats or not lons:
        return {"tile_center_lat": None, "tile_center_lon": None}

    return {
        "tile_center_lat": (min(lats) + max(lats)) / 2,
        "tile_center_lon": (min(lons) + max(lons)) / 2,
    }


def polygon_text_to_json_array(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    text = str(value).strip()
    if not text:
        return None
    points: list[list[float]] = []
    for chunk in text.split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [part.strip() for part in chunk.split(",")]
        if len(parts) < 2:
            continue
        try:
            lat = float(parts[0])
            lon = float(parts[1])
        except ValueError:
            continue
        points.append([lat, lon])
    return json.dumps(points, ensure_ascii=False) if points else None


def geometry_tile_from_center(
    tile_center_lat: float | None,
    tile_center_lon: float | None,
    tile_edge_meters: int | None,
) -> dict[str, Any]:
    """Construye un tile aproximado en lat/lon a partir del centro y tamaño en metros."""
    if tile_center_lat is None or tile_center_lon is None:
        return {"tile_bbox": None, "pbox": None}
    if tile_edge_meters is None:
        return {"tile_bbox": None, "pbox": None}

    half_side_meters = float(tile_edge_meters) / 2.0
    meters_per_degree_lat = 111_320.0
    meters_per_degree_lon = 111_320.0 * max(0.01, abs(math.cos(math.radians(tile_center_lat))))

    delta_lat = half_side_meters / meters_per_degree_lat
    delta_lon = half_side_meters / meters_per_degree_lon

    min_lat = tile_center_lat - delta_lat
    max_lat = tile_center_lat + delta_lat
    min_lon = tile_center_lon - delta_lon
    max_lon = tile_center_lon + delta_lon

    polygon = [
        [min_lat, min_lon],
        [min_lat, max_lon],
        [max_lat, max_lon],
        [max_lat, min_lon],
    ]
    tile_box = {
        "min_lat": min_lat,
        "max_lat": max_lat,
        "min_lon": min_lon,
        "max_lon": max_lon,
        "puntos_bbox": polygon,
        "pbox": [min_lon, min_lat, max_lon, max_lat],
    }
    return {
        "tile_bbox": json.dumps(tile_box),
        "pbox": json.dumps(tile_box),
        "polygon_bbox": json.dumps(tile_box),
    }


# ----------------------------------------------------------------------------
# Escenas:  production_monitoring_detalle (Dynamo) -> s3_monitoring_escenas
# ----------------------------------------------------------------------------
def scene_dynamo_to_mysql(item: dict, include_scene_json: bool = False) -> dict:
    """
    Dynamo:
      id = 'PROD#<id>', clave = scene_name, fecha, cloud_cover,
      preview_json/svg/image, procesado, renderizado, scene_created, ...
    MySQL s3_monitoring_escenas:
      scene_name, fecha, scene_json_key, scene_json_uri, cloud_cover, status
    """
    scene_name = item.get("clave")
    preview_json = item.get("preview_json")  # s3://bucket/previews/PROD_x/fecha_scene.json

    scene_json_key = None
    scene_json_uri = preview_json
    if preview_json and preview_json.startswith("s3://"):
        # s3://bucket/key  ->  key
        scene_json_key = preview_json.split("/", 3)[-1] if preview_json.count("/") >= 3 else None

    status = "procesado" if item.get("procesado") else "pendiente"

    row = {
        "scene_name": scene_name,
        "fecha": item.get("fecha"),
        "scene_json_key": scene_json_key,
        "scene_json_uri": scene_json_uri,
        "cloud_cover": item.get("cloud_cover"),
        "status": status,
    }
    if include_scene_json:
        row["_raw_scene"] = item  # transportado aparte, no es columna directa
    return row


def production_id_from_scene_pk(pk: str) -> Optional[int]:
    """'PROD#2060' -> 2060"""
    if not pk or "#" not in pk:
        return None
    try:
        return int(pk.split("#", 1)[1])
    except (ValueError, IndexError):
        return None


# ----------------------------------------------------------------------------
# Archivos derivados (Fase 2): objeto S3 -> s3_monitoring_escena_archivos
# ----------------------------------------------------------------------------
_TIPO_POR_EXTENSION = {
    ".tif": "tif",
    ".tiff": "tif",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".json": "json",
    ".svg": "image",
}


def classify_s3_file(key: str) -> dict:
    """
    Determina tipo/extension de un archivo en S3.
    Distingue el JSON de IA (multiband.ia.json) del JSON de escena/params.
    """
    base = key.rsplit("/", 1)[-1].lower()
    _, ext = os.path.splitext(base)

    if base.endswith("multiband.ia.json"):
        tipo = "ia"
    elif ext == ".tif" or ext == ".tiff":
        # truth vs render por convencion de nombre
        if "render" in base:
            tipo = "render_tif"
        else:
            tipo = "truth_tif"
    elif ext == ".json":
        tipo = "params"
    else:
        tipo = _TIPO_POR_EXTENSION.get(ext, "otro")

    return {"tipo": tipo, "extension": ext.lstrip(".") or None}


def file_s3_to_mysql(
    s3_obj: dict, bucket: str, scene_id_mysql: int, json_content: Optional[str] = None
) -> dict:
    """
    s3_obj: {key, size, last_modified}
    MySQL s3_monitoring_escena_archivos:
      s3_monitoring_escena_id, tipo, s3_key, s3_key_hash, s3_uri,
      extension, size_bytes, last_modified, existe, json_content
    """
    key = s3_obj["key"]
    info = classify_s3_file(key)
    key_hash = hashlib.md5(key.encode("utf-8")).hexdigest()
    last_modified = s3_obj.get("last_modified")
    if last_modified is not None and hasattr(last_modified, "strftime"):
        last_modified = last_modified.strftime("%Y-%m-%d %H:%M:%S")

    return {
        "s3_monitoring_escena_id": scene_id_mysql,
        "tipo": info["tipo"],
        "s3_key": key,
        "s3_key_hash": key_hash,
        "s3_uri": f"s3://{bucket}/{key}",
        "extension": info["extension"],
        "size_bytes": s3_obj.get("size"),
        "last_modified": last_modified,
        "existe": 1,
        "json_content": json_content,
    }


def file_flags_from_tipos(tipos: set[str]) -> dict:
    """Deriva los flags *_exists de s3_monitoring_escenas a partir de los tipos."""
    return {
        "truth_tif_exists": 1 if "truth_tif" in tipos else 0,
        "render_tif_exists": 1 if "render_tif" in tipos else 0,
        "params_exists": 1 if "params" in tipos else 0,
    }


# ----------------------------------------------------------------------------
# IA (Fase 3): multiband.ia.json -> s3_monitoring_escena_ia_resumen
# ----------------------------------------------------------------------------
def ia_json_to_mysql(ia: dict, scene_id_mysql: int) -> dict:
    """
    JSON IA:
      estado_clave, estado_general (top-level),
      riesgo{nivel, motivo} (anidado),
      fecha_analisis (top-level)
    MySQL s3_monitoring_escena_ia_resumen:
      s3_monitoring_escena_id, estado_clave, estado_general,
      riesgo_nivel, riesgo_motivo, fecha_analisis, json_original
    """
    riesgo = ia.get("riesgo") or {}
    if not isinstance(riesgo, dict):
        riesgo = {}

    estado_clave = ia.get("estado_clave")
    estado_general = ia.get("estado_general")
    fecha_analisis = ia.get("fecha_analisis")
    if isinstance(fecha_analisis, str):
        fecha_analisis = fecha_analisis.strip() or None

    return {
        "s3_monitoring_escena_id": scene_id_mysql,
        "estado_clave": estado_clave,
        "estado_general": estado_general,
        "riesgo_nivel": riesgo.get("nivel"),
        "riesgo_motivo": riesgo.get("motivo"),
        "fecha_analisis": fecha_analisis,
        "json_original": json.dumps(ia, ensure_ascii=False),
    }
