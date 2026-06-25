# AgroSentinel TIF — Microservicio (implementación)

Implementación del microservicio TIF según el contrato funcional del repositorio.
Servicio REST de **solo lectura** hacia bases de datos: genera `multiband.tif` y
derivados a partir de escenas indexadas en MySQL, usando DynamoDB únicamente para
configuración runtime.

## Garantías de contrato (verificadas)

- **No escribe en ninguna base de datos.** Toda consulta MySQL pasa por un guard
  (`app/db/readonly_guard.py`) que rechaza `INSERT/UPDATE/DELETE/CREATE/ALTER/
  DROP/TRUNCATE/REPLACE/...`, multi-sentencia e inyección por comentarios.
  Probado: 12/12 escrituras bloqueadas, 5/5 lecturas permitidas.
- **DynamoDB solo lee** el item `app_config`. Nunca escribe en DynamoDB.
- **Arranque degradado.** Sin configuración, la app arranca igual y reporta el
  estado en `/health`, `/config/view` e `/internal/config/validate`. No rompe Swagger.
- **Sin scheduler/cron/daemon.** Los jobs son efímeros y solo se disparan por POST.
- **Rutas legacy de escritura** (`/admin/reset`, `/bootstrap-sql`,
  `/monitoring/daemon`, `/monitoring/enqueue`) responden `403 write_disabled`.
- Generar archivos en S3 devuelve rutas/resumen; **no indexa en MySQL**.
- `dry_run=true` no escribe en S3.

## Cómo correr

### Desarrollo
```bash
pip install -r requirements.txt
cp .env.example .env          # ajusta solo la conexión a DynamoDB
uvicorn app.main:app --host 0.0.0.0 --port 5300 --reload
```

### Producción
```bash
uvicorn app.main:app --host 0.0.0.0 --port 5300
```

### Docker
```bash
docker compose up --build
```
Entre contenedores usa el nombre interno: `http://agro-tif:5300` (no `localhost`).

Swagger: `http://localhost:5300/docs` · OpenAPI: `/openapi.json`

## `.env` (mínimo)

Solo conexión a DynamoDB y localización del item:
`APP_CONFIG_TABLE_NAME`, `APP_CONFIG_ITEM_ID` (default `microservicio-tif`),
`APP_CONFIG_ITEM_PK`, `AWS_REGION`, `DYNAMODB_ENDPOINT_URL`, `DYNAMODB_USE_AWS`,
`AWS_*_CUSTOM`, `CONFIG_CACHE_TTL_SECONDS`, `CONFIG_FAIL_FAST`.
**No** poner aquí MySQL, S3, Earth Search ni parámetros de procesamiento.

## `app_config` en DynamoDB (campos esperados)

`enabled`, `timezone`, `request_timeout_seconds`,
`mysql.{host,port,database,user,password,strict_mode}`,
`storage.{driver,s3_bucket,base_path,public_url_ttl_minutes}`,
`earth_search.{search_url,collection,max_cloud_coverage,request_timeout_seconds,band_resolution_meters,asset_map}`,
`processing.{default_indices,resolution_meters,apply_cloud_mask,max_production_cloud,min_valid_pixels_percentage,generate_png,generate_geotiff,generate_pdf}`,
`outputs.{multiband_filename,params_filename,temp_*}`,
`targets` (no dispara IA), `security.api_secret_key`.

Si falta config crítica (p.ej. `mysql.host`), los endpoints que la requieren
responden `503` indicando la clave faltante; el resto del servicio sigue vivo.

## Estructura

```
app/
  config/     env mínimo, modelo app_config, lector DynamoDB, provider+cache, validador
  core/       errores (503/403/422) y dependencias (require_mysql/storage/earth_search)
  db/         cliente MySQL solo-lectura, guard SQL, repositorios (SELECT)
  storage/    drivers S3 / local, dry_run
  processing/ índices espectrales, cliente Earth Search/STAC, builder raster + production_cloud
  jobs/       gestor de jobs bajo demanda (sin scheduler)
  services/   ensamblado de capas
  routers/    health, monitoring, tif, jobs, ia_handoff, admin
  main.py     app FastAPI + lifespan degradado
```

## Notas de validación

Probado en proceso (TestClient): `/docs` y `/openapi.json` cargan (51 rutas en
schema, 61 totales), arranque degradado sin DynamoDB, 503 por capa faltante,
403 en legacy de escritura, rutas de jobs normalizadas en `/jobs*`, y
guard SQL. **No** se probó contra MySQL/Earth Search/S3 reales: esas rutas están
escritas a especificación pero requieren tus credenciales para validación end-to-end.
```
