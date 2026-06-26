from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
import json


@dataclass
class ProduccionMonitoreo:
    produccion_id: int
    estatus: str
    fecha_siembra: date
    dias_max_monitoreo: int
    lat: Optional[float] = None
    lon: Optional[float] = None
    poligono: Optional[str] = None


@dataclass
class ClimadiarioRecord:
    produccion_id: int
    fecha: date
    tipo_dato: str           # historico_confirmado | actual | forecast
    fuente: str              # historical_api | forecast_api
    fecha_consulta: datetime
    horizonte_dia: Optional[int]

    temp_max: Optional[float] = None
    temp_min: Optional[float] = None
    temp_prom: Optional[float] = None
    humedad_prom: Optional[float] = None
    precipitacion_mm: Optional[float] = None
    lluvia_mm: Optional[float] = None
    probabilidad_lluvia_max: Optional[float] = None
    viento_max_kmh: Optional[float] = None
    radiacion_solar_mj: Optional[float] = None
    evapotranspiracion_mm: Optional[float] = None

    riesgo_helada: int = 0
    riesgo_estres_hidrico: int = 0
    riesgo_lluvia: int = 0

    riesgo_helada_pct: float = 0.0
    riesgo_estres_hidrico_pct: float = 0.0
    riesgo_lluvia_pct: float = 0.0
    riesgo_viento_pct: float = 0.0
    riesgo_enfermedad_pct: float = 0.0
    riesgo_plaga_pct: float = 0.0

    recomendacion: Optional[str] = None
    raw_json: Optional[dict] = field(default=None)
    bloqueado: int = 0

    def calcular_riesgos(self) -> None:
        self.riesgo_helada = 0
        self.riesgo_estres_hidrico = 0
        self.riesgo_lluvia = 0

        self.riesgo_helada_pct = 0.0
        self.riesgo_estres_hidrico_pct = 0.0
        self.riesgo_lluvia_pct = 0.0
        self.riesgo_viento_pct = 0.0
        self.riesgo_enfermedad_pct = 0.0
        self.riesgo_plaga_pct = 0.0

        if self.temp_min is not None and self.temp_min <= 2:
            self.riesgo_helada = 1
            self.riesgo_helada_pct = 100.0
        if (
            self.temp_max is not None and self.temp_max >= 35
            and (self.precipitacion_mm is None or self.precipitacion_mm < 1)
        ):
            self.riesgo_estres_hidrico = 1
            self.riesgo_estres_hidrico_pct = 100.0

        lluvia_pct = 0.0
        if self.probabilidad_lluvia_max is not None:
            lluvia_pct = max(lluvia_pct, max(0.0, min(100.0, float(self.probabilidad_lluvia_max))))
        if self.precipitacion_mm is not None:
            lluvia = self.precipitacion_mm
            if lluvia >= 20:
                lluvia_pct = max(lluvia_pct, 100.0)
            elif lluvia >= 10:
                lluvia_pct = max(lluvia_pct, 75.0)
            elif lluvia >= 5:
                lluvia_pct = max(lluvia_pct, 50.0)
            elif lluvia >= 1:
                lluvia_pct = max(lluvia_pct, 25.0)

        self.riesgo_lluvia_pct = lluvia_pct
        if (
            (self.probabilidad_lluvia_max is not None and self.probabilidad_lluvia_max >= 70)
            or (self.precipitacion_mm is not None and self.precipitacion_mm >= 20)
        ):
            self.riesgo_lluvia = 1
        elif self.riesgo_lluvia_pct >= 25:
            self.riesgo_lluvia = 1

        if self.viento_max_kmh is not None:
            if self.viento_max_kmh >= 60:
                self.riesgo_viento_pct = 100.0
            elif self.viento_max_kmh >= 40:
                self.riesgo_viento_pct = 75.0
            elif self.viento_max_kmh >= 25:
                self.riesgo_viento_pct = 50.0
            elif self.viento_max_kmh >= 15:
                self.riesgo_viento_pct = 25.0

    def raw_json_str(self) -> Optional[str]:
        return json.dumps(self.raw_json) if self.raw_json else None
