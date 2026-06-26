from pydantic import BaseModel


class SecurityConfig(BaseModel):
    api_secret_key: str
    allowed_origins: list[str] = []


class StorageConfig(BaseModel):
    driver: str
    aws_region: str = "us-east-1"
    s3_bucket: str
    base_path: str
    public_url_ttl_minutes: int = 60


class AIProviderConfig(BaseModel):
    enabled: bool = False
    api_key: str = ""
    model: str = ""
    base_url: str = ""


class AIConfig(BaseModel):
    provider: str
    timeout: int = 60
    max_tokens: int = 2500
    temperature: float = 0.2
    fallback_provider: str | None = None
    providers: dict[str, AIProviderConfig] = {}


class AgronomicRules(BaseModel):
    ndvi_drop_alert_pct: float = 15
    ndmi_drop_alert_pct: float = 20
    ndre_drop_alert_pct: float = 15
    bsi_rise_alert_pct: float = 50
    min_valid_pixels_percentage: float = 80


class LaravelConfig(BaseModel):
    webhook_url: str
    webhook_secret: str
    timeout: int = 30


class IAServiceConfig(BaseModel):
    security: SecurityConfig
    storage: StorageConfig
    ai: AIConfig
    agronomic_rules: AgronomicRules
    laravel: LaravelConfig
    version: int = 1
