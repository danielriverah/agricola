"""
Núcleo de procesamiento raster del TIF.

Flujo (según README, sección "Flujo objetivo"):
  1. recorte inicial con tile_bbox
  2. construir multiband.tif con las bandas requeridas
  3. generar derivados (índices) opcionales
  4. máscara con `poligono` para nubosidad real dentro del área productiva
  5. calcular production_cloud
  6. sugerir usable si production_cloud <= max_production_cloud

NO escribe en MySQL. Puede escribir archivos en S3/almacenamiento si se solicita.
Devuelve un resumen con rutas y métricas.
"""

from __future__ import annotations

import io
from decimal import Decimal
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app.config.models import AppConfig
from app.core.errors import ExternalResourceError
from app.processing.earth_search_client import EarthSearchClient
from app.processing.indices import (
    MULTIBAND_BANDS,
    band_index,
    cloud_mask_from_scl,
    compute_index,
    invalid_mask_from_scl,
)
from app.storage.storage import StorageDriver, StoredObject, build_base_path
from app.utils.geometry import bbox_to_geom, parse_bbox, parse_polygon

logger = logging.getLogger("tif.raster")


@dataclass
class GeneratedFile:
    role: str          # multiband | params | index_png | index_tif
    key: str
    uri: str
    bytes: int
    written: bool


@dataclass
class SceneResult:
    production_id: Any
    scene_name: str
    fecha: Any
    bbox: tuple | None = None
    production_cloud: float | None = None
    usable_suggested: bool | None = None
    valid_pixels_percentage: float | None = None
    files: list[GeneratedFile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "production_id": self.production_id,
            "scene_name": self.scene_name,
            "fecha": str(self.fecha) if self.fecha is not None else None,
            "bbox": list(self.bbox) if self.bbox else None,
            "production_cloud": self.production_cloud,
            "usable_suggested": self.usable_suggested,
            "valid_pixels_percentage": self.valid_pixels_percentage,
            "files": [f.__dict__ for f in self.files],
            "notes": self.notes,
            "error": self.error,
            # Recordatorio explícito de contrato.
            "db_writes": False,
        }


def _dimensions(bbox: tuple[float, float, float, float], resolution_m: int) -> tuple[int, int]:
    """Aproxima ancho/alto en px desde el bbox geográfico y la resolución."""
    minx, miny, maxx, maxy = bbox
    lat_mid = (miny + maxy) / 2.0
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * max(0.01, abs(np.cos(np.radians(lat_mid))))
    width = int(round((maxx - minx) * m_per_deg_lon / resolution_m))
    height = int(round((maxy - miny) * m_per_deg_lat / resolution_m))
    # límites de seguridad
    width = max(16, min(width, 2500))
    height = max(16, min(height, 2500))
    return width, height


def _scene_date_from_name(scene_name: str) -> str | None:
    match = re.search(r"_(20\d{6})_", scene_name or "")
    if not match:
        return None
    value = match.group(1)
    return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"


def _parse_scene_json(scene_json_text: str) -> dict[str, Any]:
    parsed = json.loads(scene_json_text)
    return parsed if isinstance(parsed, dict) else {}


def _normalize_urls_bandas_value(value: Any, scene_name: str) -> dict[str, str]:
    if not value:
        return {}

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return {}
        if s.startswith("{") or s.startswith("["):
            try:
                parsed = json.loads(s)
                return _normalize_urls_bandas_value(parsed, scene_name)
            except Exception:  # noqa: BLE001
                pass
        if any(marker in s for marker in ("{band}", "{banda}", "{escene_name}", "{escena_name}")):
            return _expand_band_template(s, scene_name)
        return {}

    if isinstance(value, dict):
        normalized = {str(k): str(v) for k, v in value.items() if v}
        if not normalized:
            return {}
        if all(band in normalized for band in MULTIBAND_BANDS):
            return normalized
        sample_url = next(iter(normalized.values()), None)
        if isinstance(sample_url, str) and sample_url:
            return _expand_band_template(sample_url, scene_name)
        return {}

    return {}


def _normalize_urls_bandas(
    scene_json: dict[str, Any],
    scene_name: str,
) -> dict[str, str]:
    urls_bandas = _normalize_urls_bandas_value(scene_json.get("urls_bandas"), scene_name)
    if urls_bandas:
        return urls_bandas

    bands = scene_json.get("bands") or {}
    if not isinstance(bands, dict) or not bands:
        return {}
    normalized = _normalize_urls_bandas_value(bands, scene_name)
    if normalized:
        return normalized

    # Fallback por si el JSON trae una sola URL base o una plantilla.
    sample_url = (
        scene_json.get("scene_json_uri")
        or scene_json.get("scene_json_url")
        or scene_json.get("band_url")
    )
    if not isinstance(sample_url, str) or not sample_url:
        return {}

    return _expand_band_template(sample_url, scene_name)


def _expand_band_template(sample_url: str, scene_name: str) -> dict[str, str]:
    template = _derive_band_template(sample_url, scene_name)
    if template is None:
        return {}
    return {band: template.format(band=band, escena_name=scene_name, escene_name=scene_name) for band in MULTIBAND_BANDS}


def _resolve_band_urls(urls_bandas: dict[str, str], scene_name: str) -> dict[str, str]:
    if all(band in urls_bandas for band in MULTIBAND_BANDS):
        return urls_bandas
    sample_url = next(iter(urls_bandas.values()), None)
    if not isinstance(sample_url, str) or not sample_url:
        return urls_bandas
    expanded = _expand_band_template(sample_url, scene_name)
    return expanded if expanded else urls_bandas


def _derive_band_template(sample_url: str, scene_name: str) -> str | None:
    """
    Convierte una URL de una banda en una plantilla reutilizable.

    Ejemplo:
      .../B04.tif -> .../{band}.tif
      .../red.tif  -> .../{band}.tif
    """
    normalized = sample_url.strip()
    if not normalized:
        return None
    expanded = normalized.replace("{escene_name}", scene_name).replace("{escena_name}", scene_name)
    expanded = expanded.replace("{banda}", "{band}.tif").replace("{band}", "{band}")
    if "{band}" in expanded:
        return expanded
    filename = expanded.rsplit("/", 1)[-1]
    stem = filename.rsplit(".", 1)[0]
    if not stem:
        return None
    return expanded[: -len(filename)] + "{band}.tif"


def _scene_cache_key(scene_name: str, production_id: Any | None = None) -> str:
    safe_scene = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(scene_name or "scene"))
    if production_id is None:
        return f"temps/bands/{safe_scene}"
    return f"temps/bands/{safe_scene}/{production_id}"


def _band_temp_dir(scene_name: str, production_id: Any | None = None) -> Path:
    return Path("/tmp/tif-outputs") / _scene_cache_key(scene_name, production_id)


def _band_temp_paths(scene_name: str, production_id: Any | None = None) -> dict[str, Path]:
    temp_dir = _band_temp_dir(scene_name, production_id)
    return {band: temp_dir / f"{band}.tif" for band in MULTIBAND_BANDS}


def _local_band_temp_complete(scene_name: str, production_id: Any | None = None) -> bool:
    paths = _band_temp_paths(scene_name, production_id)
    return all(path.exists() for path in paths.values())


def _temp_multiband_path(scene_name: str, production_id: Any | None = None) -> Path:
    return _band_temp_dir(scene_name, production_id) / "multiband.tif"


def _rasterize_polygon_mask(
    polygon, bbox: tuple[float, float, float, float], width: int, height: int
) -> np.ndarray:
    """True donde el píxel cae dentro del polígono de producción."""
    from affine import Affine
    from rasterio.features import rasterize

    minx, miny, maxx, maxy = bbox
    transform = Affine(
        (maxx - minx) / width, 0, minx,
        0, -(maxy - miny) / height, maxy,
    )
    mask = rasterize(
        [(polygon, 1)],
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype="uint8",
    )
    return mask.astype(bool)


