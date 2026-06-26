from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Literal

import boto3
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from app.core.config import (
    AWS_ACCESS_KEY_ID_CUSTOM,
    AWS_REGION,
    AWS_SECRET_ACCESS_KEY_CUSTOM,
    AWS_SESSION_TOKEN_CUSTOM,
    BEDROCK_API_KEY,
    BEDROCK_AUTH_MODE,
    BEDROCK_MODEL_ID,
)

router = APIRouter(tags=["analyze"])


SYSTEM_PROMPT = """Eres un asistente experto en análisis agrícola, agricultura de precisión e interpretación de índices satelitales.

Tu tarea es analizar datos de una producción agrícola usando información de cultivo, lote, rancho, fecha de plantación, fecha de escena, índices vegetativos e histórico.

Debes responder ÚNICAMENTE en formato JSON válido.
No uses Markdown.
No expliques fuera del JSON.
No incluyas texto adicional antes ni después.
No inventes datos no proporcionados.
Si un dato opcional no viene informado, usa null.
Si no hay suficiente información para afirmar algo, indícalo en el campo correspondiente con severidad baja o como observación.

Datos que puedes recibir:
- id_produccion
- rancho
- lote
- cultivo
- variedades
- fecha_plantacion
- fecha_escena
- fecha_analisis
- dias_despues_plantacion
- etapa_fenologica
- tipo_suelo
- clima_historico
- pronostico
- indices actuales
- indices anteriores
- historico
- zonas_detectadas

Reglas:
1. Si etapa_fenologica no viene informada, estímala usando fecha_plantacion y fecha_escena.
2. Si dias_despues_plantacion no viene informado, calcúlalo con fecha_escena - fecha_plantacion.
3. Interpreta el NDVI como vigor vegetal.
4. Interpreta el NDWI/NDMI como posible indicador de humedad o estrés hídrico.
5. Interpreta NDRE como indicador relacionado con clorofila/nitrógeno.
6. Interpreta SAVI cuando el cultivo tenga baja cobertura vegetal o etapa temprana.
7. Compara índices actuales contra anteriores cuando existan.
8. Usa el histórico solo como referencia de tendencia, no como diagnóstico absoluto.
9. Las recomendaciones deben ser prácticas, accionables y orientadas a campo.
10. No recomiendes aplicaciones químicas específicas si no hay evidencia suficiente.
11. No afirmes plagas, enfermedades o deficiencias nutricionales como hechos; usa términos como "posible", "sugerido por", "conviene verificar en campo".
12. El resultado debe ajustarse exactamente al esquema solicitado.

IMPORTANTE: Devuelve SOLO este esquema compacto (nada más):
{
  "estado_clave": "optimo"|"bueno"|"alerta"|"critico",
  "estado_general": string,
  "resumen": string,
  "hallazgos": [{"tipo": string, "zona": string, "severidad": "baja"|"media"|"alta", "descripcion": string}],
  "recomendaciones": [string],
  "riesgo": {"nivel": "bajo"|"medio"|"alto", "motivo": string}
}
"""


class AnalyzeSceneRequest(BaseModel):
    payload: dict[str, Any]


class HallazgoOut(BaseModel):
    tipo: str
    zona: str
    severidad: Literal["baja", "media", "alta"]
    descripcion: str


class RiesgoOut(BaseModel):
    nivel: Literal["bajo", "medio", "alto"]
    motivo: str


class OpcionalesOut(BaseModel):
    etapa_fenologica_manual: str | None = None
    tipo_suelo: str | None = None
    clima_historico: dict[str, Any] | None = None
    pronostico: dict[str, Any] | None = None


class AnalyzeSceneOutput(BaseModel):
    estado_clave: Literal["optimo", "bueno", "alerta", "critico"]
    estado_general: str
    resumen: str
    hallazgos: list[HallazgoOut] = Field(default_factory=list)
    recomendaciones: list[str] = Field(default_factory=list)
    riesgo: RiesgoOut


