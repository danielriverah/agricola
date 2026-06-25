# AgroSentinel Sync Microservice

Microservicio REST para comparar, sincronizar y validar datos de monitoreo entre DynamoDB, MySQL y S3.

Este servicio se encarga de:

- sincronizar producciones
- sincronizar escenas
- indexar metadata de escenas en MySQL
- indexar archivos derivados de escenas
- indexar resumenes IA
- exponer endpoints de diagnostico y `dry_run`
- ejecutar sincronizaciones automáticas por schedule

## Tecnologia

Este microservicio esta construido con:

- Python 3.11+
- FastAPI
- Uvicorn
- PyMySQL para MySQL
- Boto3 para DynamoDB y S3
- Docker y Docker Compose

## Despliegue

### Local

1. Configura el archivo `.env`.
2. Levanta el contenedor con Docker Compose.
3. Abre Swagger para validar rutas y payloads.

```bash
docker compose up -d --build agro-syncronizador
```

Swagger:

- `http://localhost:8006/docs`
- `http://localhost:8006/redoc`
- `http://localhost:8006/openapi.json`

### Produccion

1. Construir la imagen del microservicio.
2. Inyectar variables de entorno de AWS, MySQL y scheduler.
3. Exponer el puerto del servicio.
4. Monitorear logs y jobs activos.

## Principios del servicio

1. Una sola sincronizacion pesada activa a la vez.
2. Las consultas de lectura deben responder directo.
3. Las sincronizaciones pesadas deben encolarse o bloquearse si ya hay una corriendo.
4. `dry_run=true` solo simula.
5. La fuente de verdad depende de la fase:
   - DynamoDB para producciones, escenas base y referencias de IA
   - S3 para archivos derivados y artefactos de IA
   - MySQL para lo ya indexado y persistido
6. Los inserts/updates reales deben hacerse por lotes.

## Fuente de verdad por fase

- Producciones: DynamoDB para el estado base y MySQL para lo ya indexado.
- Escenas: DynamoDB para la fuente de verdad de sincronizacion.
- IA en producciones y escenas: DynamoDB para decidir que procesar, S3 para archivos y resumenes generados.
- Archivos derivados: S3 como fuente de verdad.

## Rutas canonicas recomendadas

Si el microservicio se construye desde cero, estas son las URLs normalizadas sugeridas.

### Producciones

- `GET /productions/{production_id}/dynamo`
- `GET /productions/{production_id}/mysql`
- `POST /productions/{production_id}/sync`
- `GET /productions/inserts`
- `GET /productions/{production_id}/inserts`

### Escenas de una produccion

- `GET /productions/{production_id}/scenes/dynamo`
- `GET /productions/{production_id}/scenes/mysql`
- `POST /productions/{production_id}/scenes/sync`
- `POST /productions/{production_id}/scenes/{scene_name}/sync`
- `GET /productions/{production_id}/scenes/inserts`

### Fase 1: escenas

- `GET /productions/escenes/dynamo`
- `GET /productions/escenes/mysql`
- `GET /productions/escenes/inserts`
- `POST /productions/escenes/sync`

### Fase 2: archivos

- `POST /productions/escenes/archivos/sync`
- `POST /productions/{production_id}/escenes/archivos/sync`
- `POST /productions/{production_id}/scenes/archivos/sync`
- `POST /productions/{production_id}/scenes/{scene_name}/archivos/sync`
- `GET /productions/escenes/archivos/mysql`
- `GET /productions/escenes/archivos/s3`
- `GET /productions/{production_id}/escenes/archivos/mysql`
- `GET /productions/{production_id}/escenes/archivos/s3`

### Fase 3: analisis IA

- `POST /productions/ia/sync`
- `GET /productions/ia/pending`
- `GET /productions/{production_id}/ia/pending`

### Ejecucion maestra

- `POST /productions/sync/full`

### Jobs y estado

- `GET /sync/jobs`
- `GET /sync/jobs/active`
- `GET /sync/jobs/{job_id}`
- `POST /sync/jobs/cancel/{job_id}`

### Configuracion runtime

- `GET /config/runtime`
- `GET /config/runtime?force_refresh=true`
- `POST /config/runtime/refresh`

