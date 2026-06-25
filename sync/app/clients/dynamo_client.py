"""
Cliente DynamoDB.

Soporta:
  - DYNAMODB_USE_AWS=true  -> Dynamo real en AWS (endpoint omitible)
  - DYNAMODB_USE_AWS=false -> Dynamo local / endpoint alterno
"""
from decimal import Decimal
from typing import Any, Optional
from datetime import date, datetime

import boto3
from boto3.dynamodb.conditions import Attr, Key

from app.core.logging_config import get_logger
from app.core.settings import get_settings

log = get_logger(__name__)


def _to_native(obj: Any) -> Any:
    """Convierte Decimals de Dynamo a int/float nativos de forma recursiva."""
    if isinstance(obj, list):
        return [_to_native(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        # int si no tiene parte fraccionaria
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj


def _normalize_date_string(value: Any, field_name: str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError(f"{field_name} es obligatorio")
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise ValueError(f"{field_name} debe tener formato YYYY-MM-DD") from exc
    raise ValueError(f"{field_name} debe ser fecha o string YYYY-MM-DD")


class DynamoClient:
    def __init__(self):
        self._settings = get_settings()
        self._resource = self._build_resource()

    def _build_resource(self):
        s = self._settings
        kwargs: dict[str, Any] = {"region_name": s.AWS_REGION}

        if s.AWS_ACCESS_KEY_ID_CUSTOM and s.AWS_SECRET_ACCESS_KEY_CUSTOM:
            kwargs["aws_access_key_id"] = s.AWS_ACCESS_KEY_ID_CUSTOM
            kwargs["aws_secret_access_key"] = s.AWS_SECRET_ACCESS_KEY_CUSTOM
            if s.AWS_SESSION_TOKEN_CUSTOM:
                kwargs["aws_session_token"] = s.AWS_SESSION_TOKEN_CUSTOM

        if not s.DYNAMODB_USE_AWS and s.DYNAMODB_ENDPOINT_URL:
            kwargs["endpoint_url"] = s.DYNAMODB_ENDPOINT_URL
            # boto exige credenciales aunque sea local
            kwargs.setdefault("aws_access_key_id", "local")
            kwargs.setdefault("aws_secret_access_key", "local")

        log.info(
            "DynamoDB resource | use_aws=%s endpoint=%s region=%s",
            s.DYNAMODB_USE_AWS, kwargs.get("endpoint_url", "<aws>"), s.AWS_REGION,
        )
        return boto3.resource("dynamodb", **kwargs)

    def table(self, name: str):
        return self._resource.Table(name)

    def get_item(self, table_name: str, key: dict) -> Optional[dict]:
        # Normaliza números enteros para evitar desajustes con el schema N de Dynamo.
        normalized_key = {}
        for k, v in key.items():
            if isinstance(v, bool):
                normalized_key[k] = v
            elif isinstance(v, int):
                normalized_key[k] = v
            else:
                normalized_key[k] = v
        resp = self.table(table_name).get_item(Key=normalized_key)
        item = resp.get("Item")
        return _to_native(item) if item else None

    def query(self, table_name: str, key_name: str, key_value: Any) -> list[dict]:
        items: list[dict] = []
        table = self.table(table_name)
        kwargs = {"KeyConditionExpression": Key(key_name).eq(key_value)}
        while True:
            resp = table.query(**kwargs)
            items.extend(resp.get("Items", []))
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
        return _to_native(items)

    def scan(self, table_name: str, limit: Optional[int] = None) -> list[dict]:
        items: list[dict] = []
        table = self.table(table_name)
        kwargs: dict[str, Any] = {}
        while True:
            resp = table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            if limit and len(items) >= limit:
                items = items[:limit]
                break
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
        return _to_native(items)

    def scan_by_fecha_production(
        self,
        table_name: str,
        production_id: int,
        date_from: str,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        if production_id is None:
            return []
        if not date_from:
            raise ValueError("date_from es obligatorio")
        date_from = _normalize_date_string(date_from, "date_from")
        if date_to:
            date_to = _normalize_date_string(date_to, "date_to")
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
            dt_to = datetime.strptime(date_to, "%Y-%m-%d")

            # Si fecha fin < fecha inicio usar fecha inicio
            if dt_to < dt_from:
                date_to = date_from
        table = self.table(table_name)
        pk = f"PROD#{production_id}"
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("id").eq(pk)
        }
        if date_to:
            kwargs["FilterExpression"] = Attr("fecha").between(date_from, date_to)
        else:
            kwargs["FilterExpression"] = Attr("fecha").gte(date_from)

        items: list[dict] = []

        while True:
            resp = table.query(**kwargs)

            items.extend(resp.get("Items", []))

            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break

            kwargs["ExclusiveStartKey"] = lek

        return _to_native(items)

_client: Optional[DynamoClient] = None


def get_dynamo() -> DynamoClient:
    global _client
    if _client is None:
        _client = DynamoClient()
    return _client