def _build_bedrock_client():
    kwargs: dict[str, Any] = {"region_name": AWS_REGION}
    if BEDROCK_AUTH_MODE == "api_key":
        if not BEDROCK_API_KEY:
            raise HTTPException(
                status_code=500,
                detail="BEDROCK_AUTH_MODE=api_key but BEDROCK_API_KEY/AWS_BEARER_TOKEN_BEDROCK is missing",
            )
        # Botocore reads this token for Bedrock API key auth mode.
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = BEDROCK_API_KEY
    else:
        if AWS_ACCESS_KEY_ID_CUSTOM and AWS_SECRET_ACCESS_KEY_CUSTOM:
            kwargs["aws_access_key_id"] = AWS_ACCESS_KEY_ID_CUSTOM
            kwargs["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY_CUSTOM
            if AWS_SESSION_TOKEN_CUSTOM:
                kwargs["aws_session_token"] = AWS_SESSION_TOKEN_CUSTOM
    return boto3.client("bedrock-runtime", **kwargs)


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    # Remove markdown code fences commonly returned by LLMs.
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("Model response does not contain valid JSON")


def _invoke_bedrock_text(client: Any, model_input: dict[str, Any]) -> str:
    resp = client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(model_input).encode("utf-8"),
        contentType="application/json",
        accept="application/json",
    )
    raw = json.loads(resp["body"].read().decode("utf-8"))
    content = raw.get("content") or []
    text = ""
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text += str(block.get("text") or "")
    return text


def _invoke_bedrock_json(client: Any, model_input: dict[str, Any]) -> tuple[dict[str, Any], str]:
    text = _invoke_bedrock_text(client, model_input)
    parsed = _extract_json(text)
    return parsed, text


def _build_prompt_preview(payload: dict[str, Any]) -> dict[str, Any]:
    user_payload_json = json.dumps(payload, ensure_ascii=False)
    user_text = (
        "Devuelve solo JSON válido, compacto, sin markdown, sin bloques de código, y exactamente con el esquema solicitado.\n"
        f"{user_payload_json}"
    )
    return {
        "model_id": BEDROCK_MODEL_ID,
        "auth_mode": BEDROCK_AUTH_MODE,
        "region": AWS_REGION,
        "system_prompt": SYSTEM_PROMPT,
        "user_prompt": user_text,
        "model_input": {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2600,
            "temperature": 0.0,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Devuelve solo JSON válido, compacto, sin markdown, sin bloques de código, y exactamente con el esquema solicitado.",
                        },
                        {"type": "text", "text": user_payload_json},
                    ],
                }
            ],
        },
    }


@router.post("/prompt-preview")
async def prompt_preview(req: AnalyzeSceneRequest):
    """
    Devuelve el prompt exacto que se enviaría a Bedrock, sin ejecutar el análisis.
    """
    preview = _build_prompt_preview(req.payload)
    return preview


@router.post("/analyze", response_model=AnalyzeSceneOutput)
async def analyze(req: AnalyzeSceneRequest):
    client = _build_bedrock_client()
    user_payload = req.payload
    preview = _build_prompt_preview(user_payload)
    model_input = preview["model_input"]
    try:
        first_text = ""
        second_text = ""
        try:
            first_text = _invoke_bedrock_text(client, model_input)
            parsed = _extract_json(first_text)
        except Exception as first_exc:
            # Retry once asking the model to repair/emit strict JSON only.
            repair_input = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 3000,
                "temperature": 0.0,
                "system": SYSTEM_PROMPT,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Devuelve de nuevo la respuesta en JSON válido estricto, "
                                    "sin texto adicional, sin markdown, sin bloques ```json, "
                                    "cumpliendo exactamente el esquema."
                                ),
                            },
                            {"type": "text", "text": json.dumps(user_payload, ensure_ascii=False)},
                        ],
                    }
                ],
            }
            try:
                second_text = _invoke_bedrock_text(client, repair_input)
                parsed = _extract_json(second_text)
            except Exception as second_exc:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "bedrock_json_parse_failed",
                        "message": "Bedrock devolvió texto no parseable a JSON tras reintento.",
                        "hint": "Posible salida truncada por tokens o JSON con formato libre. Reintenta con respuesta mas compacta.",
                        "model_id": BEDROCK_MODEL_ID,
                        "region": AWS_REGION,
                        "auth_mode": BEDROCK_AUTH_MODE,
                        "first_error": f"{type(first_exc).__name__}: {first_exc}",
                        "second_error": f"{type(second_exc).__name__}: {second_exc}",
                        "first_raw_response": (first_text or "")[:12000] or None,
                        "second_raw_response": (second_text or "")[:12000] or None,
                    },
                ) from first_exc
        validated = AnalyzeSceneOutput.model_validate(parsed)
        return validated
    except ValidationError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "ia_output_schema_validation_failed",
                "model_id": BEDROCK_MODEL_ID,
                "region": AWS_REGION,
                "auth_mode": BEDROCK_AUTH_MODE,
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "bedrock_analyze_failed",
                "model_id": BEDROCK_MODEL_ID,
                "region": AWS_REGION,
                "auth_mode": BEDROCK_AUTH_MODE,
                "message": f"{type(exc).__name__}: {exc}",
            },
        ) from exc
