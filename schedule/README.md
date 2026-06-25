# Microservicio `schedule`

Microservicio que programa ejecuciones automaticas de endpoints HTTP para
cualquier microservicio del stack. No ejecuta logica de negocio: lee
configuracion, calcula cuando disparar, llama por HTTP, guarda historial y
registra `job_id` cuando la respuesta lo incluye. **No hace polling de `job_id`.**

## Arquitectura

```
app/
  main.py                 # FastAPI + lifespan (bootstrap, schema, worker)
  core/
    settings.py           # variables de entorno minimas
    app_config.py         # lectura de app_config desde DynamoDB
    runtime.py            # estado runtime (app_config cargado)
    timeutils.py          # zonas horarias y calculo de next_run_at
  db/
    mysql.py              # pool de conexiones + creacion de esquema
    repository.py         # queries de schedule_task y schedule_historia
  schemas/task.py         # validaciones Pydantic
  services/
    executor.py           # resuelve URL, llama HTTP, extrae job_id, guarda historial
    scheduler.py          # orquestacion: lock + ejecutar + actualizar estado
    worker.py             # worker async que revisa tareas vencidas
  api/routes.py           # endpoints HTTP
  static/index.html       # vista web de administracion
tests/                    # pruebas sin infra (validacion, executor, tiempo)
```

El servicio solo accede a: DynamoDB (`app_config`), MySQL (sus propias tablas)
y a los microservicios destino por HTTP. No escribe en bases de datos ajenas ni
llama funciones internas de otros servicios por codigo.

## Variables de entorno

Ver `.env.example`. Las minimas:

```env
APP_CONFIG_TABLE_NAME=app_config
APP_CONFIG_ITEM_ID=microservicio-schedule
APP_CONFIG_ITEM_PK=config_id
AWS_REGION=us-east-1
```

El resto de la configuracion (MySQL, timezone, targets, timeout) vive en
`app_config` dentro de DynamoDB.

## `app_config`

```json
{
  "config_id": "microservicio-schedule",
  "enabled": true,
  "timezone": "America/Mexico_City",
  "request_timeout_seconds": 30,
  "targets": {
    "servicio_a": "http://servicio-a",
    "otro_microservicio": "http://otro-microservicio"
  },
  "mysql": {
    "host": "mysql",
    "port": 3306,
    "database": "agricola",
    "user": "root",
    "password": "secret"
  }
}
```

`request_timeout_seconds` es opcional; si falta se usa
`DEFAULT_REQUEST_TIMEOUT_SECONDS` (30s).

## Ejecutar local

```bash
pip install -r requirements.txt
cp .env.example .env        # ajusta valores
uvicorn app.main:app --reload --port 8000
```

Vista web: `http://localhost:8000/`
API docs (Swagger): `http://localhost:8000/docs`

Para explorar la API sin DynamoDB/MySQL (solo pruebas):

```bash
SKIP_BOOTSTRAP=true uvicorn app.main:app --port 8000
```

## Docker

```bash
docker build -t schedule .
docker run --env-file .env -p 8000:8000 schedule
```

Integracion en el stack: ver `docker-compose.example.yml`. El servicio se
despliega como contenedor independiente con healthcheck en `GET /health`.

## Pruebas

```bash
pytest -q
```

Las pruebas cubren validaciones, resolucion de URL, extraccion de `job_id` y
calculo de `next_run_at` sin requerir infraestructura.

## Endpoints

| Metodo | Ruta | Descripcion |
|--------|------|-------------|
| GET    | `/health` | healthcheck |
| GET    | `/config/runtime` | configuracion resuelta (sin secretos de mysql) |
| GET    | `/tasks` | lista tareas (`?enabled=true/false`) |
| POST   | `/tasks` | crea tarea |
| GET    | `/tasks/{id}` | detalle |
| PUT    | `/tasks/{id}` | actualiza |
| DELETE | `/tasks/{id}` | elimina |
| POST   | `/tasks/{id}/run` | ejecucion manual inmediata |
| GET    | `/history` | historial (`?task_id=`) |
| GET    | `/history/{id}` | detalle de una ejecucion |