## Compatibilidad con rutas legacy

Si el proyecto ya existe y aun conserva rutas antiguas, puedes mantenerlas como alias temporales mientras migras al esquema canonico.

- `/sync/productions`
- `/sync/scenes`
- `/sync/s3/phase1`
- `/sync/s3/phase2`
- `/sync/s3/phase3`
- `/sync/s3/full`

## Flujo recomendado

### Desde cero

1. Sincronizar producciones.
2. Sincronizar escenas.
3. Indexar metadata de escena.
4. Indexar archivos derivados.
5. Indexar IA.
6. Ejecutar schedule automatico si aplica.

### Reglas de sincronizacion

- Si ya existe un trabajo activo, rechazar nuevas ejecuciones con `409 sync_busy`.
- Si una sincronizacion no tiene nada pendiente, responder `already_synced`.
- Las consultas `GET` deben evitar generar escrituras.
- Las fases deben procesarse en orden:
  1. metadata
  2. archivos
  3. IA

## Variables de entorno

### Conexión a DynamoDB

Estas variables son las mínimas que deben estar definidas para que el servicio pueda conectarse a DynamoDB. Esta conexión puede apuntar a AWS o a un Dynamo local.

- `AWS_REGION`
- `DYNAMODB_USE_AWS`
- `DYNAMODB_ENDPOINT_URL`
- `AWS_ACCESS_KEY_ID_CUSTOM`
- `AWS_SECRET_ACCESS_KEY_CUSTOM`
- `AWS_SESSION_TOKEN_CUSTOM`
- `PRODUCTION_MONITORING_TABLE_NAME`
- `PRODUCTION_MONITORING_DETAIL_TABLE_NAME`

#### Cuándo usar cada una

- `DYNAMODB_USE_AWS=true`:
  - usa DynamoDB real en AWS
  - `DYNAMODB_ENDPOINT_URL` puede omitirse
- `DYNAMODB_USE_AWS=false`:
  - usa DynamoDB local o un endpoint alterno
  - `DYNAMODB_ENDPOINT_URL` debe apuntar al servicio correspondiente

#### Identificadores de configuración en DynamoDB

El microservicio toma estos nombres como identificadores de configuración:

- `PRODUCTION_MONITORING_TABLE_NAME`: tabla base de producciones
- `PRODUCTION_MONITORING_DETAIL_TABLE_NAME`: tabla base de detalle de escenas

Si cambian los nombres de las tablas en DynamoDB, estas variables deben actualizarse.

### Configuracion centralizada en DynamoDB

El microservicio puede leer su configuracion operativa desde una tabla de DynamoDB y un registro especifico. La idea es que el contenedor no tenga que reiniciarse cada vez que cambie un valor operativo.

Variables sugeridas para ese esquema:

- `APP_CONFIG_TABLE_NAME`
- `APP_CONFIG_ITEM_ID`
- `APP_CONFIG_ITEM_PK`
- `APP_CONFIG_ITEM_SK` opcional y reservado para extensiones futuras

Valores que deberian vivir dentro de ese registro de configuracion:

