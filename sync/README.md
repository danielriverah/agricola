# AgroSentinel Sync Microservice

Microservicio REST (FastAPI) que compara, sincroniza y valida datos de
monitoreo entre **DynamoDB**, **MySQL** y **S3**.

> **Política de seguridad de datos:** el servicio **nunca altera la estructura**
> de la base de datos (no emite `CREATE` / `ALTER` / `DROP` / `TRUNCATE` /
> `RENAME` — bloqueado a nivel de cliente MySQL). Sí crea y actualiza
> **registros** cuando `dry_run=false`. Con `dry_run=true` solo simula.

## Arquitectura

```
app/
├── main.py                 # FastAPI app, routers, lifespan (scheduler)
├── core/
│   ├── settings.py         # .env: SOLO conexion Dynamo + id de config
│   ├── errors.py           # sync_busy / not_found / validation_error
│   └── logging_config.py
├── clients/
│   ├── dynamo_client.py    # AWS o Dynamo local
│   ├── mysql_client.py     # PyMySQL + anti-DDL + batch
│   └── s3_client.py        # listado y lectura de JSON
├── services/
│   ├── config_service.py   # config centralizada leida de DynamoDB (cache)
│   ├── job_manager.py      # 1 sync pesada a la vez (lock) + cancelacion
│   ├── transformers.py     # mapeo Dynamo/S3 -> filas MySQL
│   ├── mysql_repo.py       # SELECT + upserts por lote (ON DUPLICATE KEY)
│   └── sync_service.py     # orquesta fases con dry_run real
├── routers/
│   ├── phases.py           # /productions/escenes|ia|sync/full (literales)
│   ├── productions.py      # /productions/{id}/...
│   ├── jobs_config.py      # /sync/jobs/*  y  /config/runtime/*
│   └── legacy.py           # aliases /sync/s3/phase1..3
└── scheduler/
    └── runner.py           # interval | daily | every_n_days
```

## Fuente de verdad por fase

| Fase | Fuente de verdad | Destino MySQL |
|------|------------------|---------------|
| Producciones | DynamoDB `production_monitoring` | `s3_monitoring_producciones` |
| Escenas | DynamoDB `production_monitoring_detalle` | `s3_monitoring_escenas` |
| Archivos (Fase 2) | S3 `previews/PROD_{id}/{scene}/...` | `s3_monitoring_escena_archivos` |
| IA (Fase 3) | S3 `previews/PROD_{id}/{scene}/multiband.ia.json` | `s3_monitoring_escena_ia_resumen` |
| Geometría / tile (Fase 4) | DynamoDB + cálculo derivado | `s3_monitoring_producciones` |

> El JSON de IA (`.../{scene}/multiband.ia.json`) se distingue del JSON de
> escena (`.../{fecha}_{scene}.json`) por la ruta y el sufijo `multiband.ia`.

## Configuración

El `.env` solo trae la **conexión a DynamoDB** y el **identificador del item
de config**. El resto (MySQL, S3, scheduler) vive en un item de DynamoDB y se
lee en runtime — no requiere reiniciar el contenedor.

1. Copia `.env.example` a `.env` y ajusta la conexión a Dynamo.
2. Crea el item de configuración en tu tabla `app_config` (ver ejemplo en el
   README original del proyecto).

```bash
docker compose up -d --build agro-syncronizador
```

- Swagger: http://localhost:8006/docs
- ReDoc:   http://localhost:8006/redoc

### Modo desarrollo (recarga automática)

Descomenta `volumes` y `command` en `docker-compose.yml`, o local:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8006 --reload
```

## Endpoints (resumen)

### Producciones
```
GET  /productions/{id}/dynamo | /mysql | /inserts
POST /productions/{id}/sync?dry_run=false
GET  /productions/inserts
```

### Escenas
```
GET  /productions/{id}/scenes/dynamo | /mysql | /inserts
POST /productions/{id}/scenes/sync?dry_run=false
POST /productions/{id}/scenes/{scene_name}/sync?dry_run=false
GET  /productions/escenes/dynamo | /mysql | /inserts
POST /productions/escenes/sync?dry_run=false
```

### Fase 2 — archivos
```
POST /productions/escenes/archivos/sync?dry_run=false
POST /productions/{id}/escenes/archivos/sync?dry_run=false
POST /productions/{id}/scenes/{scene_name}/archivos/sync?dry_run=false
GET  /productions/escenes/archivos/mysql | /s3
```

### Fase 3 — IA
```
POST /productions/ia/sync?dry_run=false
GET  /productions/ia/pending
GET  /productions/{id}/ia/pending
```

### Fase 4 — geometría / tile
```
GET  /productions/{id}/geometry
POST /productions/{id}/geometry/sync?dry_run=false
POST /productions/{id}/geometry/tile/sync?dry_run=false
GET  /productions/{id}/geometry/inserts
GET  /productions/geometry/inserts
POST /productions/geometry/sync?dry_run=false
```
La fase 4 no calcula el tile final; solo expone lo persistido y lo pendiente
para que otro microservicio calcule y consuma la geometría final.
En `dry_run=true` el endpoint devuelve el payload exacto que escribiría.
El payload incluye `polygon_bbox` y `tile_bbox` con el formato completo:
`min_lat`, `max_lat`, `min_lon`, `max_lon`, `puntos_bbox` y `pbox`.

### Maestra / Jobs / Config
```
POST /productions/sync/full?dry_run=false
GET  /sync/jobs | /sync/jobs/active | /sync/jobs/{job_id}
POST /sync/jobs/cancel/{job_id}
GET  /config/runtime?force_refresh=true
POST /config/runtime/refresh
```
`GET /sync/jobs/{job_id}` incluye `progress` y `progress_note`.
La Fase 5 devuelve un `plan` previo con `pending` y `skipped`, incluyendo
`reasons` como `tile_center_lat_changed` o `pbox_missing`.

### Legacy (aliases temporales)
```
POST /sync/productions | /sync/scenes
POST /sync/s3/phase1 | /phase2 | /phase3 | /full
```

## Reglas de sincronización

- Una sola sincronización pesada activa a la vez. Si ya hay una corriendo →
  `409 sync_busy`.
- Sin nada pendiente → `already_synced`.
- Las `GET` nunca escriben.
- Orden de fases: metadata → archivos → IA.
- Inserts/updates por lote (`executemany`, `ON DUPLICATE KEY UPDATE`).

## Códigos de error

| Código | HTTP | Significado |
|--------|------|-------------|
| `sync_busy` | 409 | Ya hay una sync pesada activa |
| `already_synced` | 200 | Nada pendiente por sincronizar |
| `not_found` | 404 | Recurso inexistente |
| `validation_error` | 422 | Parámetros inválidos / config faltante |

## Scheduler

Lee sus banderas del item de config en DynamoDB (`SYNC_SCHEDULER_*`). Modos:
`interval`, `daily`, `every_n_days`. Cada corrida queda registrada en
`monitoring_daemon_logs`. Tras editar el item:

```bash
curl -X POST "http://localhost:8006/config/runtime/refresh"
```
