# Guía de setup — microservicio-clima

## Requisitos previos

- Python 3.11+
- Acceso a AWS (DynamoDB + Lambda)
- MySQL accesible desde Lambda (RDS en la misma VPC o con endpoint público)
- Tabla `produccion_clima_diario` creada (ver DDL en `docs/`)

---

## 1. Instalación local

```bash
cd microservicio-clima
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

pip install -r requirements.txt
```

---

## 2. Variables de entorno locales

Crea un archivo `.env` (no subir a git):

```env
DYNAMO_TABLE_PRODUCCIONES=producciones_monitoreo
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...

MYSQL_HOST=tu-rds-endpoint.rds.amazonaws.com
MYSQL_PORT=3306
MYSQL_DB=agro_sentinel
MYSQL_USER=admin
MYSQL_PASSWORD=...

OPENMETEO_TIMEZONE=America/Mexico_City
LOG_LEVEL=DEBUG
```

---

## 3. Ejecución local (simulando Lambda)

```bash
python -c "
import json
from app.main import lambda_handler
result = lambda_handler({}, None)
print(json.dumps(result, indent=2, default=str))
"
```

---

## 4. Empaquetado para Lambda

### Opción A — ZIP directo

```bash
pip install -r requirements.txt -t package/
cp -r app/ package/app/
cd package
zip -r ../lambda_clima.zip .
cd ..
```

### Opción B — Docker (recomendado para dependencias nativas)

```bash
docker build -t microservicio-clima .
docker run --rm \
  -e DYNAMO_TABLE_PRODUCCIONES=... \
  -e MYSQL_HOST=... \
  microservicio-clima
```

---

## 5. Configuración Lambda en AWS

| Parámetro | Valor |
|---|---|
| Runtime | Python 3.11 |
| Handler | `app.main.lambda_handler` |
| Timeout | 300 s (5 min) — puede ajustarse según volumen |
| Memory | 256 MB |
| VPC | Misma VPC que el RDS (si aplica) |

### Variables de entorno en Lambda

Agregar todas las del `.env` en la sección *Configuration → Environment variables*.

### Trigger EventBridge

```
Nombre: clima-diario-trigger
Schedule: cron(0 12 * * ? *)   # 06:00 CST
Target: ARN de esta función Lambda
```

---

## 6. DynamoDB — estructura esperada

La función hace un `scan` con `FilterExpression: estatus = OPEN`.

Campos mínimos requeridos por ítem:

| Campo DynamoDB | Tipo | Descripción |
|---|---|---|
| `produccion_id` | N | ID de la producción en MySQL |
| `estatus` | S | `OPEN` / `CLOSED` |
| `fecha_siembra` | S | `YYYY-MM-DD` |
| `dias_max_monitoreo` | N | Días máximos desde siembra |
| `ultima_fecha_consultada` | S | Última fecha procesada (se actualiza automáticamente) |

---

## 7. MySQL — permisos requeridos

```sql
GRANT SELECT ON agro_sentinel.producciones TO 'usuario_lambda'@'%';
GRANT SELECT ON agro_sentinel.asignaciones_zonas_producciones TO 'usuario_lambda'@'%';
GRANT SELECT, INSERT, UPDATE ON agro_sentinel.produccion_clima_diario TO 'usuario_lambda'@'%';
```
