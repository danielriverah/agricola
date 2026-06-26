# Microservicio Clima

Microservicio encargado de consultar clima histórico y pronóstico, persistirlo en MySQL y exponer endpoints para monitoreo de producciones.

## Base URL

```text
http://localhost:8004
```

## Variables de entorno principales

```env
APP_ENV=local
APP_BUILD_TAG=dev-local
APP_PORT=8004

AWS_REGION=us-east-1
DYNAMO_TABLE_PRODUCCIONES=producciones_monitoreo

MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_DB=agro_sentinel
MYSQL_USER=root
MYSQL_PASSWORD=tu_password

OPENMETEO_TIMEZONE=America/Mexico_City

SCHEDULER_ENABLED=false
SCHEDULER_TIME=06:00
SCHEDULER_TIMEZONE=America/Mexico_City

LOG_LEVEL=INFO
```

## Formato general

- `GET`: no requiere body.
- `POST`: en este microservicio normalmente no usa body, salvo que el endpoint indique lo contrario.
- Todas las fechas se manejan en formato `YYYY-MM-DD`.
- Las fechas y datetimes de salida se serializan como texto ISO.

---

## Endpoints

## 1) Health

### `GET /health`

Verifica que el servicio esté vivo y devuelve configuración del scheduler.

### Request

Sin body.

### Response

```json
{
  "status": "ok",
  "service": "agro-sentinel-clima",
  "version": "0.1.0",
  "build": "dev-local",
  "uptime_seconds": 48,
  "scheduler": {
    "enabled": true,
    "time": "10:17",
    "timezone": "America/Mexico_City"
  }
}
```

---

## 2) Producciones activas

### `GET /producciones/activas`

Devuelve producciones monitoreadas que están:

- activas en DynamoDB (`OPEN`)
- dentro de su ventana de monitoreo (`fecha_siembra + dias_max_monitoreo`)

### Request

Sin body.

### Response

```json
{
  "total": 1,
  "producciones": [
    {
      "produccion_id": 1891,
      "estatus": "OPEN",
      "fecha_siembra": "2026-01-27",
      "dias_max_monitoreo": 95,
      "dias_transcurridos_desde_siembra": 127,
      "ultima_fecha_confirmada_mysql": "2026-06-02",
      "fecha_inicio_historico": "2026-06-03",
      "fecha_fin_historico": "2026-06-02",
      "motivo_inicio_historico": "con_registros_usando_ultima_confirmada",
      "lat": 21.122813888889,
      "lon": -100.89053055556,
      "poligono": "21.122813888889,-100.89053055556|21.122813888889,-100.88798333333|21.126355555556,-100.88798333333|21.126355555556,-100.89053055556",
      "tiene_poligono": true
    }
  ]
}
```

### Campos relevantes

- `fecha_inicio_historico`: inicio sugerido del histórico climático.
- `fecha_fin_historico`: última fecha confirmada en MySQL, si existe.
- `motivo_inicio_historico`:
  - `con_registros_usando_ultima_confirmada`
  - `sin_registros_usando_fecha_siembra`

---

## 3) Base cruda MySQL

### `GET /producciones/mysql`

Devuelve las producciones monitoreadas directamente desde MySQL, sin enriquecer con DynamoDB.

### Request

Sin body.

### Response

```json
{
  "total": 2,
  "producciones": [
    {
      "produccion_id": 1891,
      "estatus": "OPEN",
      "monitoring": 1,
      "fecha": "2026-01-27",
      "poligono": "21.122813888889,-100.89053055556|...",
      "area_asig": 3.52
    }
  ]
}
```

### `GET /producciones/mysql/{produccion_id}`

Devuelve una producción específica desde la base cruda de MySQL.

### Request

Sin body.

### Response

```json
{
  "produccion_id": 1891,
  "item": {
    "produccion_id": 1891,
    "estatus": "OPEN",
    "monitoring": 1,
    "fecha": "2026-01-27",
    "poligono": "21.122813888889,-100.89053055556|...",
    "area_asig": 3.52
  }
}
```

### Error

```json
{
  "detail": "Producción 1891 no encontrada en MySQL."
}
```

---

## 4) Base cruda DynamoDB

### `GET /producciones/dynamo`

Devuelve los registros crudos de DynamoDB asociados a las producciones monitoreadas.

### Request

Sin body.

### Response

```json
{
  "total": 2,
  "producciones": [
    {
      "produccion_id": 1891,
      "folio": "TL2601-04",
      "dias_max_monitoreo": 95,
      "estatus": "OPEN",
      "fecha_siembra": "2026-01-27",
      "ultima_fecha_consultada": "2026-06-02",
      "pbox": {
        "max_lat": 21.126355555556,
        "max_lon": -100.88798333333,
        "min_lat": 21.122813888889,
        "min_lon": -100.89053055556
      }
    }
  ]
}
```

### `GET /producciones/dynamo/{produccion_id}`

Devuelve una producción específica desde DynamoDB.

### Request

Sin body.

### Response

```json
{
  "produccion_id": 1891,
  "item": {
    "produccion_id": 1891,
    "folio": "TL2601-04",
    "dias_max_monitoreo": 95,
    "estatus": "OPEN",
    "fecha_siembra": "2026-01-27"
  }
}
```

### Error

```json
{
  "detail": "Producción 1891 no encontrada en DynamoDB."
}
```

---

## 5) Monitoreo paginado con historial climático

### `GET /producciones/monitoreo`

Devuelve producciones monitoreadas con un resumen y un historial reciente de clima para gráficas.

### Query params

- `page`:
  - entero
  - default: `1`
  - mínimo: `1`
- `page_size`:
  - entero
  - default: `10`
  - mínimo: `1`
  - máximo: `100`
