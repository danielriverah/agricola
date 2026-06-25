"""
Scheduler automatico.

Modos (leidos desde el item de config en DynamoDB):
  - interval     : cada SYNC_SCHEDULER_INTERVAL_SECONDS
  - daily        : una vez al dia a SYNC_SCHEDULER_TIME
  - every_n_days : cada SYNC_SCHEDULER_EVERY_N_DAYS a SYNC_SCHEDULER_TIME

El job dispara la ejecucion maestra (sync_full) respetando el lock de jobs
pesados; si ya hay uno corriendo, lo registra y omite la corrida.
"""
import json
from datetime import datetime

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.errors import SyncBusyError
from app.core.logging_config import get_logger
from app.services.config_service import get_runtime_config
from app.services.job_manager import get_job_manager
from app.services.mysql_repo import get_repo
from app.services.sync_service import get_sync_service

log = get_logger(__name__)

_scheduler: BackgroundScheduler | None = None
_JOB_ID = "agro_sync_master"


def _run_scheduled_sync():
    cfg = get_runtime_config(force_refresh=True)
    sync = get_sync_service()
    jobs = get_job_manager()
    repo = get_repo()
    svc_name = cfg.scheduler_service_name

    repo.log_daemon_event(svc_name, "scheduled_sync", "started",
                          message=f"mode={cfg.scheduler_mode} dry_run={cfg.scheduler_dry_run}")
    try:
        result = jobs.run_heavy(
            kind="scheduled_full", dry_run=cfg.scheduler_dry_run,
            fn=lambda job: sync.sync_full(
                production_id=None,
                dry_run=cfg.scheduler_dry_run,
                active_only=cfg.scheduler_active_only,
                include_scene_json=cfg.scheduler_include_scene_json,
                job=job,
            ),
        )
        repo.log_daemon_event(svc_name, "scheduled_sync", "done",
                              payload_json=json.dumps(result, default=str)[:60000])
        log.info("Scheduled sync completado: job=%s", result.get("job_id"))
    except SyncBusyError:
        repo.log_daemon_event(svc_name, "scheduled_sync", "skipped_busy",
                              message="Ya habia una sync pesada activa.")
        log.warning("Scheduled sync omitido: ya hay una sync activa.")
    except Exception as exc:  # noqa: BLE001
        repo.log_daemon_event(svc_name, "scheduled_sync", "error", message=str(exc))
        log.exception("Scheduled sync fallo")


def _build_trigger(cfg):
    tz = pytz.timezone(cfg.scheduler_timezone)
    mode = (cfg.scheduler_mode or "daily").lower()

    if mode == "interval":
        return IntervalTrigger(seconds=cfg.scheduler_interval_seconds, timezone=tz)

    # daily y every_n_days usan hora exacta HH:MM
    hour, minute = 2, 0
    try:
        hour, minute = [int(x) for x in cfg.scheduler_time.split(":")]
    except Exception:  # noqa: BLE001
        log.warning("SYNC_SCHEDULER_TIME invalido (%s), usando 02:00", cfg.scheduler_time)

    if mode == "every_n_days":
        n = max(1, cfg.scheduler_every_n_days)
        return CronTrigger(day=f"*/{n}", hour=hour, minute=minute, timezone=tz)

    # daily
    return CronTrigger(hour=hour, minute=minute, timezone=tz)


def start_scheduler():
    global _scheduler
    cfg = get_runtime_config(force_refresh=True)

    if not cfg.scheduler_enabled:
        log.info("Scheduler deshabilitado (SYNC_SCHEDULER_ENABLED=false).")
        return

    _scheduler = BackgroundScheduler(timezone=pytz.timezone(cfg.scheduler_timezone))
    trigger = _build_trigger(cfg)
    _scheduler.add_job(_run_scheduled_sync, trigger=trigger, id=_JOB_ID,
                       replace_existing=True, max_instances=1, coalesce=True)
    _scheduler.start()
    log.info("Scheduler iniciado | mode=%s tz=%s", cfg.scheduler_mode, cfg.scheduler_timezone)


def shutdown_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Scheduler detenido.")


def reload_scheduler():
    """Reaplica la config del scheduler (tras un refresh de config)."""
    shutdown_scheduler()
    start_scheduler()
