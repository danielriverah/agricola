import os
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

APP_ENV: str = os.getenv("APP_ENV", "local")
AWS_REGION: str = os.getenv("AWS_REGION", "us-east-1")
CONFIG_TABLE_NAME: str = os.getenv("CONFIG_TABLE_NAME", "agro_sentinel_config")
CONFIG_PARTITION_KEY: str = os.getenv("CONFIG_PARTITION_KEY", "local")
CONFIG_SORT_KEY: str = os.getenv("CONFIG_SORT_KEY", "active")
CONFIG_CACHE_TTL_SECONDS: int = int(os.getenv("CONFIG_CACHE_TTL_SECONDS", "0"))
CONFIG_FAIL_FAST: bool = os.getenv("CONFIG_FAIL_FAST", "true").lower() == "true"
DYNAMODB_ENDPOINT_URL: str | None = os.getenv("DYNAMODB_ENDPOINT_URL")
BEDROCK_MODEL_ID: str = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20240620-v1:0")
BEDROCK_AUTH_MODE: Literal["iam", "api_key"] = os.getenv("BEDROCK_AUTH_MODE", "iam").lower()  # type: ignore[assignment]
BEDROCK_API_KEY: str | None = os.getenv("BEDROCK_API_KEY") or os.getenv("AWS_BEARER_TOKEN_BEDROCK")
AWS_ACCESS_KEY_ID_CUSTOM: str | None = os.getenv("AWS_ACCESS_KEY_ID_CUSTOM")
AWS_SECRET_ACCESS_KEY_CUSTOM: str | None = os.getenv("AWS_SECRET_ACCESS_KEY_CUSTOM")
AWS_SESSION_TOKEN_CUSTOM: str | None = os.getenv("AWS_SESSION_TOKEN_CUSTOM")
