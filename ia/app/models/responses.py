from pydantic import BaseModel


class RulesResult(BaseModel):
    risk_level: str
    alerts: list[str]


class AIResult(BaseModel):
    risk_level: str
    summary: str
    probable_causes: list[str]
    recommendations: list[str]
    confidence: str
    limitations: list[str]


class Meta(BaseModel):
    ai_provider: str
    ai_model: str
    config_version: int
    warnings: list[str]
    processing_seconds: float


class AnalyzeResponse(BaseModel):
    ok: bool
    job_id: str
    lot_id: int
    status: str
    rules_result: RulesResult
    ai_result: AIResult | None = None
    meta: Meta
