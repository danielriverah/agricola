"""
Lectura del item app_config desde DynamoDB.

DynamoDB se usa EXCLUSIVAMENTE para leer configuración runtime. Este módulo
nunca escribe en DynamoDB (cumple la regla del README).

Si boto3 no está disponible o la conexión falla, devolvemos None y dejamos que
la capa de configuración entre en modo degradado en vez de romper.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.env_settings import EnvSettings

logger = logging.getLogger("tif.dynamo")


class DynamoConfigReader:
    def __init__(self, env: EnvSettings) -> None:
        self.env = env

    def _build_resource(self):
        import boto3  # import perezoso: no romper si falta en algún entorno

        kwargs: dict[str, Any] = {"region_name": self.env.aws_region}

        if not self.env.dynamodb_use_aws and self.env.dynamodb_endpoint_url:
            kwargs["endpoint_url"] = self.env.dynamodb_endpoint_url
        elif self.env.dynamodb_endpoint_url:
            # Permitir endpoint explícito incluso con use_aws (p.ej. VPC endpoint)
            kwargs["endpoint_url"] = self.env.dynamodb_endpoint_url

        if self.env.aws_access_key_id and self.env.aws_secret_access_key:
            kwargs["aws_access_key_id"] = self.env.aws_access_key_id
            kwargs["aws_secret_access_key"] = self.env.aws_secret_access_key
            if self.env.aws_session_token:
                kwargs["aws_session_token"] = self.env.aws_session_token

        return boto3.resource("dynamodb", **kwargs)

    def read_item(self) -> dict | None:
        """Devuelve el item app_config crudo, o None si no se puede leer."""
        try:
            resource = self._build_resource()
            table = resource.Table(self.env.app_config_table_name)
            key = {self.env.app_config_item_pk: self.env.app_config_item_id}
            resp = table.get_item(Key=key)
            item = resp.get("Item")
            if item is None:
                logger.warning(
                    "app_config no encontrado en tabla=%s key=%s",
                    self.env.app_config_table_name,
                    key,
                )
            return item
        except Exception as exc:  # noqa: BLE001 - degradación intencional
            logger.warning("No se pudo leer app_config desde DynamoDB: %s", exc)
            return None
