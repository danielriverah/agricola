class AgroSentinelError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 500):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


class ConfigValidationError(AgroSentinelError):
    def __init__(self, missing_fields: list[str]):
        super().__init__(
            code="CONFIG_VALIDATION_ERROR",
            message=f"Missing required config fields: {missing_fields}",
            http_status=503,
        )
        self.missing_fields = missing_fields


class AIResponseInvalidError(AgroSentinelError):
    def __init__(self):
        super().__init__(
            code="AI_RESPONSE_INVALID",
            message="AI provider returned an invalid response after retries",
            http_status=502,
        )


class AIProviderUnavailableError(AgroSentinelError):
    def __init__(self):
        super().__init__(
            code="AI_PROVIDER_UNAVAILABLE",
            message="Both primary and fallback AI providers failed",
            http_status=503,
        )


class InsufficientHistoryWarning(Exception):
    code = "INSUFFICIENT_HISTORY"
