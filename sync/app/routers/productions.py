from typing import Optional
from fastapi import APIRouter, Query

from app.core.errors import NotFoundError
from app.services.job_manager import get_job_manager
from app.services.sync_service import get_sync_service

router = APIRouter(prefix="/productions", tags=["productions"])
sync = get_sync_service()
jobs = get_job_manager()


# ------------------- Producciones -------------------
@router.get("/inserts")
def productions_inserts_all():
    return sync.production_inserts()


@router.get("/{production_id}/dynamo")
def production_dynamo(production_id: int):
    item = sync.production_dynamo(production_id)
    if not item:
        raise NotFoundError(f"Produccion {production_id} no existe en DynamoDB.")
    return item


@router.get("/{production_id}/mysql")
def production_mysql(production_id: int):
    item = sync.production_mysql(production_id)
    if not item:
        raise NotFoundError(f"Produccion {production_id} no existe en MySQL.")
    return item


@router.get("/{production_id}/inserts")
def production_inserts(production_id: int):
    return sync.production_inserts(production_id)


@router.post("/{production_id}/sync")
def production_sync(production_id: int, dry_run: bool = Query(True)):
    return jobs.run_heavy(
        kind=f"prod_{production_id}", dry_run=dry_run,
        fn=lambda job: sync.sync_productions(production_id, dry_run),
    )


@router.post("/sync")
def productions_sync(dry_run: bool = Query(True), active_only: bool = Query(False)):
    return jobs.run_heavy(
        kind="prod_all",
        dry_run=dry_run,
        fn=lambda job: sync.sync_productions(None, dry_run, active_only),
    )


# ------------------- Escenas de una produccion -------------------
@router.get("/{production_id}/scenes/dynamo")
def scenes_dynamo(production_id: int):
    return sync.scenes_dynamo(production_id)


@router.get("/{production_id}/scenes/mysql")
def scenes_mysql(production_id: int):
    return sync.scenes_mysql(production_id)


@router.get("/{production_id}/scenes/inserts")
def scenes_inserts(production_id: int):
    return sync.scenes_inserts(production_id)


@router.post("/{production_id}/scenes/sync")
def scenes_sync(production_id: int, dry_run: bool = Query(True),
                include_scene_json: bool = Query(False)):
    return jobs.run_heavy(
        kind=f"scenes_{production_id}", dry_run=dry_run,
        fn=lambda job: sync.sync_scenes(production_id, dry_run, include_scene_json),
    )


@router.post("/{production_id}/scenes/{scene_name}/sync")
def scene_one_sync(production_id: int, scene_name: str, dry_run: bool = Query(True)):
    return jobs.run_heavy(
        kind=f"scene_{production_id}", dry_run=dry_run,
        fn=lambda job: sync.sync_scenes(production_id, dry_run, scene_name=scene_name),
    )


# ------------------- Fase 2: archivos (por produccion / escena) -------------------
@router.post("/{production_id}/escenes/archivos/sync")
@router.post("/{production_id}/scenes/archivos/sync")
def files_sync_by_production(
    production_id: int,
    dry_run: bool = Query(True),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    def _run(job):
        plan = sync.files_inserts_global(
            production_id=production_id,
            active_only=False,
            date_from=date_from,
            date_to=date_to,
        )
        prod_item = next(
            (item for item in plan.get("by_production", []) if item.get("production_id") == production_id),
            None,
        )
        if not prod_item or prod_item.get("pending_count", 0) <= 0:
            return {"phase": "files", "status": "already_synced", "plan": plan, "results": []}
        return sync.sync_files_from_plan(
            production_id=production_id,
            scene_items=[scene for scene in prod_item.get("by_scene", []) if scene.get("pending_count", 0) > 0],
            dry_run=dry_run,
            date_to=date_to,
        )
    return jobs.run_heavy(
        kind=f"files_{production_id}", dry_run=dry_run,
        fn=_run,
    )


@router.post("/{production_id}/scenes/{scene_name}/archivos/sync")
def files_sync_by_scene(
    production_id: int,
    scene_name: str,
    dry_run: bool = Query(True),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    return jobs.run_heavy(
        kind=f"files_{production_id}_{scene_name}", dry_run=dry_run,
        fn=lambda job: sync.sync_files(
            production_id, dry_run, scene_name=scene_name, date_from=date_from, date_to=date_to
        ),
    )


@router.get("/{production_id}/escenes/archivos/mysql")
def files_mysql_by_production(
    production_id: int,
    active_only: bool = Query(False),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    return sync.files_mysql_snapshot(
        production_id=production_id,
        active_only=active_only,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/{production_id}/escenes/archivos/s3")
def files_s3_by_production(
    production_id: int,
    active_only: bool = Query(False),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    return sync.files_s3_snapshot(
        production_id=production_id,
        active_only=active_only,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/{production_id}/escenes/archivos/inserts")
def files_inserts_by_production(
    production_id: int,
    active_only: bool = Query(False),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    return sync.files_inserts(
        production_id,
        date_from=date_from,
        date_to=date_to,
    )


# ------------------- Fase 3: IA (por produccion) -------------------
@router.get("/{production_id}/ia/pending")
def ia_pending_by_production(production_id: int):
    return sync.ia_pending(production_id)


# ------------------- Fase 4: geometria / tile -------------------
@router.get("/{production_id}/geometry")
def geometry_snapshot(production_id: int):
    return sync.geometry_snapshot(production_id)


@router.post("/{production_id}/geometry/sync")
def geometry_sync(production_id: int, dry_run: bool = Query(True)):
    return jobs.run_heavy(
        kind=f"geometry_{production_id}", dry_run=dry_run,
        fn=lambda job: sync.sync_geometry(
            production_id,
            dry_run,
            inserts=sync.geometry_inserts(production_id),
        ),
    )


@router.post("/{production_id}/geometry/tile/sync")
def geometry_tile_sync(production_id: int, dry_run: bool = Query(True)):
    return jobs.run_heavy(
        kind=f"tile_{production_id}", dry_run=dry_run,
        fn=lambda job: sync.sync_tile(production_id, dry_run),
    )


@router.get("/{production_id}/geometry/inserts")
def geometry_inserts_by_production(production_id: int):
    return sync.geometry_inserts(production_id)
