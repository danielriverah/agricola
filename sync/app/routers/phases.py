"""
Rutas de fase global (sin production_id concreto) y ejecucion maestra.

  Fase 1: /productions/escenes/*
  Fase 2: /productions/escenes/archivos/sync
  Fase 3: /productions/ia/*
  Maestra: /productions/sync/full
"""
from typing import Optional

from fastapi import APIRouter, Query

from app.clients.dynamo_client import get_dynamo
from app.clients.mysql_client import get_mysql
from app.core.settings import get_settings
from app.services.config_service import get_runtime_config
from app.services.job_manager import get_job_manager
from app.services.mysql_repo import get_repo
from app.services.sync_service import get_sync_service

router = APIRouter(prefix="/productions", tags=["phases"])
sync = get_sync_service()
jobs = get_job_manager()


# ===================== FASE 1: ESCENAS (global) =====================
@router.get("/escenes/dynamo")
def escenes_dynamo(
    active_only: bool = Query(False),
    production_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    return sync.scenes_dynamo_grouped(
        active_only=active_only,
        production_id=production_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/escenes/mysql")
def escenes_mysql(
    active_only: bool = Query(False),
    production_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    return sync.scenes_mysql_snapshot(
        active_only=active_only,
        production_id=production_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/escenes/inserts")
def escenes_inserts(
    active_only: bool = Query(False),
    production_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    return sync.scenes_inserts_grouped(
        active_only=active_only,
        production_id=production_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.post("/escenes/sync")
def escenes_sync(
    dry_run: bool = Query(True),
    active_only: bool = Query(False),
    include_scene_json: bool = Query(False),
    production_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    def _run(job):
        plan, grouped = sync.scenes_inserts_grouped_with_dynamo(
            active_only=active_only,
            production_id=production_id,
            date_from=date_from,
            date_to=date_to,
        )
        out = []
        pending_by_production = [
            item for item in plan["by_production"] if item.get("pending_count", 0) > 0
        ]
        if not pending_by_production:
            return {"phase": "scenes_global", "status": "already_synced", "plan": plan, "results": []}

        grouped_pending: dict[int, list[dict]] = {}
        for prod_item in pending_by_production:
            if job.cancel_requested:
                break
            pid = prod_item["production_id"]
            pending_scenes = set(prod_item.get("pending_insert", []))
            if not pending_scenes:
                continue
            scenes = grouped.get(pid, [])
            grouped_pending[pid] = [scene for scene in scenes if scene.get("clave") in pending_scenes]
            out.append(sync.sync_scenes_from_plan(
                production_id=pid,
                scene_names=list(pending_scenes),
                dry_run=dry_run,
                include_scene_json=include_scene_json,
                scenes_data=scenes,
                sync_date_to=date_to,
            ))
        return {"phase": "scenes_global", "plan": plan, "results": out, "by_production": grouped_pending}
    return jobs.run_heavy(kind="escenes_all", dry_run=dry_run, fn=_run)


# ===================== FASE 2: ARCHIVOS (global) =====================
@router.post("/escenes/archivos/sync")
def archivos_sync_global(
    dry_run: bool = Query(True),
    active_only: bool = Query(False),
    production_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    def _run(job):
        job_manager = get_job_manager()
        job_manager.set_progress(job.job_id, 5, "Etapa 3/5: archivos - iniciando plan global")
        plan = sync.files_inserts_global(
            production_id=production_id,
            active_only=active_only,
            date_from=date_from,
            date_to=date_to,
        )
        out = []
        pending_by_production = [item for item in plan["by_production"] if item.get("pending_count", 0) > 0]
        if not pending_by_production:
            job_manager.set_progress(job.job_id, 40, "Etapa 3/5: archivos - sin pendientes")
            return {"phase": "files_global", "status": "already_synced", "plan": plan, "results": []}
        total_files = max(int(plan.get("total_pending", 0) or 0), 1)
        processed_files = 0
        job_manager.set_progress(
            job.job_id,
            40,
            f"Etapa 3/5: archivos - plan global listo ({plan.get('total_pending', 0)} archivos pendientes)",
        )
        for prod_item in pending_by_production:
            pid = prod_item["production_id"]
            if job.cancel_requested:
                break
            scene_items = [scene for scene in prod_item.get("by_scene", []) if scene.get("pending_count", 0) > 0]
            prod_files = sum(int(scene.get("pending_count", 0) or 0) for scene in scene_items)
            prod_scenes = len(scene_items)
            job_manager.set_progress(
                job.job_id,
                40 + int((processed_files / total_files) * 50),
                f"Etapa 3/5: archivos - prod {pid} | escenas {prod_scenes} | archivos {prod_files} | avance {processed_files}/{total_files}",
            )
            out.append(sync.sync_files_from_plan(
                production_id=pid,
                scene_items=scene_items,
                dry_run=dry_run,
                date_to=date_to,
            ))
            processed_files += prod_files
            job_manager.set_progress(
                job.job_id,
                40 + int((processed_files / total_files) * 50),
                f"Etapa 3/5: archivos - prod {pid} finalizada | escenas {prod_scenes} | archivos {prod_files} | avance {processed_files}/{total_files}",
            )
        return {"phase": "files_global", "plan": plan, "results": out}
    return jobs.run_heavy(kind="archivos_all", dry_run=dry_run, fn=_run)


@router.get("/escenes/archivos/mysql")
def archivos_mysql_global(
    active_only: bool = Query(False),
    production_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    print("INICIA CONSULTA URL MYSQL")
    result=sync.files_mysql_snapshot(
        production_id=production_id,
        active_only=active_only,
        date_from=date_from,
        date_to=date_to,
    )
    print("TERMINA CONSULTA URL MYSQL")
    return result


@router.get("/escenes/archivos/s3")
def archivos_s3_global(
    active_only: bool = Query(False),
    production_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    return sync.files_s3_snapshot(
        production_id=production_id,
        active_only=active_only,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/escenes/archivos/inserts")
def archivos_inserts_global(
    active_only: bool = Query(False),
    production_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    return sync.files_inserts_global(
        production_id=production_id,
        active_only=active_only,
        date_from=date_from,
        date_to=date_to,
    )


# ===================== FASE 3: IA (global) =====================
@router.post("/ia/sync")
def ia_sync_global(dry_run: bool = Query(True)):
    return jobs.run_heavy(
        kind="ia_all", dry_run=dry_run,
        fn=lambda job: sync.sync_ia(None, dry_run),
    )


@router.get("/ia/pending")
def ia_pending_global():
    return sync.ia_pending(None)


# ===================== FASE 4: GEOMETRIA / TILE =====================
@router.get("/geometry/inserts")
def geometry_inserts_global(active_only: bool = Query(False),production_id: Optional[int] = Query(None)):
    return sync.geometry_inserts(production_id=production_id,active_only=active_only)


@router.post("/geometry/sync")
def geometry_sync_global(dry_run: bool = Query(True), active_only: bool = Query(False),production_id: Optional[int] = Query(None)):
    def _run(job):
        plan = sync.geometry_inserts(production_id=production_id, active_only=active_only)
        pending = plan["pending"]
        out = []
        total = len(pending) or 1
        for index, item in enumerate(pending, start=1):
            if job.cancel_requested:
                break
            job_manager = get_job_manager()
            pid = item["production_id"]
            job_manager.set_progress(job.job_id, int(((index - 1) / total) * 100),
                                     f"Procesando produccion {pid}")
            '''result = sync.sync_geometry(
                production_id=production_id,
                dry_run=dry_run,
                active_only=active_only,
                inserts={"pending": [item]},
            )'''
            result = sync.sync_geometry_item(item=item,dry_run=dry_run)
            print(result)
            result["reasons"] = item.get("reasons", [])
            out.append(result)
            job_manager.set_progress(job.job_id, int((index / total) * 100),
                                     f"Produccion {pid} procesada")
        return {"phase": "geometry_global", "plan": plan, "results": out}

    return jobs.run_heavy(kind="geometry_all", dry_run=dry_run, fn=_run)


# ===================== EJECUCION MAESTRA =====================
@router.post("/sync/full")
def sync_full(dry_run: bool = Query(True), production_id: Optional[int] = Query(None),
              active_only: bool = Query(False), include_scene_json: bool = Query(False)):
    return jobs.run_heavy(
        kind="full", dry_run=dry_run,
        fn=lambda job: sync.sync_full(production_id, dry_run, active_only, include_scene_json, job=job),
        params={
            "active_only": active_only,
            "production_id": production_id,
            "include_scene_json": include_scene_json,
        },
    )
