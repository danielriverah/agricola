from app.core.exceptions import ConfigValidationError

_REQUIRED_FIELDS = [
    ("security", "api_secret_key"),
    ("storage", "driver"),
    ("storage", "s3_bucket"),
    ("storage", "base_path"),
    ("ai", "provider"),
    ("agronomic_rules", "ndvi_drop_alert_pct"),
    ("agronomic_rules", "ndmi_drop_alert_pct"),
    ("laravel", "webhook_url"),
    ("laravel", "webhook_secret"),
]


def validate_ia_config(cfg: dict) -> list[str]:
    missing = []
    for section, field in _REQUIRED_FIELDS:
        if not cfg.get(section, {}).get(field):
            missing.append(f"{section}.{field}")

    provider = cfg.get("ai", {}).get("provider")
    if provider:
        provider_cfg = cfg.get("ai", {}).get("providers", {}).get(provider, {})
        if not provider_cfg.get("enabled"):
            missing.append(f"ai.providers.{provider}.enabled")
        if not provider_cfg.get("model") and provider != "ollama":
            missing.append(f"ai.providers.{provider}.model")

    return missing


def assert_valid(cfg: dict) -> None:
    missing = validate_ia_config(cfg)
    if missing:
        raise ConfigValidationError(missing)
