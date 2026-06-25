"""
Capa de almacenamiento para archivos técnicos derivados (multiband.tif, PNG,
JSON de parámetros).

TIF puede ESCRIBIR archivos en S3/almacenamiento, pero NO indexa nada en MySQL.
Siempre devuelve las rutas generadas para que otro microservicio decida si las
indexa (regla 10 del README).

Soporta dry_run: en ese caso no escribe nada y solo devuelve lo que haría.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from app.config.models import StorageConfig

logger = logging.getLogger("tif.storage")


@dataclass
class StoredObject:
    key: str
    uri: str
    bytes_written: int
    written: bool  # False si dry_run


class StorageDriver:
    def put_bytes(self, key: str, data: bytes, content_type: str, dry_run: bool) -> StoredObject:
        raise NotImplementedError

    def public_url(self, key: str, ttl_minutes: int) -> str | None:
        raise NotImplementedError

    def read_text(self, uri: str) -> str:
        raise NotImplementedError

    def ensure_local_dir(self, key_prefix: str) -> str:
        raise NotImplementedError


class S3StorageDriver(StorageDriver):
    def __init__(self, cfg: StorageConfig) -> None:
        self.cfg = cfg

    def _client(self):
        import boto3
        from app.config.env_settings import get_env_settings

        env = get_env_settings()
        kwargs = {"region_name": env.aws_region}
        if env.aws_access_key_id and env.aws_secret_access_key:
            kwargs["aws_access_key_id"] = env.aws_access_key_id
            kwargs["aws_secret_access_key"] = env.aws_secret_access_key
            if env.aws_session_token:
                kwargs["aws_session_token"] = env.aws_session_token
        return boto3.client("s3", **kwargs)

    def _uri(self, key: str) -> str:
        return f"s3://{self.cfg.s3_bucket}/{key}"

    def put_bytes(self, key: str, data: bytes, content_type: str, dry_run: bool) -> StoredObject:
        if dry_run:
            return StoredObject(key=key, uri=self._uri(key), bytes_written=len(data), written=False)
        self._client().put_object(
            Bucket=self.cfg.s3_bucket, Key=key, Body=data, ContentType=content_type
        )
        return StoredObject(key=key, uri=self._uri(key), bytes_written=len(data), written=True)

    def public_url(self, key: str, ttl_minutes: int) -> str | None:
        try:
            return self._client().generate_presigned_url(
                "get_object",
                Params={"Bucket": self.cfg.s3_bucket, "Key": key},
                ExpiresIn=ttl_minutes * 60,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo generar presigned URL: %s", exc)
            return None

    def read_text(self, uri: str) -> str:
        import boto3

        if not uri.startswith("s3://"):
            raise ValueError(f"URI S3 inválida: {uri}")
        bucket_key = uri[5:]
        bucket, key = bucket_key.split("/", 1)
        obj = self._client().get_object(Bucket=bucket, Key=key)
        return obj["Body"].read().decode("utf-8")

    def ensure_local_dir(self, key_prefix: str) -> str:
        path = Path("/tmp/tif-outputs") / key_prefix
        path.mkdir(parents=True, exist_ok=True)
        return str(path)


class LocalStorageDriver(StorageDriver):
    """Driver de archivo local (útil para pruebas sin S3)."""

    def __init__(self, cfg: StorageConfig, root: str = "/tmp/tif-outputs") -> None:
        self.cfg = cfg
        self.root = root

    def _path(self, key: str) -> str:
        return os.path.join(self.root, key)

    def put_bytes(self, key: str, data: bytes, content_type: str, dry_run: bool) -> StoredObject:
        uri = f"file://{self._path(key)}"
        if dry_run:
            return StoredObject(key=key, uri=uri, bytes_written=len(data), written=False)
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return StoredObject(key=key, uri=uri, bytes_written=len(data), written=True)

    def public_url(self, key: str, ttl_minutes: int) -> str | None:
        return f"file://{self._path(key)}"

    def read_text(self, uri: str) -> str:
        path = uri[7:] if uri.startswith("file://") else uri
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    def ensure_local_dir(self, key_prefix: str) -> str:
        path = Path(self.root) / key_prefix
        path.mkdir(parents=True, exist_ok=True)
        return str(path)


def build_storage_driver(cfg: StorageConfig) -> StorageDriver:
    if cfg.driver == "s3":
        return S3StorageDriver(cfg)
    return LocalStorageDriver(cfg)


def build_base_path(template: str, production_id, scene_name: str) -> str:
    """Resuelve el base_path con placeholders del README."""
    return template.format(production_id=production_id, scene_name=scene_name).strip("/")
