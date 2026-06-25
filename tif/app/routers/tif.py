"""
Router TIF: preview de archivos, escenas sin tif, y generación de derivados.

Reglas aplicadas:
  - lectura solo de MySQL,
  - generación escribe en S3/almacenamiento pero devuelve rutas, sin indexar,
  - dry_run no escribe nada,
  - generación masiva corre como job bajo demanda (no scheduler).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from app.config.models import AppConfig
from app.core.dependencies import require_mysql, require_storage
from app.core.errors import HeavyJobBusy, ValidationError
from app.jobs.manager import Job, get_job_manager
from app.services.tif_service import build_service

router = APIRouter(prefix="/monitoring", tags=["tif"])


# --------------------------- Lectura / preview ---------------------------

@router.get("/scenes/missing-tif", summary="Escenas sin multiband.tif (global)")
def missing_tif_all(cfg: AppConfig = Depends(require_mysql)):
    return {"items": build_service(cfg).scenes.list_missing_tif()}


@router.get("/scenes/missing-tif/{production_id}", summary="Escenas sin tif de una producción")
def missing_tif_for_production(
    production_id: str = Path(...),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    prod = svc.get_production_or_none(production_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {production_id}", "production_id")
    if not svc.production_has_tile_bbox(prod):
        raise ValidationError("La producción no tiene tile_bbox; este módulo no puede procesarla.", "tile_bbox")
    return {"production_id": production_id, "items": svc.missing_tif_for_production(prod)}


@router.get("/files/tifs/{production_id}", summary="Archivos .tif indexados de una producción")
def files_tifs(
    production_id: str = Path(...),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    prod = svc.get_production_or_none(production_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {production_id}", "production_id")
    if not svc.production_has_tile_bbox(prod):
        raise ValidationError("La producción no tiene tile_bbox; este módulo no puede procesarla.", "tile_bbox")
    internal = prod.get("s3_monitoring_produccion_id")
    return {"items": svc.files.list_by_production_and_extension(internal, "tif")}


@router.get("/files/others/{production_id}", summary="Otros archivos indexados (png/json)")
def files_others(
    production_id: str = Path(...),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    prod = svc.get_production_or_none(production_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {production_id}", "production_id")
    if not svc.production_has_tile_bbox(prod):
        raise ValidationError("La producción no tiene tile_bbox; este módulo no puede procesarla.", "tile_bbox")
    internal = prod.get("s3_monitoring_produccion_id")
    png = svc.files.list_by_production_and_extension(internal, "png")
    js = svc.files.list_by_production_and_extension(internal, "json")
    return {"png": png, "json": js}


@router.get("/progress/{production_id}", summary="Progreso de jobs activos de una producción")
def progress(production_id: str = Path(...)):
    jm = get_job_manager()
    items = [j.as_dict() for j in jm.list() if str(production_id) in j.kind]
    return {"production_id": production_id, "jobs": items}


@router.get("/status/{production_id}", summary="Resumen de estado de una producción")
def status(
    production_id: str = Path(...),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    prod = svc.get_production_or_none(production_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {production_id}", "production_id")
    if not svc.production_has_tile_bbox(prod):
        raise ValidationError("La producción no tiene tile_bbox; este módulo no puede procesarla.", "tile_bbox")
    scenes = svc.scenes_for_production(prod)
    missing = [s for s in scenes if not s.get("truth_tif_exists")]
    return {
        "production_id": production_id,
        "scenes_total": len(scenes),
        "missing_tif": len(missing),
        "usable_persisted": sum(1 for s in scenes if s.get("usable")),
    }


# --------------------------- Generación ---------------------------

@router.post(
    "/scenes/missing-tif/{production_id}/generate",
    summary="Generar tif/derivados para escenas sin tif de una producción (job)",
)
def generate_missing_for_production(
    production_id: str = Path(...),
    dry_run: bool = Query(False, description="Si true, no escribe nada en S3."),
    cfg: AppConfig = Depends(require_storage),
):
    require_mysql()
    svc = build_service(cfg)
    prod = svc.get_production_or_none(production_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {production_id}", "production_id")
    if not svc.production_has_tile_bbox(prod):
        raise ValidationError("La producción no tiene tile_bbox; este módulo no puede procesarla.", "tile_bbox")

    scenes = svc.missing_tif_for_production(prod)
    jm = get_job_manager()
    job, active = jm.try_create_heavy(kind=f"generate:prod:{production_id}", total=len(scenes))
    if job is None:
        raise HeavyJobBusy(active.as_dict() if active else None)

    def work(j: Job) -> None:
        for sc in scenes:
            if j.cancel_requested():
                break
            res = svc.process_one_scene(prod, sc, generate_files=True, dry_run=dry_run)
            j.results.append(res.as_dict())
            j.processed += 1

    jm.run_async(job, work)
    return {"job_id": job.id, "scheduled_scenes": len(scenes), "dry_run": dry_run, "db_writes": False}


@router.post(
    "/scenes/missing-tif/generate/all",
    summary="Generar tif/derivados para todas las escenas sin tif (job masivo)",
)
def generate_missing_all(
    dry_run: bool = Query(False),
    batch: int | None = Query(None, ge=1, le=2000, description="Límite de escenas a procesar."),
    cfg: AppConfig = Depends(require_storage),
):
    require_mysql()
    svc = build_service(cfg)
    scenes = svc.scenes.list_missing_tif()
    if batch:
        scenes = scenes[:batch]

    # cache de producciones por id interno para no re-consultar
    prod_cache: dict = {}

    def resolve_prod(sc: dict) -> dict | None:
        key = sc.get("s3_monitoring_produccion_id")
        if key not in prod_cache:
            prod_cache[key] = svc.productions.get_by_internal_id(key)
        return prod_cache[key]

    jm = get_job_manager()
    job, active = jm.try_create_heavy(kind="generate:all", total=len(scenes))
    if job is None:
        raise HeavyJobBusy(active.as_dict() if active else None)

    def work(j: Job) -> None:
        for sc in scenes:
            if j.cancel_requested():
                break
            prod = resolve_prod(sc)
            if not prod:
                j.results.append({"scene": sc.get("scene_name"), "error": "producción no encontrada"})
            else:
                res = svc.process_one_scene(prod, sc, generate_files=True, dry_run=dry_run)
                j.results.append(res.as_dict())
            j.processed += 1

    jm.run_async(job, work)
    return {"job_id": job.id, "scheduled_scenes": len(scenes), "dry_run": dry_run, "db_writes": False}


@router.post("/render/{production_id}", summary="Render de la escena más reciente (job)")
def render_production(
    production_id: str = Path(...),
    dry_run: bool = Query(False),
    cfg: AppConfig = Depends(require_storage),
):
    require_mysql()
    svc = build_service(cfg)
    prod = svc.get_production_or_none(production_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {production_id}", "production_id")
    if not svc.production_has_tile_bbox(prod):
        raise ValidationError("La producción no tiene tile_bbox; este módulo no puede procesarla.", "tile_bbox")
    scenes = svc.scenes_for_production(prod)
    if not scenes:
        raise ValidationError("La producción no tiene escenas.", "production_id")

    jm = get_job_manager()
    job, active = jm.try_create_heavy(kind=f"render:prod:{production_id}", total=1)
    if job is None:
        raise HeavyJobBusy(active.as_dict() if active else None)

    def work(j: Job) -> None:
        if j.cancel_requested():
            return
        res = svc.process_one_scene(prod, scenes[0], generate_files=True, dry_run=dry_run)
        j.results.append(res.as_dict())
        j.processed = 1

    jm.run_async(job, work)
    return {"job_id": job.id, "scheduled_scenes": 1, "dry_run": dry_run, "db_writes": False}
