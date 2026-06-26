import time
from typing import Any

import boto3

from app.core import config as env


_cache: dict[str, Any] = {}
_cache_ts: float = 0.0


def get_config() -> dict[str, Any]:
    global _cache, _cache_ts
    now = time.time()
    if _cache and (now - _cache_ts) < env.CONFIG_CACHE_TTL_SECONDS:
        return _cache

    kwargs: dict[str, Any] = {"region_name": env.AWS_REGION}
    if env.DYNAMODB_ENDPOINT_URL:
        kwargs["endpoint_url"] = env.DYNAMODB_ENDPOINT_URL

    client = boto3.client("dynamodb", **kwargs)
    response = client.get_item(
        TableName=env.CONFIG_TABLE_NAME,
        Key={
            "pk": {"S": env.CONFIG_PARTITION_KEY},
            "sk": {"S": env.CONFIG_SORT_KEY},
        },
    )
    item = response.get("Item", {})
    parsed = _deserialize(item)
    _cache = parsed
    _cache_ts = now
    return parsed


def invalidate_cache() -> None:
    global _cache, _cache_ts
    _cache = {}
    _cache_ts = 0.0


def _deserialize(item: dict) -> dict:
    from boto3.dynamodb.types import TypeDeserializer
    d = TypeDeserializer()
    return {k: d.deserialize(v) for k, v in item.items()}
