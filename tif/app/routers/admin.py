"""
Router administrativo y de depuración.

Notas de contrato:
  - Operaciones manuales / bajo demanda. NO hay scheduler ni cron.
  - /monitoring/reset solo limpia estado EN MEMORIA (jobs), nunca toca DB.
  - Rutas legacy de escritura a DB (bootstrap-sql, admin/reset SQL, daemon,
    enqueue) quedan deshabilitadas y responden error de validación.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.config.models import AppConfig
from app.core.dependencies import require_mysql, require_storage
from app.core.errors import HeavyJobBusy, WriteAttemptBlocked
from app.jobs.manager import Job, get_job_manager
from app.services.tif_service import build_service

router = APIRouter(tags=["admin-debug"])


# --------------------------- Jobs admin ---------------------------

@router.get("/monitoring/jobs", summary="Listar todos los jobs")
def all_jobs():
    return {"items": [j.as_dict() for j in get_job_manager().list()]}


@router.get("/monitoring/jobs/stats", summary="Estadísticas de jobs")
def jobs_stats():
    return get_job_manager().stats()


@router.get("/monitoring/jobs/reconcile-log", summary="Ver log de reconciliación (memoria)")
def reconcile_log_get():
    jm = get_job_manager()
    done = [j.as_dict() for j in jm.list() if j.status.value in ("done", "failed", "cancelled")]
    return {"items": done}


@router.post("/monitoring/jobs/reconcile-log", summary="Reconciliar log (no escribe DB)")
def reconcile_log_post():
    # Reconciliación lógica en memoria: marca consistencia sin tocar ninguna DB.
    jm = get_job_manager()
    return {"reconciled": len(jm.list()), "db_writes": False}


@router.post("/monitoring/jobs/run-once", summary="Ejecutar una escena pendiente (bajo demanda)")
def run_once(
    dry_run: bool = Query(False),
    cfg: AppConfig = Depends(require_storage),
):
    require_mysql()
    svc = build_service(cfg)
    scenes = svc.scenes.list_missing_tif()
    scenes = [sc for sc in scenes if svc.production_has_tile_bbox(svc.productions.get_by_internal_id(sc.get("s3_monitoring_produccion_id")))]
    if not scenes:
        return {"processed": 0, "note": "No hay escenas pendientes."}
    sc = scenes[0]
    prod = svc.productions.get_by_internal_id(sc.get("s3_monitoring_produccion_id"))
    if not prod:
        return {"processed": 0, "note": "Producción de la escena no encontrada."}

    jm = get_job_manager()
    job, active = jm.try_create_heavy(kind="run-once", total=1)
    if job is None:
        raise HeavyJobBusy(active.as_dict() if active else None)

    def work(j: Job) -> None:
        if j.cancel_requested():
            return
        res = svc.process_one_scene(prod, sc, generate_files=True, dry_run=dry_run)
        j.results.append(res.as_dict())
        j.processed = 1

    jm.run_async(job, work)
    return {"job_id": job.id, "scheduled_scenes": 1, "dry_run": dry_run, "db_writes": False}


@router.post("/monitoring/jobs/run-batch", summary="Ejecutar lote pendiente (bajo demanda)")
def run_batch(
    dry_run: bool = Query(False),
    batch: int = Query(20, ge=1, le=2000, description="Límite de escenas a procesar."),
    cfg: AppConfig = Depends(require_storage),
):
    require_mysql()
    svc = build_service(cfg)
    scenes = svc.scenes.list_missing_tif()[:batch]
    scenes = [sc for sc in scenes if svc.production_has_tile_bbox(svc.productions.get_by_internal_id(sc.get("s3_monitoring_produccion_id")))]
    jm = get_job_manager()
    job, active = jm.try_create_heavy(kind="run-batch", total=len(scenes))
    if job is None:
        raise HeavyJobBusy(active.as_dict() if active else None)
    prod_cache: dict = {}

    def work(j: Job) -> None:
        for sc in scenes:
            if j.cancel_requested():
                break
            key = sc.get("s3_monitoring_produccion_id")
            if key not in prod_cache:
                prod_cache[key] = svc.productions.get_by_internal_id(key)
            prod = prod_cache[key]
            if prod:
                res = svc.process_one_scene(prod, sc, generate_files=True, dry_run=dry_run)
                j.results.append(res.as_dict())
            j.processed += 1

    jm.run_async(job, work)
    return {"job_id": job.id, "scheduled_scenes": len(scenes), "db_writes": False}


@router.post("/monitoring/reset", summary="Reiniciar estado en memoria (NO toca DB)")
def reset_memory():
    # Recrea el job manager: limpia jobs en memoria. No hay persistencia en DB.
    import app.jobs.manager as jm_module

    active = get_job_manager().active_heavy()
    if active:
        raise HeavyJobBusy(active.as_dict())

    jm_module._manager = None  # type: ignore[attr-defined]
    return {"reset": True, "scope": "in_memory_jobs", "db_writes": False}


@router.get("/outputs/s3", summary="Configuración de salida S3 (informativo)")
def outputs_s3(cfg: AppConfig = Depends(require_mysql)):
    return {
        "driver": cfg.storage.driver,
        "bucket": cfg.storage.s3_bucket,
        "base_path": cfg.storage.base_path,
        "multiband_filename": cfg.outputs.multiband_filename,
        "params_filename": cfg.outputs.params_filename,
    }


# --------------------------- Legacy de escritura: BLOQUEADAS ---------------------------
# Se exponen como handlers separados por método para no colisionar operation_id.
# Quedan fuera del schema OpenAPI (include_in_schema=False) salvo una entrada
# informativa, pero siguen respondiendo 403 ante cualquier intento.

def _blocked(route: str):
    raise WriteAttemptBlocked(route)


@router.get("/admin/reset", include_in_schema=False)
def legacy_admin_reset_get():
    _blocked("/admin/reset")


@router.post("/admin/reset", summary="[Deshabilitada] escritura a DB no permitida")
def legacy_admin_reset_post():
    _blocked("/admin/reset")


@router.get("/bootstrap-sql", include_in_schema=False)
def legacy_bootstrap_sql_get():
    _blocked("/bootstrap-sql")


@router.post("/bootstrap-sql", summary="[Deshabilitada] SQL de escritura no permitido")
def legacy_bootstrap_sql_post():
    _blocked("/bootstrap-sql")


@router.get("/monitoring/daemon", include_in_schema=False)
def legacy_daemon_get():
    _blocked("/monitoring/daemon")


@router.post("/monitoring/daemon", summary="[Deshabilitada] no hay daemon recurrente")
def legacy_daemon_post():
    _blocked("/monitoring/daemon")


@router.get("/monitoring/enqueue", include_in_schema=False)
def legacy_enqueue_get():
    _blocked("/monitoring/enqueue")


@router.post("/monitoring/enqueue", summary="[Deshabilitada] sin cola recurrente")
def legacy_enqueue_post():
    _blocked("/monitoring/enqueue")
