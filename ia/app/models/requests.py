from pydantic import BaseModel


class ImageQualityInput(BaseModel):
    cloud_percentage: float
    valid_pixels_percentage: float
    confidence: str


class IndexInput(BaseModel):
    mean: float
    std: float


class WeatherContext(BaseModel):
    rainfall_7_days_mm: float | None = None
    avg_temperature_c: float | None = None


class AnalyzeRequest(BaseModel):
    job_id: str
    lot_id: int
    lot_name: str
    crop: str
    phenological_stage: str | None = None
    area_ha: float
    analysis_date: str
    image_quality: ImageQualityInput
    indices: dict[str, IndexInput]
    weather_context: WeatherContext | None = None