## Modelo de datos

### `schedule_task`
Definicion de cada tarea. Campos del README original mas los operativos:
`completed`, `running`, `locked_at` (bloqueo entre instancias), `next_run_at`,
`last_run_at`, `ejecuciones_realizadas`.

### `schedule_historia`
Cada ejecucion disparada. Incluye `final_url` ademas de los campos del README.
`status`: `success`, `http_error`, `exception` o `skipped`.

Las tablas se crean automaticamente al arrancar si no existen.

## Resolucion de URL

- Si `endpoint` es URL completa (`http://`/`https://`) se usa tal cual.
- Si es relativo: se busca la base en `app_config.targets[service_name]` y se
  concatena con el endpoint.

## Extraccion de `job_id`

Si `trae_job_id=true`, se navega `ruta_job_id` sobre el cuerpo de la respuesta.
El prefijo `body.` se interpreta como la raiz del body
(`body.job_id`, `body.task.job_id`, `body.data.execution.id`). Si la ruta no
existe se guarda `status=success`, `job_id=null` y una observacion.

## Auth soportada

- `none`: sin cabecera.
- `bearer`: `Authorization: Bearer {auth_token}`.
- `header`: `{auth_header_name}: {auth_token}`.

## Bloqueo entre instancias

Antes de ejecutar, el scheduler intenta un `UPDATE` atomico que marca
`running=1, locked_at=now` solo si la tarea no esta corriendo o su lock es
"stale" (mas viejo que `LOCK_TIMEOUT_SECONDS`). Asi, con varias instancias del
servicio, solo una ejecuta cada tarea. Al terminar se libera el lock.

## Reglas de ejecucion

- `enabled=false` o `completed=true` no se ejecutan.
- Si `cantidad_ejecuciones` se alcanzo, la tarea se marca `completed`.
- `daily=true`: se recalcula `next_run_at` al siguiente dia a la misma hora local.
- `daily=false` (`run_at`): ejecucion unica; al terminar se marca `completed`.
- Fallo HTTP -> `status=http_error`. Excepcion tecnica -> `status=exception`.

---

## Decisiones tomadas frente al README original

Donde el README dejaba pendientes o ambiguedades, se tomaron decisiones
conservadoras y documentadas:

1. **`daily` y `run_at` son excluyentes.** Una tarea es diaria (con
   `hora_ejecucion`) o de una sola vez (con `run_at`), nunca ambas. La
   validacion lo exige.
2. **`cantidad_ejecuciones=null` significa ilimitado** (para tareas diarias).
3. **Tarea `run_at` vencida** se ejecuta una vez y se marca `completed=true`
   (no se elimina, queda como historico).
4. **Concurrencia:** el worker procesa tareas vencidas de forma secuencial
   dentro de una instancia; entre instancias se evita el doble disparo con el
   lock `running`/`locked_at`. Una tarea vencida puede ejecutarse aunque otra
   distinta este corriendo (el lock es por tarea, no global).
5. **Header secreto obligatorio en rutas `internal`:** no se impone por ahora;
   la autenticacion se controla por tarea via `auth_type`. Queda como mejora
   futura (se puede usar `auth_type=header`).
6. **Campo `final_url`** se agrego a `schedule_historia` (lo pedia la consigna
   aunque no estaba en el README) para registrar la URL realmente invocada.
7. **Marcas de tiempo** se almacenan en UTC; el calculo de hora usa la
   `zona_horaria` de cada tarea (o el timezone default de `app_config`).
8. **Seguridad de `auth_token`:** se guarda en claro en MySQL por simplicidad,
   como en el README. Cifrado / secret manager queda como pendiente futuro.

## Pendientes futuros

- Cifrar `auth_token` o moverlo a un secret manager.
- Polling opcional de `job_id`.
- Header secreto obligatorio configurable para rutas `internal`.
- Ejecucion en paralelo configurable dentro de una instancia.