def _stack_to_geotiff_bytes(
    stack: np.ndarray, bbox: tuple[float, float, float, float]
) -> bytes:
    import rasterio
    from affine import Affine

    n, height, width = stack.shape
    minx, miny, maxx, maxy = bbox
    transform = Affine(
        (maxx - minx) / width, 0, minx,
        0, -(maxy - miny) / height, maxy,
    )
    buf = io.BytesIO()
    with rasterio.open(
        buf, "w", driver="GTiff", height=height, width=width, count=n,
        dtype="float32", crs="EPSG:4326", transform=transform,
    ) as dst:
        for i in range(n):
            dst.write(stack[i].astype("float32"), i + 1)
            if i < len(MULTIBAND_BANDS):
                dst.set_band_description(i + 1, MULTIBAND_BANDS[i])
    return buf.getvalue()


def _resize_band_to_grid(
    band: np.ndarray,
    width: int,
    height: int,
    *,
    nearest: bool,
) -> np.ndarray:
    from PIL import Image

    source = np.asarray(band)
    if source.shape == (height, width):
        return source.astype("float32", copy=False)

    image = Image.fromarray(source.astype("float32"))
    method = Image.Resampling.NEAREST if nearest else Image.Resampling.BILINEAR
    resized = image.resize((width, height), resample=method)
    return np.asarray(resized, dtype="float32")


def _align_stack_to_grid(
    raw_stack: Any,
    width: int,
    height: int,
    result: SceneResult,
) -> np.ndarray:
    """
    Normaliza todas las bandas al grid objetivo antes de apilar.

    Sentinel-2 mezcla bandas de 10m y 20m. Earth Search entrega cada banda como
    COG independiente; este paso evita deformaciones si alguna fuente llega con
    dimensiones distintas al grid objetivo.
    """
    if isinstance(raw_stack, np.ndarray):
        if raw_stack.ndim != 3:
            raise ValueError(f"Stack inválido: se esperaba 3D y llegó {raw_stack.ndim}D.")
        band_arrays = [raw_stack[i] for i in range(raw_stack.shape[0])]
    elif isinstance(raw_stack, (list, tuple)):
        band_arrays = [np.asarray(b) for b in raw_stack]
    else:
        raise ValueError(f"Stack inválido: tipo no soportado {type(raw_stack).__name__}.")

    aligned: list[np.ndarray] = []
    for index, band in enumerate(band_arrays):
        band = np.asarray(band)
        if band.ndim == 3 and band.shape[0] == 1:
            band = band[0]
        if band.ndim != 2:
            raise ValueError(
                f"Banda inválida en posición {index + 1}: "
                f"se esperaba 2D y llegó {band.ndim}D."
            )
        band_name = MULTIBAND_BANDS[index] if index < len(MULTIBAND_BANDS) else f"band_{index + 1}"
        source_height, source_width = band.shape[-2], band.shape[-1]
        nearest = band_name.upper() in {"SCL", "QA60", "MASK", "CLOUD_MASK"}
        if (source_height, source_width) != (height, width):
            result.notes.append(
                f"{band_name} remuestreada de {source_width}x{source_height} "
                f"a {width}x{height} ({'nearest' if nearest else 'bilinear'})."
            )
        aligned.append(_resize_band_to_grid(band, width, height, nearest=nearest))

    stack = np.stack(aligned, axis=0).astype("float32")
    if stack.shape[0] < len(MULTIBAND_BANDS):
        raise ValueError(
            f"Stack incompleto: {stack.shape[0]} bandas recibidas, "
            f"{len(MULTIBAND_BANDS)} esperadas."
        )
    return stack


def _polygon_to_pixel_points(
    polygon,
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
) -> list[tuple[int, int]]:
    if polygon is None:
        return []
    minx, miny, maxx, maxy = bbox
    coords = list(getattr(polygon, "exterior", polygon).coords)
    points: list[tuple[int, int]] = []
    for lon, lat in coords:
        x = int(round((lon - minx) / (maxx - minx) * (width - 1))) if maxx != minx else 0
        y = int(round((maxy - lat) / (maxy - miny) * (height - 1))) if maxy != miny else 0
        points.append((max(0, min(width - 1, x)), max(0, min(height - 1, y))))
    return points