- `SYNC_SCHEDULER_ENABLED`
- `SYNC_SCHEDULER_MODE`
- `SYNC_SCHEDULER_TIME`
- `SYNC_SCHEDULER_TIMEZONE`
- `SYNC_SCHEDULER_INTERVAL_SECONDS`
- `SYNC_SCHEDULER_EVERY_N_DAYS`
- `SYNC_SCHEDULER_DRY_RUN`
- `SYNC_SCHEDULER_ACTIVE_ONLY`
- `SYNC_SCHEDULER_BATCH_SIZE`
- `SYNC_SCHEDULER_INCLUDE_SCENE_JSON`
- `SYNC_SCHEDULER_DATE_FROM`
- `SYNC_SCHEDULER_DATE_TO`
- `SYNC_SCHEDULER_SERVICE_NAME`
- `MYSQL_HOST`
- `MYSQL_PORT`
- `MYSQL_DATABASE`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_TARGET_TABLE`
- `MYSQL_SCENES_TABLE`
- `MYSQL_SCENE_FILES_TABLE`
- `MONITORING_S3_BUCKET`
- `MONITORING_S3_PREFIX`
- `MONITORING_S3_PHASE2_FILES`
- `SYNC_PREFIX_TEMPLATE`

Con ese enfoque:

- el `.env` solo deja la conexion a DynamoDB y el identificador del registro de configuracion
- el registro de configuracion guarda los parametros operativos
- los cambios de operacion no requieren `uvicorn --reload`
- el endpoint `GET /config/runtime` permite ver lo que el servicio esta leyendo
- el endpoint `POST /config/runtime/refresh` fuerza un refresco inmediato

#### Ejemplo de item de configuracion

```json
{
  "config_id": "microservicio-sync",
  "config_type": "runtime",
  "SYNC_SCHEDULER_ENABLED": true,
  "SYNC_SCHEDULER_MODE": "daily",
  "SYNC_SCHEDULER_TIME": "02:00",
  "SYNC_SCHEDULER_TIMEZONE": "America/Mexico_City",
  "SYNC_SCHEDULER_INTERVAL_SECONDS": 3600,
  "SYNC_SCHEDULER_EVERY_N_DAYS": 1,
  "SYNC_SCHEDULER_DRY_RUN": false,
  "SYNC_SCHEDULER_ACTIVE_ONLY": false,
  "SYNC_SCHEDULER_BATCH_SIZE": 20,
  "SYNC_SCHEDULER_INCLUDE_SCENE_JSON": false,
  "SYNC_SCHEDULER_DATE_FROM": null,
  "SYNC_SCHEDULER_DATE_TO": null,
  "SYNC_SCHEDULER_SERVICE_NAME": "schedule_micro_serv_sync",
  "MYSQL_HOST": "mysql-host",
  "MYSQL_PORT": 3306,
  "MYSQL_DATABASE": "agro_sentinel",
  "MYSQL_USER": "app_user",
  "MYSQL_PASSWORD": "secret",
  "MYSQL_TARGET_TABLE": "s3_monitoring_producciones",
  "MYSQL_SCENES_TABLE": "s3_monitoring_escenas",
  "MYSQL_SCENE_FILES_TABLE": "s3_monitoring_escena_archivos"
}
```

#### Campos que conviene leer desde ese item

- `SYNC_SCHEDULER_ENABLED`
- `SYNC_SCHEDULER_MODE`
- `SYNC_SCHEDULER_TIME`
- `SYNC_SCHEDULER_TIMEZONE`
- `SYNC_SCHEDULER_INTERVAL_SECONDS`
- `SYNC_SCHEDULER_EVERY_N_DAYS`
- `SYNC_SCHEDULER_DRY_RUN`
- `SYNC_SCHEDULER_ACTIVE_ONLY`
- `SYNC_SCHEDULER_BATCH_SIZE`
- `SYNC_SCHEDULER_INCLUDE_SCENE_JSON`
- `SYNC_SCHEDULER_DATE_FROM`
- `SYNC_SCHEDULER_DATE_TO`
- `SYNC_SCHEDULER_SERVICE_NAME`
- `MYSQL_HOST`
- `MYSQL_PORT`
- `MYSQL_DATABASE`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_TARGET_TABLE`
- `MYSQL_SCENES_TABLE`
- `MYSQL_SCENE_FILES_TABLE`

### AWS / DynamoDB / S3

- `AWS_REGION`
- `AWS_ACCESS_KEY_ID_CUSTOM`
- `AWS_SECRET_ACCESS_KEY_CUSTOM`
- `AWS_SESSION_TOKEN_CUSTOM`
- `DYNAMODB_ENDPOINT_URL`
- `DYNAMODB_USE_AWS`
- `PRODUCTION_MONITORING_TABLE_NAME`
- `PRODUCTION_MONITORING_DETAIL_TABLE_NAME`
- `APP_CONFIG_TABLE_NAME`
- `APP_CONFIG_ITEM_ID`
- `APP_CONFIG_ITEM_PK`
- `APP_CONFIG_ITEM_SK`
- `MONITORING_S3_BUCKET`
- `MONITORING_S3_PREFIX`
- `MONITORING_S3_PHASE2_FILES`
- `SYNC_PREFIX_TEMPLATE`

### MySQL

