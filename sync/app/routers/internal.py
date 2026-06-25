from datetime import date
from typing import Optional

from fastapi import APIRouter, Query

from app.services.job_manager import get_job_manager
from app.services.mysql_repo import get_repo
from app.services.sync_service import get_sync_service

router = APIRouter(prefix="/internal", tags=["internal"])
sync = get_sync_service()
jobs = get_job_manager()


@router.post("/productions/sync/full")
def internal_sync_full(
    dry_run: bool = Query(True),
    production_id: Optional[int] = Query(None),
    active_only: bool = Query(False),
    include_scene_json: bool = Query(False),
):
    return jobs.run_heavy(
        kind="internal_full",
        dry_run=dry_run,
        fn=lambda job: sync.sync_full(production_id, dry_run, active_only, include_scene_json, job=job),
        params={
            "active_only": active_only,
            "production_id": production_id,
            "include_scene_json": include_scene_json,
        },
    )


@router.post("/productions/sync")
def internal_productions_sync(
    dry_run: bool = Query(True),
    active_only: bool = Query(False),
):
    def _run(job):
        plan = sync.production_inserts()
        return sync.sync_productions_from_plan(plan, dry_run, active_only)

    return jobs.run_heavy(
        kind="internal_productions",
        dry_run=dry_run,
        fn=_run,
        params={"active_only": active_only},
    )


@router.post("/productions/escenes/sync")
def internal_scenes_sync(
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

    return jobs.run_heavy(
        kind="internal_scenes",
        dry_run=dry_run,
        fn=_run,
        params={
            "active_only": active_only,
            "production_id": production_id,
            "date_from": date_from,
            "date_to": date_to,
            "include_scene_json": include_scene_json,
        },
    )


@router.post("/productions/escenes/archivos/sync")
def internal_files_sync(
    dry_run: bool = Query(True),
    active_only: bool = Query(False),
    production_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    def _run(job):
        plan = sync.files_inserts_global(
            production_id=production_id,
            active_only=active_only,
            date_from=date_from,
            date_to=date_to,
        )
        out = []
        pending_by_production = [item for item in plan["by_production"] if item.get("pending_count", 0) > 0]
        if not pending_by_production:
            return {"phase": "files_global", "status": "already_synced", "plan": plan, "results": []}

        total_files = max(int(plan.get("total_pending", 0) or 0), 1)
        processed_files = 0
        for prod_item in pending_by_production:
            pid = prod_item["production_id"]
            scene_items = [scene for scene in prod_item.get("by_scene", []) if scene.get("pending_count", 0) > 0]
            if not scene_items:
                continue
            prod_files = sum(int(scene.get("pending_count", 0) or 0) for scene in scene_items)
            out.append(sync.sync_files_from_plan(
                production_id=pid,
                scene_items=scene_items,
                dry_run=dry_run,
                date_to=date_to,
            ))
            processed_files += prod_files
        return {
            "phase": "files_global",
            "plan": plan,
            "results": out,
            "progress": {
                "processed_files": processed_files,
                "total_files": total_files,
            },
        }

    return jobs.run_heavy(
        kind="internal_files",
        dry_run=dry_run,
        fn=_run,
        params={
            "active_only": active_only,
            "production_id": production_id,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


@router.post("/productions/ia/sync")
def internal_ia_sync(
    dry_run: bool = Query(True),
    production_id: Optional[int] = Query(None),
):
    return jobs.run_heavy(
        kind="internal_ia",
        dry_run=dry_run,
        fn=lambda job: sync.sync_ia(production_id, dry_run),
    )


@router.post("/productions/geometry/sync")
def internal_geometry_sync(
    dry_run: bool = Query(True),
    active_only: bool = Query(False),
    production_id: Optional[int] = Query(None),
):
    def _run(job):
        plan = sync.geometry_inserts(production_id=production_id, active_only=active_only)
        pending = plan.get("pending", []) if isinstance(plan, dict) else list(plan or [])
        if not pending:
            return {"phase": "geometry_global", "status": "already_synced", "plan": plan, "results": []}

        out = []
        total = len(pending)
        for index, item in enumerate(pending, start=1):
            if job.cancel_requested:
                break
            out.append(sync.sync_geometry_item(item=item, dry_run=dry_run))
        return {"phase": "geometry_global", "plan": plan, "results": out, "progress": {"total": total}}

    return jobs.run_heavy(kind="internal_geometry", dry_run=dry_run, fn=_run)
