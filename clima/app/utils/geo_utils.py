from typing import Optional, Tuple


def primer_punto_poligono(poligono: str) -> Optional[Tuple[float, float]]:
    """
    Recibe el polígono en formato 'lat,lon|lat,lon|...'
    Retorna (lat, lon) del primer punto, o None si el formato es inválido.
    """
    if not poligono:
        return None
    try:
        primer = poligono.strip().split("|")[0]
        partes = primer.strip().split(",")
        lat = float(partes[0].strip())
        lon = float(partes[1].strip())
        return lat, lon
    except (IndexError, ValueError):
        return None
