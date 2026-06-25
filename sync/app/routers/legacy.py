"""
Aliases legacy. Mantienen compatibilidad mientras se migra al esquema canonico.
Cada alias delega en el servicio/jobs igual que su ruta canonica.
"""
from typing import Optional

from fastapi import APIRouter, Query

from app.services.job_manager import get_job_manager
from app.services.sync_service import get_sync_service

router = APIRouter(prefix="/sync", tags=["legacy"])
sync = get_sync_service()
jobs = get_job_manager()


@router.post("/productions")
def legacy_sync_productions(dry_run: bool = Query(True),
                           production_id: Optional[int] = Query(None),
                           active_only: bool = Query(False)):
    """Alias de POST /productions/{id}/sync (o todas)."""
    return jobs.run_heavy(
        kind="legacy_productions", dry_run=dry_run,
        fn=lambda job: sync.sync_productions(production_id, dry_run, active_only),
    )


@router.post("/scenes")
def legacy_sync_scenes(production_id: int = Query(...), dry_run: bool = Query(True)):
    """Alias de POST /productions/{id}/scenes/sync."""
    return jobs.run_heavy(
        kind="legacy_scenes", dry_run=dry_run,
        fn=lambda job: sync.sync_scenes(production_id, dry_run),
    )


@router.post("/s3/phase1")
def legacy_phase1(dry_run: bool = Query(True), active_only: bool = Query(False)):
    """Alias de POST /productions/escenes/sync (metadata de escenas)."""
    def _run(job):
        prod_ids = sync._target_production_ids(None, active_only)
        return {"results": [sync.sync_scenes(p, dry_run) for p in prod_ids]}
    return jobs.run_heavy(kind="legacy_phase1", dry_run=dry_run, fn=_run)


@router.post("/s3/phase2")
def legacy_phase2(
    dry_run: bool = Query(True),
    active_only: bool = Query(False),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    """Alias de POST /productions/escenes/archivos/sync."""
    def _run(job):
        prod_ids = sync._target_production_ids(None, active_only)
        return {"results": [sync.sync_files(p, dry_run, date_from=date_from, date_to=date_to) for p in prod_ids]}
    return jobs.run_heavy(kind="legacy_phase2", dry_run=dry_run, fn=_run)


@router.post("/s3/phase3")
def legacy_phase3(dry_run: bool = Query(True)):
    """Alias de POST /productions/ia/sync."""
    return jobs.run_heavy(
        kind="legacy_phase3", dry_run=dry_run,
        fn=lambda job: sync.sync_ia(None, dry_run),
    )


@router.post("/s3/full")
def legacy_full(dry_run: bool = Query(True), production_id: Optional[int] = Query(None),
                active_only: bool = Query(False)):
    """Alias de POST /productions/sync/full."""
    return jobs.run_heavy(
        kind="legacy_full", dry_run=dry_run,
        fn=lambda job: sync.sync_full(production_id, dry_run, active_only, job=job),
        params={
            "active_only": active_only,
            "production_id": production_id,
        },
    )
