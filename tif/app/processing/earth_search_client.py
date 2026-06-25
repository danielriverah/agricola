"""
Cliente Earth Search / STAC para Sentinel-2 L2A.

Busca la escena en https://earth-search.aws.element84.com/v1/search, descarga
cada banda como COG público y la recorta/reproyecta al grid definido por
`tile_bbox`. No escribe en bases de datos.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.config.models import EarthSearchConfig
from app.processing.indices import MULTIBAND_BANDS

logger = logging.getLogger("tif.earth_search")


class EarthSearchClient:
    def __init__(self, cfg: EarthSearchConfig) -> None:
        self.cfg = cfg
        self.last_item_id: str | None = None

    def fetch_multiband(
        self,
        bbox: tuple[float, float, float, float],
        date_from: str,
        date_to: str,
        width: int,
        height: int,
        bands: list[str] | None = None,
        scene_name: str | None = None,
    ) -> np.ndarray:
        """
        Devuelve array (n_bands, height, width) float32.

        Las bandas 20m se remuestrean al grid objetivo; SCL usa nearest para no
        deformar clases, reflectancias usan bilinear.
        """
        import rasterio
        from affine import Affine

        selected_bands = bands or MULTIBAND_BANDS
        item = self._search_item(bbox, date_from, date_to, scene_name)
        self.last_item_id = item.get("id")

        minx, miny, maxx, maxy = bbox
        dst_transform = Affine(
            (maxx - minx) / width, 0, minx,
            0, -(maxy - miny) / height, maxy,
        )

        stack: list[np.ndarray] = []
        with rasterio.Env(AWS_NO_SIGN_REQUEST="YES", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
            for band_name in selected_bands:
                href = self._asset_href(item, band_name)
                stack.append(
                    self._read_asset_to_grid(
                        href=href,
                        band_name=band_name,
                        width=width,
                        height=height,
                        dst_transform=dst_transform,
                    )
                )
        return np.stack(stack, axis=0).astype("float32")

    def _search_item(
        self,
        bbox: tuple[float, float, float, float],
        date_from: str,
        date_to: str,
        scene_name: str | None,
    ) -> dict[str, Any]:
        import requests

        payload: dict[str, Any] = {
            "collections": [self.cfg.collection],
            "bbox": list(bbox),
            "datetime": f"{date_from}T00:00:00Z/{date_to}T23:59:59Z",
            "limit": 20,
            "query": {"eo:cloud_cover": {"lte": self.cfg.max_cloud_coverage}},
            "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        }
        response = requests.post(
            self.cfg.search_url,
            json=payload,
            timeout=self.cfg.request_timeout_seconds,
        )
        if response.status_code == 400:
            logger.warning("Earth Search rechazó query avanzada; reintentando búsqueda básica.")
            payload.pop("query", None)
            payload.pop("sortby", None)
            response = requests.post(
                self.cfg.search_url,
                json=payload,
                timeout=self.cfg.request_timeout_seconds,
            )
        response.raise_for_status()
        features = response.json().get("features") or []
        if not features:
            raise ValueError(
                "Earth Search no devolvió escenas para "
                f"{date_from}/{date_to} bbox={list(bbox)}."
            )

        if scene_name:
            normalized = scene_name.lower()
            for item in features:
                item_id = str(item.get("id") or "").lower()
                if normalized in item_id or item_id in normalized:
                    return item

        return self._lowest_cloud_item(features)

    @staticmethod
    def _lowest_cloud_item(features: list[dict[str, Any]]) -> dict[str, Any]:
        return sorted(
            features,
            key=lambda item: item.get("properties", {}).get("eo:cloud_cover", 9999),
        )[0]

    def _asset_href(self, item: dict[str, Any], band_name: str) -> str:
        assets = item.get("assets") or {}
        preferred = self.cfg.asset_map.get(band_name) or self.cfg.asset_map.get(band_name.upper())
        candidates = [
            preferred,
            band_name,
            band_name.lower(),
            band_name.upper(),
        ]
        for candidate in candidates:
            if candidate and candidate in assets and assets[candidate].get("href"):
                return assets[candidate]["href"]
        raise ValueError(
            f"La escena {item.get('id')} no contiene asset para banda {band_name}. "
            f"Assets disponibles: {', '.join(sorted(assets.keys()))}"
        )

    def _read_asset_to_grid(
        self,
        href: str,
        band_name: str,
        width: int,
        height: int,
        dst_transform,
    ) -> np.ndarray:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.warp import reproject

        categorical = band_name.upper() in {"SCL", "QA60", "MASK", "CLOUD_MASK"}
        dst_nodata = 0 if categorical else np.nan
        destination = np.full((height, width), dst_nodata, dtype="float32")

        with rasterio.open(href) as src:
            reproject(
                source=rasterio.band(src, 1),
                destination=destination,
                src_transform=src.transform,
                src_crs=src.crs,
                src_nodata=src.nodata,
                dst_transform=dst_transform,
                dst_crs="EPSG:4326",
                dst_nodata=dst_nodata,
                resampling=Resampling.nearest if categorical else Resampling.bilinear,
            )

        if categorical:
            return np.nan_to_num(destination, nan=0).astype("float32")
        return self._normalize_reflectance(destination)

    @staticmethod
    def _normalize_reflectance(values: np.ndarray) -> np.ndarray:
        finite = np.isfinite(values)
        if not finite.any():
            return values.astype("float32")
        p99 = float(np.nanpercentile(values[finite], 99))
        if p99 > 2:
            values = values / 10000.0
        return values.astype("float32")