Estas credenciales y nombres de tablas pueden venir desde el item de configuracion centralizada en DynamoDB:

- `MYSQL_HOST`
- `MYSQL_PORT`
- `MYSQL_DATABASE`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_TARGET_TABLE`
- `MYSQL_SCENES_TABLE`
- `MYSQL_SCENE_FILES_TABLE`

### Scheduler

El scheduler ya no depende del `.env` para sus banderas operativas. Lee esas claves desde el item de configuracion en DynamoDB.

Las claves que lee son:

- `SYNC_SCHEDULER_ENABLED`
- `SYNC_SCHEDULER_MODE`
- `SYNC_SCHEDULER_TIME`
- `SYNC_SCHEDULER_TIMEZONE`
- `SYNC_SCHEDULER_INTERVAL_SECONDS`
- `SYNC_SCHEDULER_EVERY_N_DAYS`
- `SYNC_SCHEDULER_DRY_RUN`
- `SYNC_SCHEDULER_ACTIVE_ONLY`
- `SYNC_SCHEDULER_BATCH_SIZE`
- `SYNC_SCHEDULER_INCLUDE_SCENE_JSON`
- `SYNC_SCHEDULER_DATE_FROM`
- `SYNC_SCHEDULER_DATE_TO`
- `SYNC_SCHEDULER_SERVICE_NAME`

## Columnas manuales recomendadas en MySQL

Estas columnas deben existir para que el servicio guarde toda la informacion.

### `s3_monitoring_escena_archivos`

```sql
ALTER TABLE s3_monitoring_escena_archivos
ADD COLUMN json_content LONGTEXT NULL AFTER s3_uri;
```

### `s3_monitoring_escenas`

```sql
ALTER TABLE s3_monitoring_escenas
ADD COLUMN fase2_completa_at DATETIME NULL DEFAULT NULL AFTER ia_exists;
```

### `s3_monitoring_producciones`

```sql
ALTER TABLE s3_monitoring_producciones
  ADD COLUMN pbox JSON NULL,
  ADD COLUMN polygon_bbox JSON NULL,
  ADD COLUMN tile_bbox JSON NULL,
  ADD COLUMN tile_center_lat DECIMAL(10,8) NULL,
  ADD COLUMN tile_center_lon DECIMAL(11,8) NULL,
  ADD COLUMN tile_edge_meters INT UNSIGNED NULL DEFAULT 2000;
