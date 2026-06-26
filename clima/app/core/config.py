import os
from dotenv import load_dotenv

load_dotenv()

APP_ENV: str = os.getenv("APP_ENV", "local")
APP_BUILD_TAG: str = os.getenv("APP_BUILD_TAG", "dev-local")
APP_PORT: int = int(os.getenv("APP_PORT", "8004"))

AWS_REGION: str = os.getenv("AWS_REGION", "us-east-1")
DYNAMO_TABLE_PRODUCCIONES: str = os.environ["DYNAMO_TABLE_PRODUCCIONES"]

MYSQL_HOST: str = os.environ["MYSQL_HOST"]
MYSQL_PORT: int = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DB: str = os.environ["MYSQL_DB"]
MYSQL_USER: str = os.environ["MYSQL_USER"]
MYSQL_PASSWORD: str = os.environ["MYSQL_PASSWORD"]

OPENMETEO_TIMEZONE: str = os.getenv("OPENMETEO_TIMEZONE", "America/Mexico_City")
OPENMETEO_FORECAST_URL: str = "https://api.open-meteo.com/v1/forecast"
OPENMETEO_ARCHIVE_URL: str = "https://archive-api.open-meteo.com/v1/archive"

# Programador diario integrado
SCHEDULER_ENABLED: bool = os.getenv("SCHEDULER_ENABLED", "false").lower() == "true"
# Hora de ejecución automática en formato "HH:MM" (hora local del servidor)
SCHEDULER_TIME: str = os.getenv("SCHEDULER_TIME", "06:00")
# Zona horaria usada por el scheduler, independiente de la del servidor
SCHEDULER_TIMEZONE: str = os.getenv("SCHEDULER_TIMEZONE", OPENMETEO_TIMEZONE)

LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
