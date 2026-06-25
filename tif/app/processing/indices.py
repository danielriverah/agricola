"""
Definición de bandas Sentinel-2 L2A y de los índices espectrales soportados.

El multiband.tif se construye con las bandas crudas necesarias; los índices se
calculan a partir de esas bandas (en memoria o como derivados PNG/GeoTIFF).
"""

from __future__ import annotations

import numpy as np

# Bandas Sentinel-2 que necesitamos para todos los índices del README.
# (reflectancias 0..1)
MULTIBAND_BANDS = [
    "B02",  # blue
    "B03",  # green
    "B04",  # red
    "B05",  # red edge 1
    "B06",  # red edge 2
    "B07",  # red edge 3
    "B08",  # nir
    "B8A",  # nir narrow
    "B11",  # swir1
    "B12",  # swir2
    "SCL",  # scene classification (para máscara de nubes)
]

# SCL separada en clases de nube real vs no-v?lidos.
SCL_CLOUD = {3, 7, 8, 9, 10}
# 3=cloud shadow, 8=cloud medium, 9=cloud high, 10=cirrus
SCL_INVALID = {0, 1, 2, 3, 7, 8, 9, 10, 11}
# 0/1/2 no data/saturated/dark, 11=snow; INVALID se usa para visualizaci?n y an?lisis confiable.


def band_index(name: str) -> int:
    return MULTIBAND_BANDS.index(name)


def _safe_div(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    out = np.zeros_like(num, dtype="float32")
    np.divide(num, den, out=out, where=(den != 0))
    return out


def compute_index(name: str, stack: np.ndarray) -> np.ndarray:
    """
    Calcula un índice a partir del stack (bandas, alto, ancho) ordenado como
    MULTIBAND_BANDS. Devuelve un array float32.
    """
    b = {n: stack[i].astype("float32") for i, n in enumerate(MULTIBAND_BANDS)}
    blue, green, red = b["B02"], b["B03"], b["B04"]
    re1, re3 = b["B05"], b["B07"]
    nir, swir1, swir2 = b["B08"], b["B11"], b["B12"]

    name = name.lower()
    if name == "ndvi":
        return _safe_div(nir - red, nir + red)
    if name == "gndvi":
        return _safe_div(nir - green, nir + green)
    if name == "ndre":
        return _safe_div(nir - re1, nir + re1)
    if name == "red_edge":
        return _safe_div(re3 - red, re3 + red)
    if name == "savi":
        L = 0.5
        return _safe_div((nir - red) * (1 + L), nir + red + L)
    if name == "evi":
        return 2.5 * _safe_div(nir - red, nir + 6 * red - 7.5 * blue + 1)
    if name == "ndwi":
        return _safe_div(green - nir, green + nir)
    if name == "ndmi":
        return _safe_div(nir - swir1, nir + swir1)
    if name == "nbr":
        return _safe_div(nir - swir2, nir + swir2)
    if name == "natural":
        # composición se maneja como RGB en el render; aquí devolvemos rojo.
        return red
    if name == "false_color_veg":
        return nir
    if name == "swir":
        return swir1
    raise ValueError(f"Índice no soportado: {name}")


def cloud_mask_from_scl(stack: np.ndarray) -> np.ndarray:
    """Devuelve m?scara booleana True=nube real/sombra de nube a partir de SCL."""
    scl = np.rint(stack[band_index("SCL")]).astype("int16")
    return np.isin(scl, list(SCL_CLOUD))


def invalid_mask_from_scl(stack: np.ndarray) -> np.ndarray:
    """Devuelve m?scara booleana True=no v?lido para visualizaci?n/an?lisis."""
    scl = np.rint(stack[band_index("SCL")]).astype("int16")
    return np.isin(scl, list(SCL_INVALID))