- `limit_historial`:
  - entero
  - default: `20`
  - mínimo: `1`
  - máximo: `200`

### Request

Sin body.

### Ejemplo

```text
/producciones/monitoreo?page=1&page_size=5&limit_historial=14
```

### Response

```json
{
  "total": 16,
  "page": 1,
  "page_size": 5,
  "total_pages": 4,
  "has_prev": false,
  "has_next": true,
  "limit_historial": 14,
  "producciones": [
    {
      "produccion_id": 1891,
      "estatus": "OPEN",
      "monitoring": 1,
      "fecha": "2026-01-27",
      "poligono": "21.122813888889,-100.89053055556|...",
      "area_asig": 3.52,
      "total_registros_clima": 14,
      "temp_prom_min": 18.4,
      "temp_prom_max": 25.2,
      "temp_prom_ultima": 22.05,
      "fecha_ultimo_registro": "2026-06-02",
      "serie_temperatura": [
        {
          "fecha": "2026-06-02",
          "temp_max": 28.8,
          "temp_min": 15.3,
          "temp_prom": 22.05,
          "tipo_dato": "actual"
        }
      ],
      "historial_clima": [
        {
          "produccion_id": 1891,
          "fecha": "2026-06-02",
          "tipo_dato": "actual",
          "fuente": "forecast_api",
          "temp_max": 28.8,
          "temp_min": 15.3,
          "temp_prom": 22.05,
          "humedad_prom": null,
          "precipitacion_mm": 4.1,
          "lluvia_mm": null,
          "probabilidad_lluvia_max": 82,
          "viento_max_kmh": 20.1,
          "radiacion_solar_mj": 26.4,
          "evapotranspiracion_mm": 5.64,
          "riesgo_helada": 0,
          "riesgo_estres_hidrico": 0,
          "riesgo_lluvia": 1,
          "riesgo_helada_pct": 0.0,
          "riesgo_estres_hidrico_pct": 0.0,
          "riesgo_lluvia_pct": 82.0,
          "riesgo_viento_pct": 25.0,
          "riesgo_enfermedad_pct": 0.0,
          "riesgo_plaga_pct": 0.0,
          "recomendacion": null,
          "bloqueado": 0
        }
      ]
    }
  ]
}
```

---

## 6) Registros climáticos por producción

### `GET /registros/{produccion_id}`

Devuelve los registros de `produccion_clima_diario` para una producción específica.

### Path params

- `produccion_id`: entero

### Query params

- `tipo_dato`:
  - opcional
  - valores esperados:
    - `historico_confirmado`
    - `actual`
    - `forecast`
- `limit`:
  - entero
  - default: `30`
  - mínimo: `1`
  - máximo: `500`

### Request

Sin body.

### Ejemplo

```text
/registros/1891?tipo_dato=forecast&limit=10
```

### Response

```json
{
  "produccion_id": 1891,
  "total": 10,
  "registros": [
    {
      "produccion_clima_diario_id": 1,
      "produccion_id": 1891,
      "fecha": "2026-06-02",
      "tipo_dato": "actual",
      "fuente": "forecast_api",
      "fecha_consulta": "2026-06-03T10:17:00",
      "horizonte_dia": 0,
      "temp_max": 28.8,
      "temp_min": 15.3,
      "temp_prom": 22.05,
      "humedad_prom": null,
      "precipitacion_mm": 4.1,
      "lluvia_mm": null,
      "probabilidad_lluvia_max": 82,
      "viento_max_kmh": 20.1,
      "radiacion_solar_mj": 26.4,
      "evapotranspiracion_mm": 5.64,
      "riesgo_helada": 0,
      "riesgo_estres_hidrico": 0,
      "riesgo_lluvia": 1,
      "recomendacion": null,
      "bloqueado": 0,
      "created_at": "2026-06-03T10:17:05",
      "updated_at": "2026-06-03T10:17:05"
    }
  ]
}
```

---

## 7) Sincronización climática

### `POST /sync/all`

Ejecuta la sincronización climática para todas las producciones activas.

### Request

Sin body.

### Response

```json
{
  "ok": true,
  "procesadas": 2,
  "ok_count": 2,
  "error_count": 0,
  "duracion_seg": 18.4,
  "resultados": [
    {
      "produccion_id": 1891,
      "ok": true,
      "historico_dias": 0,
      "forecast_dias": 6,
      "filas_afectadas": 12,
      "error": null
    }
  ]
}
```

### `POST /sync/{produccion_id}`

Ejecuta la sincronización climática para una sola producción.

### Path params

- `produccion_id`: entero

### Request

Sin body.

### Response

```json
{
  "produccion_id": 1891,
  "ok": true,
  "historico_dias": 0,
  "forecast_dias": 6,
  "filas_afectadas": 12,
  "error": null
}
```

### Error típico

```json
{
  "produccion_id": 1891,
  "ok": false,
  "historico_dias": 0,
  "forecast_dias": 0,
  "filas_afectadas": 0,
  "error": "..."
}
```

---

## Notas de implementación

- El histórico se toma desde MySQL.
- Si no hay registros confirmados, el rango histórico arranca en `fecha_siembra`.
- Si el histórico ya está bloqueado hasta ayer, solo se actualiza forecast.
- El scheduler usa `SCHEDULER_TIME` y `SCHEDULER_TIMEZONE`.

## Ejemplos rápidos

```bash
curl http://localhost:8004/health
curl http://localhost:8004/producciones/activas
curl http://localhost:8004/producciones/mysql/1891
curl http://localhost:8004/producciones/dynamo/1891
curl "http://localhost:8004/producciones/monitoreo?page=1&page_size=5&limit_historial=14"
curl http://localhost:8004/registros/1891
curl -X POST http://localhost:8004/sync/1891
curl -X POST http://localhost:8004/sync/all
```

