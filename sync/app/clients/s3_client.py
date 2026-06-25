"""
Cliente S3 (boto3). Fuente de verdad para archivos derivados (Fase 2)
y artefactos de IA (Fase 3).
"""
import json
from typing import Any, Optional

import boto3

from app.core.logging_config import get_logger
from app.core.settings import get_settings

log = get_logger(__name__)


class S3Client:
    def __init__(self):
        s = get_settings()
        kwargs: dict[str, Any] = {"region_name": s.AWS_REGION}
        if s.AWS_ACCESS_KEY_ID_CUSTOM and s.AWS_SECRET_ACCESS_KEY_CUSTOM:
            kwargs["aws_access_key_id"] = s.AWS_ACCESS_KEY_ID_CUSTOM
            kwargs["aws_secret_access_key"] = s.AWS_SECRET_ACCESS_KEY_CUSTOM
            if s.AWS_SESSION_TOKEN_CUSTOM:
                kwargs["aws_session_token"] = s.AWS_SESSION_TOKEN_CUSTOM
        self._client = boto3.client("s3", **kwargs)

    def list_objects(self, bucket: str, prefix: str) -> list[dict]:
        """Lista objetos bajo un prefijo. Devuelve key, size, last_modified."""
        out: list[dict] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                out.append({
                    "key": obj["Key"],
                    "size": obj.get("Size"),
                    "last_modified": obj.get("LastModified"),
                })
        return out

    def head(self, bucket: str, key: str) -> Optional[dict]:
        try:
            resp = self._client.head_object(Bucket=bucket, Key=key)
            return {
                "key": key,
                "size": resp.get("ContentLength"),
                "last_modified": resp.get("LastModified"),
            }
        except self._client.exceptions.ClientError:
            return None

    def get_json(self, bucket: str, key: str) -> Optional[dict]:
        try:
            resp = self._client.get_object(Bucket=bucket, Key=key)
            body = resp["Body"].read()
            return json.loads(body)
        except self._client.exceptions.NoSuchKey:
            return None
        except json.JSONDecodeError as exc:
            log.warning("JSON invalido en s3://%s/%s: %s", bucket, key, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning("Error leyendo s3://%s/%s: %s", bucket, key, exc)
            return None

    def get_text(self, bucket: str, key: str) -> Optional[str]:
        try:
            resp = self._client.get_object(Bucket=bucket, Key=key)
            return resp["Body"].read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            log.warning("Error leyendo texto s3://%s/%s: %s", bucket, key, exc)
            return None


_client: Optional[S3Client] = None


def get_s3() -> S3Client:
    global _client
    if _client is None:
        _client = S3Client()
    return _client