```

## Ejemplos de uso

### Producciones

```bash
curl "http://localhost:8006/productions/1987/dynamo"
curl "http://localhost:8006/productions/1987/mysql"
curl -X POST "http://localhost:8006/productions/1987/sync?dry_run=false"
curl "http://localhost:8006/productions/inserts"
curl "http://localhost:8006/productions/1987/inserts"
```

### Escenas de una produccion

```bash
curl "http://localhost:8006/productions/1987/scenes/dynamo"
curl "http://localhost:8006/productions/1987/scenes/mysql"
curl -X POST "http://localhost:8006/productions/1987/scenes/sync?dry_run=false"
curl -X POST "http://localhost:8006/productions/1987/scenes/S2B_14QLJ_20260528_0_L2A/sync?dry_run=false"
curl "http://localhost:8006/productions/1987/scenes/inserts"
```

### Fase 1: escenas

```bash
curl "http://localhost:8006/productions/escenes/dynamo"
curl "http://localhost:8006/productions/escenes/mysql"
curl "http://localhost:8006/productions/escenes/inserts"
curl -X POST "http://localhost:8006/productions/escenes/sync?dry_run=false"
```

### Fase 2: archivos

```bash
curl -X POST "http://localhost:8006/productions/escenes/archivos/sync?dry_run=false"
curl -X POST "http://localhost:8006/productions/1987/escenes/archivos/sync?dry_run=false"
curl -X POST "http://localhost:8006/productions/1987/scenes/S2B_14QLJ_20260528_0_L2A/archivos/sync?dry_run=false"
curl "http://localhost:8006/productions/escenes/archivos/mysql"
curl "http://localhost:8006/productions/escenes/archivos/s3"
```

### Fase 3: analisis IA

```bash
curl -X POST "http://localhost:8006/productions/ia/sync?dry_run=false"
curl "http://localhost:8006/productions/ia/pending"
curl "http://localhost:8006/productions/1987/ia/pending"
```

### Jobs

```bash
curl "http://localhost:8006/sync/jobs"
curl "http://localhost:8006/sync/jobs/active"
curl "http://localhost:8006/sync/jobs/mtif_all_1234567890"
curl -X POST "http://localhost:8006/sync/jobs/cancel/mtif_all_1234567890"
```

### Configuracion runtime

```bash
curl "http://localhost:8006/config/runtime"
curl "http://localhost:8006/config/runtime?force_refresh=true"
curl -X POST "http://localhost:8006/config/runtime/refresh"
```

## Scheduler automatico

El schedule puede disparar el flujo maestro de forma automatica.

### Modos

- `interval`: cada cierto numero de segundos
- `daily`: una vez al dia a una hora exacta
- `every_n_days`: cada N dias a una hora exacta

### Lectura de configuracion

Las banderas del scheduler se leen desde el item de configuracion en DynamoDB. Si cambias el item, puedes refrescarlo con:

```bash
curl -X POST "http://localhost:8006/config/runtime/refresh"
```

### Logs del scheduler

La tabla usada es:

```sql
monitoring_daemon_logs (
  monitoring_daemon_log_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  service_name VARCHAR(80) NOT NULL,
  event_name VARCHAR(120) NOT NULL,
  status VARCHAR(40) NOT NULL,
  message VARCHAR(500) NULL,
  payload_json LONGTEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY (service_name),
  KEY (event_name),
  KEY (status),
  KEY (created_at)
)
```

## Ejecucion local

### Construir y levantar

```bash
docker compose up -d --build agro-syncronizador
```

### Reiniciar solo el contenedor

```bash
docker compose restart agro-syncronizador
```

### Modo desarrollo con recarga automatica

Para que detecte cambios sin reconstruir manualmente:

- montar el codigo como volumen
- usar `uvicorn --reload`

Ejemplo de comando:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8006 --reload
```

Este modo es solo para depuracion local de codigo. Para despliegue real con Docker Compose, lo normal es arrancar sin `--reload` y cambiar solo el registro de configuracion en DynamoDB.

### Puerto recomendado

Para este microservicio, el puerto `8006` esta bien porque ya coincide con la documentacion actual y te deja separado el sync de otros servicios como TIF o frontend. Si quieres estandarizar varios microservicios, mantén:

- `8001` para TIF
- `8006` para Sync
- otros servicios en puertos cercanos pero fijos, por ejemplo `8005`, `8007`, `8008`

## Notas finales

- `dry_run=true` simula el proceso.
- `dry_run=false` escribe datos reales.
- El Swagger debe usarse para explorar todos los endpoints y sus parametros.
- Mantener la sincronizacion pesada bloqueada a una sola ejecucion simultanea.
- Las rutas legacy pueden mantenerse como alias mientras migras al esquema canonico.

## Funcionalidad actual

Esto ya esta contemplado por el microservicio:

- comparar producciones entre DynamoDB y MySQL
- comparar escenas entre DynamoDB y MySQL
- sincronizar producciones por lote
- sincronizar escenas por lote
- indexar metadata de escenas
- indexar archivos derivados desde S3
- indexar analisis IA
- bloquear trabajos pesados concurrentes
- consultar jobs activos, terminados y cancelarlos
- ejecutar schedule automatico por hora, intervalo o N dias

## Falta agregar o documentar mejor

Estas piezas conviene dejarlas mas explicitamente documentadas en una siguiente pasada:

- implementar la lectura centralizada del item de configuracion desde DynamoDB
- ejemplos completos de request/response por cada endpoint canonico
- esquema exacto de payload para cada fase
- mapa definitivo de aliases legacy a rutas canónicas
- codigos de error estandarizados para `busy`, `already_synced`, `not_found` y `validation`
- ejemplos del flujo de cancelacion de jobs
- ejemplos del flujo del scheduler con `APP_ENV`, `APP_BUILD_TAG` y `LOG_LEVEL`
- diagrama del orden de fases:
  - producciones
  - escenas
  - archivos
  - IA