def _outline_png(png_bytes: bytes, polygon, bbox: tuple[float, float, float, float], width: int, height: int) -> bytes:
    from PIL import Image, ImageDraw

    points = _polygon_to_pixel_points(polygon, bbox, width, height)
    if len(points) < 2:
        return png_bytes
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.line(points + [points[0]], fill=(255, 255, 0), width=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _index_palette(index_name: str) -> list[tuple[int, int, int]]:
    name = index_name.lower()
    if name in {"ndvi", "savi"}:
        return [(120, 69, 0), (255, 224, 102), (0, 110, 0)]
    if name == "ndre":
        return [(72, 20, 103), (255, 240, 170), (21, 122, 61)]
    if name == "gndvi":
        return [(0, 35, 102), (0, 212, 255), (0, 128, 0)]
    if name == "evi":
        return [(0, 20, 120), (0, 189, 255), (255, 242, 150), (220, 50, 0)]
    if name == "ndwi":
        return [(170, 70, 40), (255, 255, 255), (0, 125, 200)]
    if name == "ndmi":
        return [(0, 86, 140), (255, 255, 255), (210, 120, 30)]
    if name == "nbr":
        return [(0, 82, 150), (255, 255, 255), (255, 165, 0), (110, 0, 0)]
    return [(0, 0, 0), (255, 255, 255)]


def _interpolate_palette(norm: np.ndarray, palette: list[tuple[int, int, int]]) -> np.ndarray:
    stops = np.asarray(palette, dtype="float32")
    if len(stops) == 1:
        return np.tile(stops[0], (*norm.shape, 1)).astype("uint8")
    scaled = np.clip(norm, 0.0, 1.0) * (len(stops) - 1)
    left = np.floor(scaled).astype(int)
    right = np.clip(left + 1, 0, len(stops) - 1)
    frac = (scaled - left)[..., None]
    rgb = stops[left] * (1.0 - frac) + stops[right] * frac
    return np.clip(rgb, 0, 255).astype("uint8")


def _index_to_png_bytes(index_name: str, index_arr: np.ndarray, mask_invalid: np.ndarray) -> bytes:
    from PIL import Image

    a = index_arr.copy()
    finite = np.isfinite(a) & ~mask_invalid
    if finite.any():
        lo, hi = np.percentile(a[finite], [2, 98])
    else:
        lo, hi = -1.0, 1.0
    if hi <= lo:
        hi = lo + 1e-6
    norm = np.clip((a - lo) / (hi - lo), 0, 1)
    rgb = _interpolate_palette(norm, _index_palette(index_name))
    rgb[mask_invalid] = [0, 0, 0]
    img = Image.fromarray(rgb, mode="RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _rgb_composite_png_bytes(
    stack: np.ndarray,
    red_band: str,
    green_band: str,
    blue_band: str,
    mask_invalid: np.ndarray | None = None,
) -> bytes:
    from PIL import Image

    band_map = {name: stack[i].astype("float32") for i, name in enumerate(MULTIBAND_BANDS)}
    red = band_map[red_band]
    green = band_map[green_band]
    blue = band_map[blue_band]
    channels = []
    valid = np.isfinite(red) & np.isfinite(green) & np.isfinite(blue)
    if mask_invalid is not None:
        valid &= ~mask_invalid
    for channel in (red, green, blue):
        values = channel[valid]
        if values.size:
            lo, hi = np.percentile(values, [1.5, 98.5])
        else:
            lo, hi = 0.0, 1.0
        if hi <= lo:
            hi = lo + 1e-6
        normalized = np.clip((channel - lo) / (hi - lo), 0, 1)
        normalized = np.power(normalized, 0.9)
        channels.append((normalized * 255).astype("uint8"))
    rgb = np.stack(channels, axis=-1)
    if mask_invalid is not None:
        rgb[mask_invalid] = [0, 0, 0]
    img = Image.fromarray(rgb, mode="RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _natural_rgb_png_bytes(stack: np.ndarray, mask_invalid: np.ndarray | None = None) -> bytes:
    return _rgb_composite_png_bytes(stack, "B04", "B03", "B02", mask_invalid)


def _false_color_veg_png_bytes(stack: np.ndarray, mask_invalid: np.ndarray | None = None) -> bytes:
    return _rgb_composite_png_bytes(stack, "B08", "B04", "B03", mask_invalid)


def _red_edge_png_bytes(stack: np.ndarray, mask_invalid: np.ndarray | None = None) -> bytes:
    return _rgb_composite_png_bytes(stack, "B06", "B05", "B04", mask_invalid)


def _swir_png_bytes(stack: np.ndarray, mask_invalid: np.ndarray | None = None) -> bytes:
    return _rgb_composite_png_bytes(stack, "B11", "B12", "B08", mask_invalid)


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _json_safe_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(v) for v in value]
    return value


class RasterProcessor:
    def __init__(self, cfg: AppConfig, storage: StorageDriver) -> None:
        self.cfg = cfg
        self.storage = storage
        self.earth_search = EarthSearchClient(cfg.earth_search)

    def _fetch_from_urls_bandas(
        self,
        urls_bandas: dict[str, str],
        bbox: tuple[float, float, float, float],
        width: int,
        height: int,
        result: SceneResult,
    ) -> np.ndarray:
        import rasterio
        from affine import Affine
        from rasterio.enums import Resampling
        from rasterio.warp import reproject

        minx, miny, maxx, maxy = bbox
        dst_transform = Affine(
            (maxx - minx) / width, 0, minx,
            0, -(maxy - miny) / height, maxy,
        )

        resolved = _resolve_band_urls(urls_bandas, result.scene_name)
        stack: list[np.ndarray] = []
        for band_name in MULTIBAND_BANDS:
            '''if cancel_requested and cancel_requested():
                return {
                    "production_id": prod_id,
                    "scene_name": scene_name,
                    "temp_dir": temp_dir,
                    "bands": written,
                    "dry_run": dry_run,
                    "db_writes": False,
                    "cancelled": True,
                }'''
            href = resolved.get(band_name)
            if not href:
                raise ValueError(f"Falta URL para banda {band_name} en urls_bandas.")
            band_resolution = self.cfg.earth_search.band_resolution_meters.get(band_name, 10)
            categorical = band_name.upper() in {"SCL", "QA60", "MASK", "CLOUD_MASK"}
            destination = np.full((height, width), 0 if categorical else np.nan, dtype="float32")
            with rasterio.open(href) as src:
                reproject(
                    source=rasterio.band(src, 1),
                    destination=destination,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    src_nodata=src.nodata,
                    dst_transform=dst_transform,
                    dst_crs="EPSG:4326",
                    dst_nodata=0 if categorical else np.nan,
                    resampling=Resampling.nearest if categorical else Resampling.bilinear,
                )
            if categorical:
                destination = np.nan_to_num(destination, nan=0).astype("float32")
            else:
                destination = self.earth_search._normalize_reflectance(destination)
            stack.append(destination)
            source_url = href.split("?")[0]
            result.notes.append(f"{band_name} ({band_resolution}m) cargada desde {source_url}.")

        return np.stack(stack, axis=0).astype("float32")
    def cache_bands_for_scene_get(
        self,
        group: list[dict],
        scene_name: str,
        bandas: dict,
        dry_run: bool = True,
        cancel_requested=None,
    ):
        temp_dir = self.storage.ensure_local_dir(_scene_cache_key(scene_name))
        band_paths = _band_temp_paths(scene_name)
        written: list[str] = []
        reused: list[str] = []

        def download_one(band_name: str) -> tuple[str, str]:
            if cancel_requested and cancel_requested():
                raise RuntimeError("cancelled")
            href = bandas.get(band_name)
            if not href:
                raise ExternalResourceError(
                    resource="urls_bandas",
                    reason=f"Falta URL para banda {band_name}.",
                    hint="Aseg?rate de que el JSON de escena tenga la banda requerida.",
                )
            band_path = str(band_paths[band_name])
            target = Path(band_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                return band_name, band_path
            if not dry_run:
                self._write_original_band_tif(href, band_path)
            return band_name, band_path

        if _local_band_temp_complete(scene_name):
            return {
                "scene_name": scene_name,
                "temp_dir": temp_dir,
                "bands": [str(path) for path in band_paths.values()],
                "dry_run": dry_run,
                "db_writes": False,
                "temp_files_reused": True,
            }

        pending_bands = [band_name for band_name in MULTIBAND_BANDS if not Path(str(band_paths[band_name])).exists()]
        if dry_run:
            return {
                "scene_name": scene_name,
                "temp_dir": temp_dir,
                "bands": [str(band_paths[band_name]) for band_name in MULTIBAND_BANDS],
                "dry_run": dry_run,
                "db_writes": False,
                "temp_files_reused": False,
                "simulated": True,
                "parallel_downloads": 2,
            }

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(download_one, band_name) for band_name in pending_bands]
            for future in as_completed(futures):
                if cancel_requested and cancel_requested():
                    raise RuntimeError("cancelled")
                _, band_path = future.result()
                written.append(band_path)

        for band_name in MULTIBAND_BANDS:
            path = str(band_paths[band_name])
            if Path(path).exists():
                reused.append(path)
                if path not in written:
                    written.append(path)

        return {
            "scene_name": scene_name,
            "temp_dir": temp_dir,
            "bands": written,
            "reused_bands": reused,
            "dry_run": dry_run,
            "db_writes": False,
            "temp_files_reused": False,
            "parallel_downloads": 2,
        }

    def cache_bands_for_scene(
        self,
        production: dict,
        scene: dict,
        dry_run: bool = False,
        cancel_requested=None,
    ) -> dict:
        prod_id = production.get("produccion_id")
        scene_name = scene.get("scene_name") or f"escena_{scene.get('s3_monitoring_escena_id')}"
        scene_json_uri = scene.get("scene_json_uri")
        if not scene_json_uri:
            raise ValueError("scene_json_uri es obligatorio.")

        urls_bandas = _normalize_urls_bandas_value(scene.get("urls_bandas"), scene_name)
        if not urls_bandas:
            try:
                scene_json = _parse_scene_json(self.storage.read_text(scene_json_uri))
            except Exception as exc:  # noqa: BLE001
                raise ExternalResourceError(
                    resource="scene_json_uri",
                    reason=str(exc),
                    hint="Revisa credenciales AWS, permisos de S3 y que la URI exista.",
                    detail={"scene_json_uri": scene_json_uri, "production_id": prod_id, "scene_name": scene_name},
                ) from exc
            urls_bandas = _normalize_urls_bandas(scene_json, scene_name)
            if not urls_bandas:
                raise ExternalResourceError(
                    resource="scene_json_uri",
                    reason="No fue posible derivar urls_bandas desde scene_json_uri.",
                    hint="Verifica que el JSON tenga `urls_bandas` o `bands` con URLs válidas.",
                    detail={"scene_json_uri": scene_json_uri, "production_id": prod_id, "scene_name": scene_name},
                )

        temp_dir = self.storage.ensure_local_dir(_scene_cache_key(scene_name))
        if cancel_requested and cancel_requested():
            return {
                "production_id": prod_id,
                "scene_name": scene_name,
                "temp_dir": temp_dir,
                "bands": [],
                "dry_run": dry_run,
                "db_writes": False,
                "cancelled": True,
            }
        if _local_band_temp_complete(scene_name):
            return {
                "production_id": prod_id,
                "scene_name": scene_name,
                "temp_dir": temp_dir,
                "bands": [str(path) for path in _band_temp_paths(scene_name).values()],
                "dry_run": dry_run,
                "db_writes": False,
                "temp_files_reused": True,
            }
        written: list[str] = []
        resolved_urls = _resolve_band_urls(urls_bandas, scene_name)
        for band_name in MULTIBAND_BANDS:
            if cancel_requested and cancel_requested():
                return {
                    "production_id": prod_id,
                    "scene_name": scene_name,
                    "temp_dir": temp_dir,
                    "bands": written,
                    "dry_run": dry_run,
                    "db_writes": False,
                    "cancelled": True,
                }
            href = resolved_urls.get(band_name)
            if not href:
                raise ExternalResourceError(
                    resource="urls_bandas",
                    reason=f"Falta URL para banda {band_name}.",
                    hint="Asegúrate de que el JSON de escena tenga la banda requerida.",
                    detail={"scene_json_uri": scene_json_uri, "band_name": band_name, "scene_name": scene_name},
                )
            band_path = str(_band_temp_paths(scene_name)[band_name])
            if dry_run:
                written.append(band_path)
                continue
            self._write_original_band_tif(href, band_path)
            written.append(band_path)

        return {
            "production_id": prod_id,
            "scene_name": scene_name,
            "temp_dir": temp_dir,
            "bands": written,
            "dry_run": dry_run,
            "db_writes": False,
            "temp_files_reused": False,
        }

    def _write_original_band_tif(self, source_href: str, path: str) -> None:
        import rasterio

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        with rasterio.open(source_href) as src:
            profile = src.profile.copy()
            profile.update(
                driver="GTiff",
                compress="deflate",
                tiled=True,
            )
            data = src.read()
            with rasterio.open(target, "w", **profile) as dst:
                dst.write(data)

    def _write_single_band_tif(self, band: np.ndarray, bbox: tuple[float, float, float, float], path: str) -> None:
        import rasterio
        from affine import Affine

        height, width = band.shape
        minx, miny, maxx, maxy = bbox
        transform = Affine(
            (maxx - minx) / width, 0, minx,
            0, -(maxy - miny) / height, maxy,
        )
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=height,
            width=width,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=transform,
        ) as dst:
            dst.write(band.astype("float32"), 1)

    def _scene_date_text(self, value: Any) -> str | None:
        if value is None:
            return None
        if hasattr(value, "strftime"):
            try:
                return value.strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                pass
        text = str(value).strip()
        if not text:
            return None
        return text[:10]

    def _to_float(self, value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:  # noqa: BLE001
            return None

    def _masked_values(self, arr: np.ndarray, mask: np.ndarray) -> np.ndarray:
        data = np.asarray(arr, dtype="float32")
        finite = np.isfinite(data) & np.asarray(mask, dtype=bool)
        values = data[finite]
        return values[np.isfinite(values)]

    def _stat_summary(self, values: np.ndarray) -> dict[str, float | None]:
        if values.size == 0:
            return {"promedio": None, "min": None, "max": None}
        return {
            "promedio": round(float(np.mean(values)), 4),
            "min": round(float(np.min(values)), 4),
            "max": round(float(np.max(values)), 4),
        }

    def _ndvi_zones(self, values: np.ndarray) -> dict[str, float]:
        if values.size == 0:
            return {"zona_baja_pct": 0.0, "zona_media_pct": 0.0, "zona_alta_pct": 0.0}
        total = float(values.size)
        low = round(float(np.count_nonzero(values < 0.1) * 100.0 / total), 2)
        medium = round(float(np.count_nonzero((values >= 0.1) & (values < 0.3)) * 100.0 / total), 2)
        high = round(max(0.0, 100.0 - low - medium), 2)
        return {"zona_baja_pct": low, "zona_media_pct": medium, "zona_alta_pct": high}

    def _ndwi_stress_pct(self, values: np.ndarray) -> float:
        if values.size == 0:
            return 0.0
        return round(float(np.count_nonzero(values < -0.25) * 100.0 / float(values.size)), 2)

    def _compute_scene_cloud_metrics(
        self,
        production: dict,
        cropped_stack: np.ndarray,
        bbox: tuple[float, float, float, float],
    ) -> dict[str, Any]:
        polygon = parse_polygon(production.get("poligono"))
        if polygon is None:
            raise ValueError("poligono ausente o no parseable; no se puede calcular production_cloud.")
        poly_mask = _rasterize_polygon_mask(
            polygon,
            bbox,
            cropped_stack.shape[2],
            cropped_stack.shape[1],
        )
        scl = np.rint(cropped_stack[band_index("SCL")]).astype("int16")

        prod_pixels = int(poly_mask.sum())
        if prod_pixels <= 0:
            return {
                "production_cloud": None,
                "usable": None,
                "valid_pixels_percentage": None,
                "poly_pixels": 0,
                "observable_pixels": 0,
                "cloudy_pixels": 0,
                "invalid_pixels": 0,
                "scl_histogram": {},
            }

        cloud_classes = np.isin(scl, [3, 7, 8, 9, 10])
        observable_classes = np.isin(scl, [4, 5, 6])
        #invalid_classes = ~(cloud_classes | observable_classes)
        invalid_classes = np.isin(
            scl,
            [0, 1, 2, 11]
        )

        cloudy_mask = poly_mask & cloud_classes
        observable_mask = poly_mask & observable_classes
        invalid_mask = poly_mask & invalid_classes

        cloudy_pixels = int(cloudy_mask.sum())
        observable_pixels = int(observable_mask.sum())
        invalid_pixels = int(invalid_mask.sum())

        denominator = cloudy_pixels + observable_pixels

        if denominator <= 0:
            production_cloud = None
            usable = False
        else:
            production_cloud = round(100.0 * cloudy_pixels / denominator, 2)
            usable = production_cloud <= self.cfg.processing.max_production_cloud

        valid_pixels = observable_pixels + cloudy_pixels
        valid_pixels_percentage = (round(    100.0 * valid_pixels / prod_pixels,    2)if prod_pixels > 0 else None)
        usable_area = (round(100.0 * observable_pixels / prod_pixels,2)
            if prod_pixels > 0
            else None
        )
        valid = (
            round(100.0 * valid_pixels / prod_pixels, 2)
            if prod_pixels > 0
            else None
        )
        #valid = round(100.0 * observable_pixels / prod_pixels, 2) if prod_pixels > 0 else None

        scl_values = scl[poly_mask]
        scl_histogram: dict[str, int] = {}
        if scl_values.size:
            unique, counts = np.unique(scl_values, return_counts=True)
            scl_histogram = {str(int(k)): int(v) for k, v in zip(unique.tolist(), counts.tolist())}

        return {
            "poligono_puntos":production.get("poligono"),
            #BaseGeometry
            "poligono_bounds":polygon.bounds,
            "production_cloud": production_cloud,
            "usable": usable,
            "valid_pixels_percentage": valid,
            "poly_pixels": prod_pixels,
            "usable_area": usable_area,
            "observable_pixels": observable_pixels,
            "cloudy_pixels": cloudy_pixels,
            "invalid_pixels": invalid_pixels,
            "scl_histogram": scl_histogram,
        }

    def _persist_scene_cloud_metrics(self, production: dict, scene: dict, cropped_stack: np.ndarray, bbox: tuple[float, float, float, float]) -> dict[str, Any]:
        metrics = self._compute_scene_cloud_metrics(production, cropped_stack, bbox)
        production_cloud = metrics.get("production_cloud")
        usable = metrics.get("usable")
        if production_cloud is None or usable is None:
            return {"production_cloud": None, "usable": None, "updated": False, **metrics}

        from app.db.mysql_client import MySQLWriteClient
        from app.db.repositories import SceneRepository

        writer = MySQLWriteClient(self.cfg.mysql)
        repo = SceneRepository(writer)
        repo.update_cloud_metrics(scene.get("s3_monitoring_escena_id"), production_cloud, 1 if usable else 0)
        return {"production_cloud": production_cloud, "usable": usable, "updated": True, **metrics}

    def _build_previous_params(self, production_id: Any, current_fecha: str | None, current_scene_id: Any | None = None) -> dict[str, Any] | None:
        if current_fecha is None:
            return None
        from app.db.mysql_client import MySQLReadOnlyClient
        from app.db.repositories import SceneFileRepository

        reader = MySQLReadOnlyClient(self.cfg.mysql)
        repo = SceneFileRepository(reader)
        row = repo.get_latest_params_before(production_id, current_fecha, current_scene_id)
        if not row:
            return None
        payload = row.get("json_content")
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", errors="ignore")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:  # noqa: BLE001
                return None
        if not isinstance(payload, dict):
            return None
        return _json_safe_value({
            "scene_ref": {
                "s3_monitoring_escena_id": row.get("s3_monitoring_escena_id"),
                "scene_name": row.get("scene_name"),
                "fecha": self._scene_date_text(row.get("fecha")),
                "production_cloud": row.get("production_cloud"),
            },
            "params": payload,
        })

    def _store_generated_asset(
        self,
        *,
        file_repo,
        scene_repo,
        scene_id: Any,
        key: str,
        uri: str,
        data: bytes,
        role: str,
        tipo: str,
        extension: str,
        dry_run: bool,
        regenerate: bool,
        json_content: str | None = None,
    ) -> dict[str, Any]:
        existing = file_repo.get_by_scene_and_key(scene_id, key) if file_repo else None
        if existing and not regenerate:
            return {
                "role": role,
                "key": existing.get("s3_key") or key,
                "uri": existing.get("s3_uri") or uri,
                "bytes": existing.get("size_bytes") or len(data),
                "written": False,
                "skipped": True,
                "already_indexed": True,
            }
        if dry_run:
            return {
                "role": role,
                "key": key,
                "uri": uri,
                "bytes": len(data),
                "written": False,
                "skipped": False,
                "already_indexed": bool(existing),
            }
        obj = self.storage.put_bytes(key, data, "application/json" if extension == "json" else ("image/tiff" if extension == "tif" else "image/png"), dry_run)
        if file_repo:
            if extension == "json":
                file_repo.upsert_json_file(scene_id, obj.key, obj.uri, tipo, json_content or data.decode("utf-8", errors="ignore"), len(data))
            else:
                file_repo.upsert_file(scene_id, obj.key, obj.uri, extension=extension, tipo=tipo, size_bytes=len(data), existe=1)
            if scene_repo and tipo == "params":
                scene_repo.update_params_exists(scene_id, 1)
            if file_repo and tipo == "truth_tif":
                file_repo.update_scene_truth_tif_exists(scene_id, 1)
        return {
            "role": role,
            "key": obj.key,
            "uri": obj.uri,
            "bytes": obj.bytes_written,
            "written": obj.written,
            "skipped": False,
            "already_indexed": bool(existing),
        }

    def _build_params_document(
        self,
        production: dict,
        scene: dict,
        cropped_stack: np.ndarray,
        cloud_info: dict[str, Any],
        poly_mask: np.ndarray,
    ) -> dict[str, Any]:
        prod_id = production.get("produccion_id")
        current_fecha = self._scene_date_text(scene.get("fecha"))
        invalid_mask = invalid_mask_from_scl(cropped_stack)
        analysis_mask = np.asarray(poly_mask, dtype=bool) & ~np.asarray(invalid_mask, dtype=bool)
        if not np.any(analysis_mask):
            analysis_mask = np.asarray(poly_mask, dtype=bool)

        ndvi = compute_index("ndvi", cropped_stack)
        ndwi = compute_index("ndwi", cropped_stack)
        ndre = compute_index("ndre", cropped_stack)
        savi = compute_index("savi", cropped_stack)
        evi = compute_index("evi", cropped_stack)
        gndvi = compute_index("gndvi", cropped_stack)
        nbr = compute_index("nbr", cropped_stack)
        ndmi = compute_index("ndmi", cropped_stack)

        ndvi_values = self._masked_values(ndvi, analysis_mask)
        ndwi_values = self._masked_values(ndwi, analysis_mask)
        ndre_values = self._masked_values(ndre, analysis_mask)
        savi_values = self._masked_values(savi, analysis_mask)
        evi_values = self._masked_values(evi, analysis_mask)
        gndvi_values = self._masked_values(gndvi, analysis_mask)
        nbr_values = self._masked_values(nbr, analysis_mask)
        ndmi_values = self._masked_values(ndmi, analysis_mask)

        current = {
            "id_produccion": prod_id,
            "fecha_escena": current_fecha,
            "archivo_tif": self.cfg.outputs.multiband_filename,
            "sensor": "Sentinel-2",
            "resolucion_metros": self.cfg.processing.resolution_meters,
            "nubosidad_pct": cloud_info.get("production_cloud"),
            "indices": {
                "ndvi": {
                    **self._stat_summary(ndvi_values),
                    **self._ndvi_zones(ndvi_values),
                },
                "ndwi": {
                    **self._stat_summary(ndwi_values),
                    "zona_estres_hidrico_pct": self._ndwi_stress_pct(ndwi_values),
                },
                "ndre": self._stat_summary(ndre_values),
                "savi": self._stat_summary(savi_values),
                "evi": self._stat_summary(evi_values),
                "gndvi": self._stat_summary(gndvi_values),
                "nbr": self._stat_summary(nbr_values),
            },
            "zonas_detectadas": [],
        }

        previous = self._build_previous_params(prod_id, current_fecha, scene.get("s3_monitoring_escena_id"))
        previous_history = {}
        previous_params = previous.get("params") if isinstance(previous, dict) else None
        if isinstance(previous_params, dict):
            history_candidate = previous_params.get("historico")
            if isinstance(history_candidate, dict):
                previous_history = history_candidate

        current_means = {
            "ndvi": current["indices"]["ndvi"].get("promedio"),
            "ndmi": round(float(np.mean(ndmi_values)), 4) if ndmi_values.size else None,
            "ndre": current["indices"]["ndre"].get("promedio"),
            "savi": current["indices"]["savi"].get("promedio"),
            "evi": current["indices"]["evi"].get("promedio"),
            "gndvi": current["indices"]["gndvi"].get("promedio"),
            "nbr": current["indices"]["nbr"].get("promedio"),
        }

        historical_keys = ["ndvi", "ndmi", "ndre", "savi", "evi", "gndvi", "nbr"]
        count_prev = int(previous_history.get("count") or 0)
        count = count_prev + 1
        historical: dict[str, Any] = {"count": count}
        for key in historical_keys:
            prev_sum = self._to_float(previous_history.get(f"{key}_sum")) or 0.0
            current_value = self._to_float(current_means.get(key)) or 0.0
            total_sum = prev_sum + current_value
            historical[f"{key}_sum"] = round(total_sum, 4)
            historical[f"{key}_promedio_historico"] = round(total_sum / count, 6) if count > 0 else None

        current["historico"] = historical
        current["anterior"] = (previous or {}).get("scene_ref") if isinstance(previous, dict) else None
        return current
    def build_temp_multiband_from_cache_create(self, scene: dict, reescribir: bool = False, dry_run: bool = False) -> dict:
        prod_id = scene.get("production_id") or scene.get("produccion_id")
        scene_name = scene.get("scene_name")
        temp_dir = self.storage.ensure_local_dir(_scene_cache_key(scene_name))
        band_paths = _band_temp_paths(scene_name)
        out_path = str(_temp_multiband_path(scene_name, prod_id))
        missing_bands = [band_path for band_path in band_paths.values() if not Path(band_path).exists()]

        if missing_bands:
            if dry_run:
                return {
                    "production_id": prod_id,
                    "scene_name": scene_name,
                    "temp_dir": temp_dir,
                    "multiband_path": out_path,
                    "dry_run": dry_run,
                    "db_writes": False,
                    "multiband_reused": False,
                    "simulated": True,
                    "missing_bands": missing_bands,
                }
            raise ExternalResourceError(
                resource="temp_band",
                reason=f"No existe banda temporal requerida: {missing_bands[0]}",
                hint="Ejecuta primero `get_bandas` para esa escena.",
                detail={"temp_dir": temp_dir, "scene_name": scene_name, "production_id": prod_id},
            )

        bbox = parse_bbox(scene.get("tile_bbox"))
        if bbox is None:
            raise ValueError("tile_bbox no parseable.")
        polygon = parse_polygon(scene.get("poligono"))
        if polygon is None:
            raise ValueError("poligono ausente o no parseable; no se puede generar multiband por producci?n.")

        if dry_run and not Path(out_path).exists():
            return {
                "production_id": prod_id,
                "scene_name": scene_name,
                "temp_dir": temp_dir,
                "multiband_path": out_path,
                "dry_run": dry_run,
                "db_writes": False,
                "multiband_reused": False,
                "simulated": True,
                "missing_multiband": True,
            }

        band_sources = {band_name: str(path) for band_name, path in band_paths.items()}
        cropped = self._fetch_from_urls_bandas(
            band_sources,
            bbox,
            *_dimensions(bbox, self.cfg.processing.resolution_meters),
            SceneResult(prod_id, scene_name, scene.get("fecha")),
        )

        cloud_info = {"production_cloud": None, "usable": None, "updated": False}
        if not dry_run:
            cloud_info = self._persist_scene_cloud_metrics(scene, scene, cropped, bbox)
        #print(f"####BANDAS DEBIGING#####[T1]:\n{cloud_info}")
        #print(f"####BANDAS DEBIGING#####[T1]:\n{Path(out_path).exists()}")
        if Path(out_path).exists():
            return {
                "production_id": prod_id,
                "scene_name": scene_name,
                "temp_dir": temp_dir,
                "multiband_path": out_path,
                "dry_run": dry_run,
                "db_writes": False,
                "multiband_reused": True,
                **cloud_info,
            }
        #print(f"####BANDAS DEBIGING#####[T2]:\n{cropped}")
        multiband = _stack_to_geotiff_bytes(cropped, bbox)
        #print(f"####BANDAS DEBIGING#####[T2]:\n{out_path}")
        if not dry_run:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "wb") as fh:
                fh.write(multiband)

        return {
            "production_id": prod_id,
            "scene_name": scene_name,
            "temp_dir": temp_dir,
            "multiband_path": out_path,
            "dry_run": dry_run,
            "db_writes": False,
            "multiband_reused": False,
            **cloud_info,
        }

    def build_temp_multiband_from_cache(self, production: dict, scene: dict, dry_run: bool = False) -> dict:
        prod_id = production.get("produccion_id")
        scene_name = scene.get("scene_name") or f"escena_{scene.get('s3_monitoring_escena_id')}"
        temp_dir = self.storage.ensure_local_dir(_scene_cache_key(scene_name))
        band_paths = [str(path) for path in _band_temp_paths(scene_name).values()]
        for band_path in band_paths:
            if not Path(band_path).exists():
                self.cache_bands_for_scene(production, scene, dry_run=dry_run)
                break
        band_paths = [str(path) for path in _band_temp_paths(scene_name).values()]
        missing_bands = [band_path for band_path in band_paths if not Path(band_path).exists()]
        if missing_bands:
            if dry_run:
                return {
                    "production_id": prod_id,
                    "scene_name": scene_name,
                    "temp_dir": temp_dir,
                    "multiband_path": str(_temp_multiband_path(scene_name, prod_id)),
                    "dry_run": dry_run,
                    "db_writes": False,
                    "multiband_reused": False,
                    "simulated": True,
                    "missing_bands": missing_bands,
                }
            raise ExternalResourceError(
                resource="temp_band",
                reason=f"No existe banda temporal requerida: {missing_bands[0]}",
                hint="Ejecuta primero `get_bandas` para esa escena.",
                detail={"temp_dir": temp_dir, "scene_name": scene_name, "production_id": prod_id},
            )
        bbox = parse_bbox(production.get("tile_bbox"))
        if bbox is None:
            raise ValueError("tile_bbox no parseable.")
        out_path = str(_temp_multiband_path(scene_name, prod_id))
        if dry_run and not Path(out_path).exists():
            return {
                "production_id": prod_id,
                "scene_name": scene_name,
                "temp_dir": temp_dir,
                "multiband_path": out_path,
                "dry_run": dry_run,
                "db_writes": False,
                "multiband_reused": False,
                "simulated": True,
                "missing_multiband": True,
            }
        band_sources = {band_name: path for band_name, path in zip(MULTIBAND_BANDS, band_paths)}
        cropped = self._fetch_from_urls_bandas(
            band_sources,
            bbox,
            *_dimensions(bbox, self.cfg.processing.resolution_meters),
            SceneResult(prod_id, scene_name, scene.get("fecha")),
        )
        cloud_info = {"production_cloud": None, "usable": None, "updated": False}
        if not dry_run:
            cloud_info = self._persist_scene_cloud_metrics(production, scene, cropped, bbox)
        if Path(out_path).exists():
            return {
                "production_id": prod_id,
                "scene_name": scene_name,
                "temp_dir": temp_dir,
                "multiband_path": out_path,
                "dry_run": dry_run,
                "db_writes": False,
                "multiband_reused": True,
                **cloud_info,
            }
        multiband = _stack_to_geotiff_bytes(cropped, bbox)
        if not dry_run:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "wb") as fh:
                fh.write(multiband)
        return {
            "production_id": prod_id,
            "scene_name": scene_name,
            "temp_dir": temp_dir,
            "multiband_path": out_path,
            "dry_run": dry_run,
            "db_writes": False,
            "multiband_reused": False,
            **cloud_info,
        }

    def generate_scene_assets_from_temp_bands(self, production: dict, scene: dict, dry_run: bool = False, regenerate: bool = False) -> dict:
        prod_id = production.get("produccion_id")
        scene_name = scene.get("scene_name") or f"escena_{scene.get('s3_monitoring_escena_id')}"
        temp_dir = self.storage.ensure_local_dir(_scene_cache_key(scene_name))
        band_paths = [str(path) for path in _band_temp_paths(scene_name).values()]
        for band_path in band_paths:
            if not Path(band_path).exists():
                self.cache_bands_for_scene(production, scene, dry_run=dry_run)
                break
        band_paths = [str(path) for path in _band_temp_paths(scene_name).values()]
        missing_bands = [band_path for band_path in band_paths if not Path(band_path).exists()]
        if missing_bands:
            if dry_run:
                return {
                    "production_id": prod_id,
                    "scene_name": scene_name,
                    "temp_dir": temp_dir,
                    "usable": False,
                    "files": [],
                    "db_writes": False,
                    "simulated": True,
                    "missing_bands": missing_bands,
                }
            raise ExternalResourceError(
                resource="temp_band",
                reason=f"No existe banda temporal requerida: {missing_bands[0]}",
                hint="Ejecuta primero `get_bandas` para esa escena.",
                detail={"temp_dir": temp_dir, "scene_name": scene_name, "production_id": prod_id},
            )

        bbox = parse_bbox(production.get("tile_bbox"))
        if bbox is None:
            raise ValueError("tile_bbox no parseable.")
        width, height = _dimensions(bbox, self.cfg.processing.resolution_meters)
        multiband_path = _temp_multiband_path(scene_name, prod_id)
        if dry_run and not multiband_path.exists():
            return {
                "production_id": prod_id,
                "scene_name": scene_name,
                "temp_dir": temp_dir,
                "usable": False,
                "files": [],
                "db_writes": False,
                "simulated": True,
                "missing_multiband": True,
            }
        band_sources = {band_name: path for band_name, path in zip(MULTIBAND_BANDS, band_paths)}
        cropped = self._fetch_from_urls_bandas(
            band_sources,
            bbox,
            width,
            height,
            SceneResult(prod_id, scene_name, scene.get("fecha")),
        )

        cloud_info = self._compute_scene_cloud_metrics(production, cropped, bbox)
        if not dry_run and cloud_info.get("production_cloud") is not None:
            cloud_info = self._persist_scene_cloud_metrics(production, scene, cropped, bbox)
        else:
            cloud_info["updated"] = False
        usable = bool(cloud_info.get("usable"))
        base = build_base_path(self.cfg.storage.base_path, prod_id, scene_name)
        files: list[dict[str, Any]] = []

        from app.db.mysql_client import MySQLWriteClient
        from app.db.repositories import SceneFileRepository, SceneRepository
        writer = MySQLWriteClient(self.cfg.mysql) if not dry_run else None
        file_repo = SceneFileRepository(writer) if writer else None
        scene_repo = SceneRepository(writer) if writer else None

        poly = parse_polygon(production.get("poligono"))
        if poly is None:
            raise ValueError("poligono ausente o no parseable; no se puede generar assets con cloud cover de producci?n.")
        poly_mask = _rasterize_polygon_mask(poly, bbox, width, height)

        multiband_key = f"{base}/{self.cfg.outputs.multiband_filename}"
        multiband_uri = f"s3://{self.cfg.storage.s3_bucket}/{multiband_key}"
        multiband_bytes = Path(_temp_multiband_path(scene_name, prod_id)).read_bytes()
        multiband_asset = self._store_generated_asset(
            file_repo=file_repo,
            scene_repo=scene_repo,
            scene_id=scene.get("s3_monitoring_escena_id"),
            key=multiband_key,
            uri=multiband_uri,
            data=multiband_bytes,
            role="multiband",
            tipo="truth_tif",
            extension="tif",
            dry_run=dry_run,
            regenerate=regenerate,
        )
        files.append(multiband_asset)

        if usable:
            params = self._build_params_document(production, scene, cropped, cloud_info, poly_mask)
            pbytes = json.dumps(params, ensure_ascii=False, indent=2).encode("utf-8")
            params_asset = self._store_generated_asset(
                file_repo=file_repo,
                scene_repo=scene_repo,
                scene_id=scene.get("s3_monitoring_escena_id"),
                key=f"{base}/{self.cfg.outputs.params_filename}",
                uri=f"s3://{self.cfg.storage.s3_bucket}/{base}/{self.cfg.outputs.params_filename}",
                data=pbytes,
                role="params",
                tipo="params",
                extension="json",
                dry_run=dry_run,
                regenerate=regenerate,
                json_content=pbytes.decode("utf-8"),
            )
            files.append(params_asset)

            if self.cfg.processing.generate_png:
                invalid = invalid_mask_from_scl(cropped) if self.cfg.processing.apply_cloud_mask else None
                for idx_name in self.cfg.processing.default_indices:
                    try:
                        if idx_name == "natural":
                            png = _natural_rgb_png_bytes(cropped, invalid)
                        elif idx_name == "false_color_veg":
                            png = _false_color_veg_png_bytes(cropped, invalid)
                        elif idx_name == "red_edge":
                            png = _red_edge_png_bytes(cropped, invalid)
                        elif idx_name == "swir":
                            png = _swir_png_bytes(cropped, invalid)
                        else:
                            idx_arr = compute_index(idx_name, cropped)
                            png = _index_to_png_bytes(idx_name, idx_arr, invalid)
                    except ValueError:
                        continue
                    png = _outline_png(png, poly, bbox, width, height)
                    obj = self._store_generated_asset(
                        file_repo=file_repo,
                        scene_repo=scene_repo,
                        scene_id=scene.get("s3_monitoring_escena_id"),
                        key=f"{base}/{idx_name}.png",
                        uri=f"s3://{self.cfg.storage.s3_bucket}/{base}/{idx_name}.png",
                        data=png,
                        role="index_png",
                        tipo="image",
                        extension="png",
                        dry_run=dry_run,
                        regenerate=regenerate,
                    )
                    files.append(obj)
        else:
            natural_png = _natural_rgb_png_bytes(cropped, None)
            natural_png = _outline_png(natural_png, poly, bbox, width, height)
            obj = self._store_generated_asset(
                file_repo=file_repo,
                scene_repo=scene_repo,
                scene_id=scene.get("s3_monitoring_escena_id"),
                key=f"{base}/natural.png",
                uri=f"s3://{self.cfg.storage.s3_bucket}/{base}/natural.png",
                data=natural_png,
                role="index_png",
                tipo="image",
                extension="png",
                dry_run=dry_run,
                regenerate=regenerate,
            )
            files.append(obj)

        return {
            "production_id": prod_id,
            "scene_name": scene_name,
            "temp_dir": temp_dir,
            "usable": usable,
            "files": files,
            "db_writes": not dry_run,
            **cloud_info,
        }

    def cleanup_temp_scene(self, scene_name: str, production_id: Any | None = None) -> dict[str, Any]:
        temp_dir = _band_temp_dir(scene_name, production_id)
        removed: list[str] = []
        if temp_dir.exists():
            for path in temp_dir.glob('*'):
                try:
                    if path.is_file():
                        path.unlink()
                        removed.append(str(path))
                except Exception:  # noqa: BLE001
                    continue
            try:
                temp_dir.rmdir()
            except Exception:  # noqa: BLE001
                pass
        return {"temp_dir": str(temp_dir), "removed": removed}

    def upload_temp_multiband(self, production: dict, scene: dict, dry_run: bool = False, regenerate: bool = False) -> dict:
        prod_id = production.get("produccion_id")
        scene_name = scene.get("scene_name") or f"escena_{scene.get('s3_monitoring_escena_id')}"
        temp_dir = self.storage.ensure_local_dir(_scene_cache_key(scene_name))
        multiband_path = str(_temp_multiband_path(scene_name, prod_id))
        if not Path(multiband_path).exists():
            raise ExternalResourceError(
                resource="temp_multiband",
                reason="No existe multiband temporal.",
                hint="Ejecuta primero `genera_tif` para esa escena.",
                detail={"temp_dir": temp_dir, "scene_name": scene_name, "production_id": prod_id},
            )

        from app.db.mysql_client import MySQLWriteClient
        from app.db.repositories import SceneFileRepository
        writer = MySQLWriteClient(self.cfg.mysql) if not dry_run else None
        repo = SceneFileRepository(writer) if writer else None
        key = f"{build_base_path(self.cfg.storage.base_path, prod_id, scene_name)}/{self.cfg.outputs.multiband_filename}"
        uri = f"s3://{self.cfg.storage.s3_bucket}/{key}"
        scene_id = scene.get("s3_monitoring_escena_id")
        existing = repo.get_by_scene_and_key(scene_id, key) if repo else None
        if existing and not regenerate:
            return {
                "production_id": prod_id,
                "scene_name": scene_name,
                "uploaded": False,
                "indexed": 0,
                "skipped": True,
                "already_indexed": True,
                "object": {
                    "key": existing.get("s3_key") or key,
                    "uri": existing.get("s3_uri") or uri,
                    "bytes_written": existing.get("size_bytes") or Path(multiband_path).stat().st_size,
                    "written": False,
                },
                "db_writes": not dry_run,
            }

        with open(multiband_path, "rb") as fh:
            data = fh.read()
        obj = self.storage.put_bytes(key, data, "image/tiff", dry_run)
        indexed = 0
        if not dry_run:
            indexed += 1
            indexed += self._index_uploaded_multiband(scene_id, obj.key, obj.uri, len(data))
        return {
            "production_id": prod_id,
            "scene_name": scene_name,
            "uploaded": obj.written,
            "indexed": indexed,
            "skipped": False,
            "already_indexed": bool(existing),
            "object": obj.__dict__,
            "db_writes": not dry_run,
        }

    def _index_uploaded_multiband(self, scene_id: Any, key: str, uri: str, size_bytes: int) -> int:
        from app.db.mysql_client import MySQLWriteClient
        from app.db.repositories import SceneFileRepository

        writer = MySQLWriteClient(self.cfg.mysql)
        repo = SceneFileRepository(writer)
        repo.upsert_file(
            s3_monitoring_escena_id=scene_id,
            s3_key=key,
            s3_uri=uri,
            extension="tif",
            tipo="truth_tif",
            size_bytes=size_bytes,
            existe=1,
        )
        repo.update_scene_truth_tif_exists(scene_id, 1)
        return 1

    def process_scene(
        self,
        production: dict,
        scene: dict,
        generate_files: bool,
        dry_run: bool,
    ) -> SceneResult:
        prod_id = production.get("produccion_id")
        scene_name = scene.get("scene_name") or f"escena_{scene.get('s3_monitoring_escena_id')}"
        fecha = scene.get("fecha")

        result = SceneResult(production_id=prod_id, scene_name=scene_name, fecha=fecha)

        # --- 1. bbox y polígono ---
        bbox = parse_bbox(production.get("tile_bbox"))
        if bbox is None:
            result.error = "tile_bbox ausente o no parseable."
            return result
        result.bbox = bbox

        polygon = parse_polygon(production.get("poligono"))
        if polygon is None:
            result.error = "poligono ausente o no parseable; no se puede calcular production_cloud."
            return result

        res_m = self.cfg.processing.resolution_meters
        width, height = _dimensions(bbox, res_m)

        # --- 2. cargar JSON de escena y resolver URLs de bandas ---
        fecha_str = str(fecha)[:10] if fecha else None
        if not fecha_str:
            result.error = "Escena sin fecha; no se puede procesar."
            return result
        search_date = _scene_date_from_name(scene_name) or fecha_str
        scene_json_uri = scene.get("scene_json_uri")
        urls_bandas: dict[str, str] = {}
        urls_bandas = _normalize_urls_bandas_value(scene.get("urls_bandas"), scene_name)
        if not urls_bandas and scene_json_uri:
            try:
                scene_json = _parse_scene_json(self.storage.read_text(scene_json_uri))
                urls_bandas = _normalize_urls_bandas(scene_json, scene_name)
                if urls_bandas:
                    result.notes.append("urls_bandas resueltas desde scene_json_uri.")
            except Exception as exc:  # noqa: BLE001
                raise ExternalResourceError(
                    resource="scene_json_uri",
                    reason=str(exc),
                    hint="Revisa credenciales AWS, permisos de S3 y que la URI exista.",
                    detail={"scene_json_uri": scene_json_uri, "production_id": prod_id, "scene_name": scene_name},
                ) from exc

        if search_date != fecha_str:
            result.notes.append(f"Búsqueda STAC con fecha de scene_name: {search_date}.")

        try:
            if urls_bandas:
                stack = self._fetch_from_urls_bandas(urls_bandas, bbox, width, height, result)
            else:
                raw_stack = self.earth_search.fetch_multiband(
                    bbox=bbox, date_from=search_date, date_to=search_date,
                    width=width, height=height, scene_name=scene_name,
                )
                stack = _align_stack_to_grid(raw_stack, width, height, result)
                if self.earth_search.last_item_id:
                    result.notes.append(f"Fuente Earth Search STAC: {self.earth_search.last_item_id}.")
        except Exception as exc:  # noqa: BLE001
            raise ExternalResourceError(
                resource="band_download",
                reason=str(exc),
                hint="Verifica urls_bandas en el JSON de escena o el acceso a Earth Search/S3.",
                detail={"production_id": prod_id, "scene_name": scene_name, "scene_json_uri": scene_json_uri},
            ) from exc

        # --- 3. máscara de nubes + máscara de polígono ---
        cloud = invalid_mask_from_scl(stack)
        poly_mask = _rasterize_polygon_mask(polygon, bbox, width, height)

                # --- 4. production_cloud: % de nube dentro del pol?gono ---
        cloud_info = self._compute_scene_cloud_metrics(production, stack, bbox)
        result.production_cloud = cloud_info.get("production_cloud")
        result.valid_pixels_percentage = cloud_info.get("valid_pixels_percentage")
        result.usable_suggested = cloud_info.get("usable")
        if result.production_cloud is None:
            result.notes.append("Pol?gono no intersecta el raster; production_cloud no calculable.")
        else:
            threshold = self.cfg.processing.max_production_cloud
            result.notes.append(
                f"usable sugerido={result.usable_suggested} "
                f"(production_cloud={result.production_cloud} <= {threshold})."
            )
            if not dry_run:
                self._persist_scene_cloud_metrics(production, scene, stack, bbox)

        usable = bool(result.usable_suggested)
        if not generate_files:
            result.notes.append("generate_files=false: solo métricas, sin archivos.")
            return result

        # --- 6. generar archivos derivados ---
        base = build_base_path(self.cfg.storage.base_path, prod_id, scene_name)
        scene_id = scene.get("s3_monitoring_escena_id")
        file_repo = None
        scene_repo = None
        if not dry_run:
            from app.db.mysql_client import MySQLWriteClient
            from app.db.repositories import SceneFileRepository, SceneRepository

            writer = MySQLWriteClient(self.cfg.mysql)
            file_repo = SceneFileRepository(writer)
            scene_repo = SceneRepository(writer)

        # multiband.tif
        if usable and self.cfg.processing.generate_geotiff:
            tif_bytes = _stack_to_geotiff_bytes(stack, bbox)
            obj = self.storage.put_bytes(
                f"{base}/{self.cfg.outputs.multiband_filename}",
                tif_bytes, "image/tiff", dry_run,
            )
            result.files.append(_to_file("multiband", obj))
            if file_repo:
                file_repo.upsert_file(
                    scene_id,
                    obj.key,
                    obj.uri,
                    extension="tif",
                    tipo="truth_tif",
                    size_bytes=len(tif_bytes),
                    existe=1,
                )
                file_repo.update_scene_truth_tif_exists(scene_id, 1)

        # params json
        if usable:
            params = self._build_params_document(production, scene, stack, {
                "production_cloud": result.production_cloud,
                "usable": result.usable_suggested,
            }, poly_mask)
            pbytes = json.dumps(params, ensure_ascii=False, indent=2).encode("utf-8")
            pobj = self.storage.put_bytes(
                f"{base}/{self.cfg.outputs.params_filename}", pbytes, "application/json", dry_run
            )
            result.files.append(_to_file("params", pobj))
            if file_repo and scene_repo:
                file_repo.upsert_json_file(
                    scene_id,
                    pobj.key,
                    pobj.uri,
                    "params",
                    pbytes.decode("utf-8"),
                    len(pbytes),
                )
                scene_repo.update_params_exists(scene_id, 1)
        else:
            result.notes.append("usable=False: params no generados; solo natural.")

        if usable and self.cfg.processing.generate_png:
            invalid = cloud if self.cfg.processing.apply_cloud_mask else None
            for idx_name in self.cfg.processing.default_indices:
                try:
                    idx_arr = compute_index(idx_name, stack)
                except ValueError:
                    result.notes.append(f"?ndice no soportado, omitido: {idx_name}")
                    continue
                png = _index_to_png_bytes(idx_name, idx_arr, invalid)
                obj = self.storage.put_bytes(
                    f"{base}/{idx_name}.png", png, "image/png", dry_run
                )
                result.files.append(_to_file("index_png", obj))
                if file_repo:
                    file_repo.upsert_file(
                        scene_id,
                        obj.key,
                        obj.uri,
                        extension="png",
                        tipo="image",
                        size_bytes=len(png),
                        existe=1,
                    )
        elif not usable:
            natural_png = _natural_rgb_png_bytes(stack, None)
            obj = self.storage.put_bytes(f"{base}/natural.png", natural_png, "image/png", dry_run)
            result.files.append(_to_file("index_png", obj))
            if file_repo:
                file_repo.upsert_file(
                    scene_id,
                    obj.key,
                    obj.uri,
                    extension="png",
                    tipo="image",
                    size_bytes=len(natural_png),
                    existe=1,
                )

        return result


def _to_file(role: str, obj: StoredObject) -> GeneratedFile:
    return GeneratedFile(role=role, key=obj.key, uri=obj.uri, bytes=obj.bytes_written, written=obj.written)
