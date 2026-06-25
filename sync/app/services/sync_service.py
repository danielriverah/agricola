"""
Servicio de sincronizacion. Orquesta las fases en orden:
  1. producciones   (Dynamo -> MySQL)
  2. escenas        (Dynamo -> MySQL)
  3. archivos       (S3 -> MySQL)        [Fase 2]
  4. IA             (S3 -> MySQL)        [Fase 3]
  5. geometría      (Dynamo -> MySQL)    [Fase 4/5]

Reglas:
  - dry_run=true  -> SIMULA: calcula que se insertaria/actualizaria pero NO escribe.
  - dry_run=false -> escribe por lote.
  - Si no hay nada pendiente -> 'already_synced'.
  - Las GET de comparacion (diff) no escriben.
"""
from typing import Any, Optional
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

from app.clients.dynamo_client import get_dynamo
from app.clients.s3_client import get_s3
from app.core.logging_config import get_logger
from app.core.settings import get_settings
from app.services.config_service import get_runtime_config
from app.services.mysql_repo import get_repo
from app.services import transformers as T

log = get_logger(__name__)


class SyncService:
    def __init__(self):
        self._settings = get_settings()

    # =====================================================================
    # LECTURAS / DIFFS  (nunca escriben)
    # =====================================================================
    def production_dynamo(self, production_id: int) -> Optional[dict]:
        s = self._settings
        key = {s.PRODUCTION_MONITORING_PK: production_id}
        if s.PRODUCTION_MONITORING_SK:
            if s.PRODUCTION_MONITORING_SK_VALUE_TEMPLATE:
                key[s.PRODUCTION_MONITORING_SK] = s.PRODUCTION_MONITORING_SK_VALUE_TEMPLATE.format(
                    production_id=production_id
                )
            else:
                key[s.PRODUCTION_MONITORING_SK] = production_id
        try:
            return get_dynamo().get_item(s.PRODUCTION_MONITORING_TABLE_NAME, key)
        except ClientError as exc:
            log.warning("GetItem fallo para production_monitoring, usando scan fallback: %s", exc)
            items = get_dynamo().scan(s.PRODUCTION_MONITORING_TABLE_NAME)
            for item in items:
                if item.get(s.PRODUCTION_MONITORING_PK) == production_id:
                    return item
            return None

    def production_mysql(self, production_id: int) -> Optional[dict]:
        return get_repo().get_production(production_id)

    def production_inserts(self, production_id: Optional[int] = None) -> dict:
        """Diff: que producciones existen en Dynamo y faltan/difieren en MySQL."""
        s = self._settings
        if production_id is not None:
            dyn = self.production_dynamo(production_id)
            dyn_items = [dyn] if dyn else []
        else:
            dyn_items = get_dynamo().scan(s.PRODUCTION_MONITORING_TABLE_NAME)

        dyn_ids = [d["produccion_id"] for d in dyn_items if d.get("produccion_id") is not None]
        existing = get_repo().get_production_id_map(dyn_ids)

        to_insert = [d["produccion_id"] for d in dyn_items
                     if d.get("produccion_id") not in existing]
        return {
            "total_dynamo": len(dyn_items),
            "total_mysql": len(existing),
            "pending_insert": to_insert,
            "pending_count": len(to_insert),
        }

    def productions_snapshot(self, active_only: bool = False,
                             production_id: Optional[int] = None,
                             date_to: Optional[str] = None) -> list[dict]:
        return get_repo().list_productions_filtered(
            active_only=active_only,
            production_id=production_id,
            date_to=date_to,
        )

    def scenes_dynamo(self, production_id: int) -> list[dict]:
        s = self._settings
        return get_dynamo().query(
            s.PRODUCTION_MONITORING_DETAIL_TABLE_NAME, "id", f"PROD#{production_id}"
        )

    def scenes_dynamo_grouped(self, active_only: bool = False,
                              production_id: Optional[int] = None,
                              date_from: Optional[str] = None,
                              date_to: Optional[str] = None) -> dict[int, list[dict]]:
        s = self._settings
        dates_producciones=get_repo().list_fechas_escenes_produccion(active_only=active_only,production_id=production_id,date_from=date_from,date_to=date_to)
        '''if production_id is not None:
            items = get_dynamo().query(
                s.PRODUCTION_MONITORING_DETAIL_TABLE_NAME,
                "id",
                f"PROD#{production_id}",
            )
        else:'''
        #items = get_dynamo().scan(s.PRODUCTION_MONITORING_DETAIL_TABLE_NAME)
        #print(items)
        grouped: dict[int, list[dict]] = {}
        for prod in dates_producciones:
            pid = prod.get("produccion_id","")
            fi = prod.get("fecha_inicio","")
            if pid is None:
                continue
            #print(fi)
            items_dinamo=get_dynamo().scan_by_fecha_production(table_name=s.PRODUCTION_MONITORING_DETAIL_TABLE_NAME,production_id=pid,date_from=fi,date_to=date_to)
            for dyn in items_dinamo:
                scene_date = self._parse_date(dyn.get("fecha"))
                grouped.setdefault(pid, []).append({
                    "id": dyn.get("id"),
                    "clave": dyn.get("clave"),
                    "fecha": dyn.get("fecha"),
                    "cloud_cover": dyn.get("cloud_cover"),
                    "procesado": dyn.get("procesado"),
                    "renderizado": dyn.get("renderizado"),
                    "scene_created": dyn.get("scene_created"),
                    "scene_json": dyn.get("scene_json"),
                    "scene_svg": dyn.get("scene_svg"),
                    "scene_image": dyn.get("scene_image"),
                    "preview_json": dyn.get("preview_json"),
                })
        '''for item in items:
            pid = self._extract_scene_production_id(item.get("id", ""))
            if pid is None:
                continue
            if production_id is not None and pid != production_id:
                continue
            if active_only:
                prod = self.production_mysql(pid) or {}
                if prod.get("monitoring") != 1:
                    continue
            scene_date = self._parse_date(item.get("fecha"))
            if date_from is not None and scene_date is not None and scene_date < self._parse_date(date_from):
                continue
            if date_to is not None and scene_date is not None and scene_date > self._parse_date(date_to):
                continue
            grouped.setdefault(pid, []).append({
                "id": item.get("id"),
                "clave": item.get("clave"),
                "fecha": item.get("fecha"),
                "cloud_cover": item.get("cloud_cover"),
                "procesado": item.get("procesado"),
                "renderizado": item.get("renderizado"),
                "scene_created": item.get("scene_created"),
                "scene_json": item.get("scene_json"),
                "scene_svg": item.get("scene_svg"),
                "scene_image": item.get("scene_image"),
                "preview_json": item.get("preview_json"),
            })'''
        return grouped

    def scenes_mysql(self, production_id: int) -> list[dict]:
        s3_prod_id = self._resolve_s3_prod_id(production_id)
        if s3_prod_id is None:
            return []
        return get_repo().get_scenes_by_production(s3_prod_id)

    def scenes_mysql_grouped(self, active_only: bool = False,
                             production_id: Optional[int] = None,
                             date_from: Optional[str] = None,
                             date_to: Optional[str] = None) -> dict[int, list[dict]]:
        rows = get_repo().list_scenes_grouped(
            active_only=active_only,
            production_id=production_id,
            date_from=date_from,
            date_to=date_to,
        )
        grouped: dict[int, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row["produccion_id"], []).append({
                "s3_monitoring_escena_id": row.get("s3_monitoring_escena_id"),
                "scene_name": row.get("scene_name"),
                "fecha": row.get("fecha"),
                "cloud_cover": row.get("cloud_cover"),
                "status": row.get("status"),
                "truth_tif_exists": row.get("truth_tif_exists"),
                "render_tif_exists": row.get("render_tif_exists"),
                "params_exists": row.get("params_exists"),
                "ia_exists": row.get("ia_exists"),
                "fase2_completa_at": row.get("fase2_completa_at"),
            })
        return grouped

    def scenes_mysql_snapshot(self, active_only: bool = False,
                              production_id: Optional[int] = None,
                              date_from: Optional[str] = None,
                              date_to: Optional[str] = None) -> dict[int, list[dict]]:
        rows = get_repo().list_scenes_grouped(
            active_only=active_only,
            production_id=production_id,
            date_from=date_from,
            date_to=date_to,
        )
        grouped: dict[int, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row["produccion_id"], []).append({
                "s3_monitoring_escena_id": row.get("s3_monitoring_escena_id"),
                "scene_name": row.get("scene_name"),
                "fecha": row.get("fecha"),
                "cloud_cover": row.get("cloud_cover"),
                "status": row.get("status"),
                "truth_tif_exists": row.get("truth_tif_exists"),
                "render_tif_exists": row.get("render_tif_exists"),
                "params_exists": row.get("params_exists"),
                "ia_exists": row.get("ia_exists"),
                "fase2_completa_at": row.get("fase2_completa_at"),
            })
        return grouped

    def scenes_inserts(self, production_id: int) -> dict:
        dyn = self.scenes_dynamo(production_id)
        dyn_names = {d.get("clave") for d in dyn}
        s3_prod_id = self._resolve_s3_prod_id(production_id)
        existing = set()
        if s3_prod_id is not None:
            existing = set(get_repo().get_scene_id_map(s3_prod_id).keys())
        pending = sorted(dyn_names - existing)
        return {
            "production_id": production_id,
            "total_dynamo": len(dyn_names),
            "total_mysql": len(existing),
            "pending_insert": pending,
            "pending_count": len(pending),
        }

    def scenes_inserts_grouped(self, active_only: bool = False,
                               production_id: Optional[int] = None,
                               date_from: Optional[str] = None,
                               date_to: Optional[str] = None) -> dict:
        plan, _ = self.scenes_inserts_grouped_with_dynamo(
            active_only=active_only,
            production_id=production_id,
            date_from=date_from,
            date_to=date_to,
        )
        return plan

    def scenes_inserts_grouped_with_dynamo(
        self,
        active_only: bool = False,
        production_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> tuple[dict, dict[int, list[dict]]]:
        dyn_grouped = self.scenes_dynamo_grouped(
            active_only=active_only,
            production_id=production_id,
            date_from=date_from,
            date_to=date_to,
        )
        mysql_grouped = self.scenes_mysql_snapshot(
            active_only=active_only,
            production_id=production_id,
            date_from=date_from,
            date_to=date_to,
        )
        summary = []
        total_pending = 0
        for pid, dyn_rows in dyn_grouped.items():
            dyn_names = {row.get("clave") for row in dyn_rows if row.get("clave") is not None}
            mysql_names = {row.get("scene_name") for row in mysql_grouped.get(pid, []) if row.get("scene_name") is not None}
            pending = sorted(dyn_names - mysql_names)
            total_pending += len(pending)
            summary.append({
                "production_id": pid,
                "total_dynamo": len(dyn_names),
                "total_mysql": len(mysql_names),
                "pending_insert": pending,
                "pending_count": len(pending),
            })
        return {"total_pending": total_pending, "by_production": summary}, dyn_grouped

    def scenes_inserts_grouped_for_productions(
        self,
        production_ids: list[int],
        active_only: bool = False,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        progress_cb: Optional[Any] = None,
    ) -> dict:
        summary = []
        total_pending = 0
        total = max(len(production_ids), 1)
        for index, pid in enumerate(production_ids, start=1):
            if progress_cb is not None:
                progress_cb(index, total, pid)
            dyn_grouped = self.scenes_dynamo_grouped(
                active_only=active_only,
                production_id=pid,
                date_from=date_from,
                date_to=date_to,
            )
            mysql_grouped = self.scenes_mysql_snapshot(
                active_only=active_only,
                production_id=pid,
                date_from=date_from,
                date_to=date_to,
            )
            dyn_rows = dyn_grouped.get(pid, [])
            dyn_names = {row.get("clave") for row in dyn_rows if row.get("clave") is not None}
            mysql_names = {
                row.get("scene_name")
                for row in mysql_grouped.get(pid, [])
                if row.get("scene_name") is not None
            }
            pending = sorted(dyn_names - mysql_names)
            total_pending += len(pending)
            summary.append({
                "production_id": pid,
                "total_dynamo": len(dyn_names),
                "total_mysql": len(mysql_names),
                "pending_insert": pending,
                "pending_count": len(pending),
            })
        return {"total_pending": total_pending, "by_production": summary}

    def files_inserts(self, production_id: int, scene_name: Optional[str] = None,
                      date_from: Optional[str] = None, date_to: Optional[str] = None) -> dict:
        """Indexa en MySQL los archivos que sí existen en S3 para cada escena."""
        cfg = get_runtime_config()
        if not cfg.s3_bucket:
            return {"phase": "files", "status": "validation_error",
                    "message": "MONITORING_S3_BUCKET no esta configurado."}

        s3_prod_id = self._resolve_s3_prod_id(production_id)
        if s3_prod_id is None:
            return {"phase": "files", "status": "not_found",
                    "message": f"Produccion {production_id} no esta en MySQL."}

        scene_map: Optional[dict[str, int]] = None
        if scene_name:
            scene_map = {k: v for k, v in scene_map.items() if k == scene_name}
        if not scene_map:
            return {"phase": "files", "status": "already_synced", "processed": 0}

        resolved_from, resolved_to, range_meta = self._resolve_files_date_range(
            production_id, scene_map, date_from, date_to
        )
        scene_rows = get_repo().get_scenes_filtered(s3_prod_id, resolved_from.isoformat(), resolved_to.isoformat())
        scene_rows_by_name = {row.get("scene_name"): row for row in scene_rows}

        items: list[dict] = []
        pending_total = 0
        for matched_scene, scene_id in scene_map.items():
            scene_row = scene_rows_by_name.get(matched_scene) or {}
            scene_date = self._parse_date(scene_row.get("fecha"))
            if scene_date is None or scene_date < resolved_from or scene_date > resolved_to:
                continue
            prefix = cfg.s3_phase2_files_template.format(
                production_id=production_id,
                scene_name=matched_scene,
                scene_id=scene_id,
            )
            s3_objects = get_s3().list_objects(cfg.s3_bucket, prefix)
            s3_keys = {obj["key"] for obj in s3_objects}
            mysql_files = get_repo().get_files_filtered(
                scene_id,
                [f"{prefix}/{filename}" for filename in cfg.s3_phase2_expected_files],
            )
            mysql_keys = {row.get("s3_key") for row in mysql_files if row.get("s3_key")}

            expected_keys = {f"{prefix}/{filename}" for filename in cfg.s3_phase2_expected_files}
            found_keys = expected_keys & s3_keys
            pending_keys = found_keys - mysql_keys
            indexed_keys = found_keys & mysql_keys

            pending = [{
                "s3_key": key,
                "tipo": self._expected_file_type(key.rsplit("/", 1)[-1]),
                "missing_in_mysql": True,
            } for key in sorted(pending_keys)]

            pending_total += len(pending)
            items.append({
                "scene_name": matched_scene,
                "scene_id": scene_id,
                "fecha": scene_row.get("fecha"),
                "prefix": prefix,
                "total_s3": len(s3_objects),
                "total_mysql": len(mysql_files),
                "found_in_s3": len(found_keys),
                "already_indexed": len(indexed_keys),
                "pending_count": len(pending),
                "pending": pending,
            })

        return {
            "phase": "files",
            "production_id": production_id,
            "date_from": resolved_from.isoformat(),
            "date_to": resolved_to.isoformat(),
            "range_meta": range_meta,
            "pending_count": pending_total,
            "by_scene": items,
        }

    def files_mysql_snapshot(self, production_id: Optional[int] = None, active_only: bool = False,
                             date_from: Optional[str] = None,
                             date_to: Optional[str] = None) -> dict:
        import time
        started_at = time.perf_counter()
        grouped: dict[int, dict[str, Any]] = {}
        total_files = 0
        process: list[dict[str, Any]] = []
        log.info(
            "files_mysql_snapshot inicio | production_id=%s active_only=%s date_from=%s date_to=%s",
            production_id, active_only, date_from, date_to,
        )
        process.append({
            "step": "start",
            "production_id": production_id,
            "active_only": active_only,
            "date_from": date_from,
            "date_to": date_to,
        })
        query_started = time.perf_counter()
        process.append({
            "step": "cte_mysql_query",
            "status": "running",
            "cte": "ru -> rules -> p -> es -> d -> ari",
        })
        print("INCIA PROCESO")
        rows = get_repo().list_files_grouped(
            active_only=active_only,
            production_id=production_id,
            date_from=date_from,
            date_to=date_to,
        )
        print("Termino consulta")
        log.info(
            "files_mysql_snapshot consulta mysql terminada | production_id=%s rows=%s elapsed_ms=%.2f",
            production_id, len(rows), (time.perf_counter() - query_started) * 1000,
        )
        process.append({
            "step": "cte_mysql_query",
            "status": "done",
            "rows": len(rows),
            "elapsed_ms": round((time.perf_counter() - query_started) * 1000, 2),
            "cte": "ru -> rules -> p -> es -> d -> ari",
        })
        print("INICIA RECORRIDO")
        for row in rows:
            scene_name = row.get("scene_name")
            if not scene_name:
                continue
            pid = row.get("produccion_id")
            scene_id = row.get("s3_monitoring_escena_id")
            grouped.setdefault(pid, {})
            grouped[pid].setdefault(scene_name, {
                "scene_id": scene_id,
                "fecha": row.get("fecha"),
                "files": [],
                "file_count": 0,
            })
            grouped[pid][scene_name]["files"].append(row)
            grouped[pid][scene_name]["file_count"] += 1
            total_files += 1
        process.append({
            "step": "group_by_scene",
            "production_count": len(grouped),
            "scene_count": sum(len(v) for v in grouped.values()),
            "file_count": total_files,
        })
        print("FIN RECORRIDO")
        return {
            "production_id": production_id,
            "by_scene": grouped,
            "total_scenes": sum(len(v) for v in grouped.values()),
            "total_files": total_files,
            "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "process": process,
        }

    def files_s3_snapshot(self, production_id: Optional[int] = None, active_only: bool = False,
                          date_from: Optional[str] = None,
                          date_to: Optional[str] = None) -> dict:
        import time
        started_at = time.perf_counter()
        cfg = get_runtime_config()
        if not cfg.s3_bucket:
            return {"phase": "files", "status": "validation_error",
                    "message": "MONITORING_S3_BUCKET no esta configurado."}
        scene_targets = get_repo()._target_production_idsyscene_mysql_toarchivos(
            active_only=active_only,
            production_id=production_id,
            date_from=date_from,
            date_to=date_to,
        )
        grouped: dict[int, dict[str, Any]] = {}
        total_objects = 0
        process: list[dict[str, Any]] = []
        log.info(
            "files_s3_snapshot inicio | production_id=%s active_only=%s date_from=%s date_to=%s scene_targets=%s",
            production_id, active_only, date_from, date_to, len(scene_targets),
        )
        process.append({
            "step": "start",
            "production_id": production_id,
            "active_only": active_only,
            "date_from": date_from,
            "date_to": date_to,
            "scene_targets": len(scene_targets),
        })
        for target in scene_targets:
            pid = target["produccion_id"]
            scene_name = target["scene_name"]
            scene_id = target.get("s3_monitoring_escena_id")
            scene_row = target
            scene_group = grouped.setdefault(pid, {})
            prefix = cfg.s3_phase2_files_template.format(
                production_id=pid,
                scene_name=scene_name,
                scene_id=scene_id,
            )
            list_started = time.perf_counter()
            s3_objects = get_s3().list_objects(cfg.s3_bucket, prefix)
            log.info(
                "files_s3_snapshot s3 listado | production_id=%s scene_id=%s scene_name=%s objects=%s elapsed_ms=%.2f",
                pid, scene_id, scene_name, len(s3_objects), (time.perf_counter() - list_started) * 1000,
            )
            process.append({
                "step": "list_s3_objects",
                "production_id": pid,
                "scene_id": scene_id,
                "scene_name": scene_name,
                "object_count": len(s3_objects),
                "elapsed_ms": round((time.perf_counter() - list_started) * 1000, 2),
            })
            scene_group[scene_name] = {
                "scene_id": scene_id,
                "fecha": scene_row.get("fecha"),
                "prefix": prefix,
                "objects": s3_objects,
                "object_count": len(s3_objects),
            }
            total_objects += len(s3_objects)
        return {
            "production_id": production_id,
            "by_scene": grouped,
            "total_scenes": sum(len(v) for v in grouped.values()),
            "total_objects": total_objects,
            "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "process": process,
        }

    def _expected_file_type(self, filename: str) -> str:
        if filename.endswith(".tif"):
            return "truth_tif" if "render" not in filename else "render_tif"
        if filename.endswith(".json"):
            return "ia" if filename.endswith("multiband.ia.json") else "params"
        if filename.endswith(".png"):
            return "image"
        return "otro"

    def _parse_date(self, value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return datetime.strptime(value.strip(), "%Y-%m-%d").date()
            except ValueError:
                return None
        return None

    def _extract_scene_production_id(self, pk: str) -> Optional[int]:
        if not pk or "#" not in pk:
            return None
        try:
            return int(pk.split("#", 1)[1])
        except (ValueError, IndexError):
            return None

    def _resolve_files_date_range(
        self,
        production_id: int,
        scene_map: dict[str, int],
        date_from: Optional[str],
        date_to: Optional[str],
    ) -> tuple[date, date, dict[str, Any]]:
        today = date.today()
        prod_mysql = self.production_mysql(production_id) or {}
        scene_rows = get_repo().get_scenes_by_production(self._resolve_s3_prod_id(production_id) or production_id)
        phase2_date = self._parse_date(prod_mysql.get("fase2_completa_at"))

        parsed_from = self._parse_date(date_from)
        parsed_to = self._parse_date(date_to)

        if parsed_from and not parsed_to:
            resolved_from = min(parsed_from, today)
            resolved_to = today
        elif not parsed_from and parsed_to:
            resolved_to = parsed_to
            max_scene_date = None
            for row in scene_rows:
                if row.get("scene_name") not in scene_map:
                    continue
                scene_date = self._parse_date(row.get("fecha"))
                if scene_date is not None and (max_scene_date is None or scene_date > max_scene_date):
                    max_scene_date = scene_date
            resolved_from = max_scene_date or self._parse_date(prod_mysql.get("fecha_plantacion")) or today
        elif parsed_from and parsed_to:
            resolved_from = parsed_from
            resolved_to = parsed_to
        else:
            scene_dates = [
                self._parse_date(row.get("fecha"))
                for row in scene_rows
                if row.get("scene_name") in scene_map
            ]
            scene_dates = [d for d in scene_dates if d is not None]
            default_from = phase2_date or self._parse_date(prod_mysql.get("fecha_plantacion")) or today
            resolved_from = phase2_date or (min(scene_dates) if scene_dates else default_from)
            resolved_to = max(scene_dates) if scene_dates else today

        if resolved_from > resolved_to:
            resolved_from = resolved_to

        return resolved_from, resolved_to, {
            "input_from": date_from,
            "input_to": date_to,
            "today": today.isoformat(),
        }

    def files_inserts_global(self, production_id: Optional[int] = None,
                             active_only: bool = False,
                             date_from: Optional[str] = None,
                             date_to: Optional[str] = None) -> dict:
        mysql_snapshot = self.files_mysql_snapshot(
            production_id=production_id,
            active_only=active_only,
            date_from=date_from,
            date_to=date_to,
        )
        s3_snapshot = self.files_s3_snapshot(
            production_id=production_id,
            active_only=active_only,
            date_from=date_from,
            date_to=date_to,
        )

        mysql_by_prod = mysql_snapshot.get("by_scene", {}) or {}
        s3_by_prod = s3_snapshot.get("by_scene", {}) or {}
        prod_ids = sorted(set(s3_by_prod.keys()))

        out: list[dict] = []
        total_pending = 0
        for pid in prod_ids:
            mysql_scenes = mysql_by_prod.get(pid, {}) or {}
            s3_scenes = s3_by_prod.get(pid, {}) or {}
            scene_names = sorted(set(s3_scenes.keys()))
            scenes_diff: list[dict[str, Any]] = []
            pending_by_prod = 0

            for scene_name in scene_names:
                mysql_scene = mysql_scenes.get(scene_name, {}) or {}
                s3_scene = s3_scenes.get(scene_name, {}) or {}
                mysql_keys = {
                    row.get("s3_key")
                    for row in mysql_scene.get("files", [])
                    if row.get("s3_key")
                }
                s3_keys = {
                    obj.get("key")
                    for obj in s3_scene.get("objects", [])
                    if obj.get("key")
                }
                pending_keys = sorted(s3_keys - mysql_keys)
                indexed_keys = sorted(s3_keys & mysql_keys)
                pending = [{
                    "s3_key": key,
                    "tipo": self._expected_file_type(key.rsplit("/", 1)[-1]),
                    "missing_in_mysql": True,
                } for key in pending_keys]

                pending_by_prod += len(pending)
                if(len(pending)>0):
                    scenes_diff.append({
                        "scene_name": scene_name,
                        "scene_id": s3_scene.get("scene_id") or mysql_scene.get("scene_id"),
                        "fecha": s3_scene.get("fecha") or mysql_scene.get("fecha"),
                        "prefix": s3_scene.get("prefix") or mysql_scene.get("prefix"),
                        "total_s3": len(s3_scene.get("objects", [])),
                        "total_mysql": len(mysql_scene.get("files", [])),
                        "found_in_s3": len(s3_keys),
                        "already_indexed": len(indexed_keys),
                        "pending_count": len(pending),
                        "pending": pending,
                    })

            total_pending += pending_by_prod
            if(pending_by_prod>0):
                out.append({
                    "production_id": pid,
                    "pending_count": pending_by_prod,
                    "by_scene": scenes_diff,
                })

        return {"phase": "files", "total_pending": total_pending, "by_production": out}

    # =====================================================================
    # FASE 4: GEOMETRÍA / TILE
    # =====================================================================
    def geometry_snapshot(self, production_id: int) -> dict:
        """Devuelve los campos geométricos ya persistidos para una producción."""
        dyn = self.production_dynamo(production_id) or {}
        mysql = self.production_mysql(production_id) or {}
        return {
            "production_id": production_id,
            "dynamo": {
                "pbox": dyn.get("pbox"),
                "polygon_bbox": (dyn.get("pbox") or {}).get("puntos_bbox"),
            },
            "mysql": {
                "pbox": mysql.get("pbox"),
                "polygon_bbox": mysql.get("polygon_bbox"),
                "tile_bbox": mysql.get("tile_bbox"),
                "tile_center_lat": mysql.get("tile_center_lat"),
                "tile_center_lon": mysql.get("tile_center_lon"),
                "tile_edge_meters": mysql.get("tile_edge_meters"),
            },
        }

    def geometry_plan(self, production_id: Optional[int] = None,
                      active_only: bool = False) -> dict:
        """Producciones con geometría incompleta en MySQL."""
        inserts = self.geometry_inserts(production_id, active_only)
        pending = inserts["pending"]
        skipped: list[dict] = []
        if production_id is not None:
            current = get_repo().get_production_with_polygon(production_id=production_id,active_only=active_only) or {}
            if current and not pending:
                skipped.append({
                    "production_id": production_id,
                    "reason": "already_synced",
                    "tile_bbox": current.get("tile_bbox"),
                    "tile_center_lat": current.get("tile_center_lat"),
                    "tile_center_lon": current.get("tile_center_lon"),
                    "poligono": current.get("poligono"),
                })
        return {"phase": "geometry", "pending_count": len(pending), "pending": pending, "skipped": skipped}
    def sync_geometry_item(
        self,
        item,
        dry_run : bool = True
    )->dict:
        print(item)
        pbox_obj = item.get("pbox") or {}
        if isinstance(pbox_obj, str):
            try:
                pbox_obj = json.loads(pbox_obj)
            except json.JSONDecodeError:
                pbox_obj = {}
        center = T.geometry_center_from_pbox({"pbox": pbox_obj})
        poligono_json = T.polygon_text_to_json_array(item.get("poligono"))
        tile_edge_meters = 1000
        tile = T.geometry_tile_from_center(
            center.get("tile_center_lat"),
            center.get("tile_center_lon"),
            tile_edge_meters,
        )
        row = {
            "produccion_id": item.get("production_id"),
            "pbox": item.get("pbox"),
            "polygon_bbox": item.get("polygon_bbox"),
            "poligono": poligono_json,
            "tile_bbox": tile.get("tile_bbox"),
            "tile_center_lat": center.get("tile_center_lat"),
            "tile_center_lon": center.get("tile_center_lon"),
            "tile_edge_meters": tile_edge_meters,
        }
        diff = item
        if not diff:
            return {
                "phase": "geometry",
                "status": "already_synced",
                "dry_run": dry_run,
                "production_id": item.get("production_id"),
                "updates": row,
                "reasons": [],
            }

        if dry_run:
            return {
                "phase": "geometry",
                "status": "simulated",
                "dry_run": True,
                "production_id": item.get("production_id"),
                "updates": row,
                "reasons": diff.get("reasons", []),
            }

        written = get_repo().update_production_geometry(row)
        return {
            "phase": "geometry",
            "status": "ok",
            "dry_run": False,
            "production_id": item.get("production_id"),
            "written": written,
            "updates": row,
            "reasons": diff.get("reasons", []),
        }
    def sync_geometry(
        self,
        production_id: int,
        dry_run: bool,
        active_only: bool = False,
        inserts: Optional[dict] = None,
    ) -> dict:
        """Persiste únicamente el tile faltante desde el pbox ya guardado."""
        inserts = inserts or self.geometry_inserts(production_id=production_id)
        pending = inserts.get("pending", []) if isinstance(inserts, dict) else list(inserts or [])
        if not pending:
            current = get_repo().get_production_with_polygon(production_id=production_id,active_only=active_only) or {}
            return {
                "phase": "geometry",
                "status": "already_synced",
                "dry_run": dry_run,
                "production_id": production_id,
                "updates": {
                    "tile_bbox": current.get("tile_bbox"),
                    "tile_center_lat": current.get("tile_center_lat"),
                    "tile_center_lon": current.get("tile_center_lon"),
                    "poligono": current.get("poligono"),
                },
                "reasons": [],
            }
        current = get_repo().get_production_with_polygon(production_id=production_id,active_only=active_only) or {}
        if not current:
            return {
                "phase": "geometry",
                "status": "not_found",
                "message": f"Produccion {production_id} no existe en MySQL.",
            }
        pbox_obj = current.get("pbox") or {}
        if isinstance(pbox_obj, str):
            try:
                pbox_obj = json.loads(pbox_obj)
            except json.JSONDecodeError:
                pbox_obj = {}
        center = T.geometry_center_from_pbox({"pbox": pbox_obj})
        poligono_json = T.polygon_text_to_json_array(current.get("poligono"))
        tile_edge_meters = 1000
        tile = T.geometry_tile_from_center(
            center.get("tile_center_lat"),
            center.get("tile_center_lon"),
            tile_edge_meters,
        )
        row = {
            "produccion_id": production_id,
            "pbox": current.get("pbox"),
            "polygon_bbox": current.get("polygon_bbox"),
            "poligono": poligono_json,
            "tile_bbox": tile.get("tile_bbox"),
            "tile_center_lat": center.get("tile_center_lat"),
            "tile_center_lon": center.get("tile_center_lon"),
            "tile_edge_meters": tile_edge_meters,
        }
        diff = next((item for item in pending if item.get("production_id") == production_id), pending[0] if pending else {})
        if not diff:
            return {
                "phase": "geometry",
                "status": "already_synced",
                "dry_run": dry_run,
                "production_id": production_id,
                "updates": row,
                "reasons": [],
            }

        if dry_run:
            return {
                "phase": "geometry",
                "status": "simulated",
                "dry_run": True,
                "production_id": production_id,
                "updates": row,
                "reasons": diff.get("reasons", []),
            }

        written = get_repo().update_production_geometry(row)
        return {
            "phase": "geometry",
            "status": "ok",
            "dry_run": False,
            "production_id": production_id,
            "written": written,
            "updates": row,
            "reasons": diff.get("reasons", []),
        }

    def sync_tile(self, production_id: int, dry_run: bool, job: Optional[Any] = None,active_only: bool = False) -> dict:
        """Fase 5: completa sólo el tile faltante en MySQL."""
        return self.sync_geometry(production_id, dry_run)

    def geometry_inserts(self, production_id: Optional[int] = None,
                         active_only: bool = False
    ) -> list[dict]:
        """Lista producciones con geometría incompleta para el microservicio consumidor."""
        lista = get_repo().get_production_with_polygon(production_id=production_id,active_only=active_only) or {}
        pending: list[dict] = []
        print(lista)
        for mysql in lista:
            print(mysql)
            needs_tile = any(mysql.get(key) is None for key in (
                "tile_bbox",
                "tile_center_lat",
                "tile_center_lon",
                "poligono",
            ))
            if needs_tile:
                pending.append({
                    "production_id": mysql.get("produccion_id"),
                    "has_tile_bbox": mysql.get("tile_bbox") is not None,
                    "has_pbox": mysql.get("pbox") is not None,
                    "has_tile_center": mysql.get("tile_center_lat") is not None and mysql.get("tile_center_lon") is not None,
                    "has_poligono": mysql.get("poligono") is not None,
                    "has_polygon_bbox": mysql.get("polygon_bbox") is not None,
                    
                    "tile_edge_meters": mysql.get("tile_edge_meters"),

                    "tile_bbox": mysql.get("tile_bbox",False),
                    "pbox": mysql.get("pbox",None),
                    "tile_center": f"{mysql.get('tile_center_lat','lat')},{mysql.get('tile_center_lon','lng')}",
                    "poligono": mysql.get("poligono"),
                    "polygon_bbox": mysql.get("polygon_bbox"),
                })
        return {"phase": "geometry", "pending_count": len(pending), "pending": pending}
    # =====================================================================
    # FASE: PRODUCCIONES
    # =====================================================================
    def sync_productions(self, production_id: Optional[int], dry_run: bool,
                         active_only: bool = False) -> dict:
        s = self._settings
        cfg = get_runtime_config()
        diff = self.production_inserts(production_id)
        pending_ids = set(diff.get("pending_insert", []))
        if not pending_ids:
            return {
                "phase": "productions",
                "status": "already_synced",
                "processed": 0,
                "diff": diff,
            }

        if production_id is not None:
            dyn_items = [self.production_dynamo(production_id)]
        else:
            dyn_items = get_dynamo().scan(s.PRODUCTION_MONITORING_TABLE_NAME)
            if active_only:
                dyn_items = [i for i in dyn_items if str(i.get("estatus", "")).upper() == "OPEN"]

        plan: list[dict] = []
        for item in dyn_items:
            if not item or item.get("produccion_id") not in pending_ids:
                continue
            desired = T.production_dynamo_to_mysql(item, cfg.sync_prefix_template)
            plan.append({
                "production_id": desired["produccion_id"],
                "updates": desired,
            })

        written = 0
        rows = [p["updates"] for p in plan]
        if not dry_run:
            for i in range(0, len(rows), cfg.scheduler_batch_size or 20):
                batch = rows[i:i + (cfg.scheduler_batch_size or 20)]
                written += get_repo().upsert_productions(batch)

        return {
            "phase": "productions",
            "status": "simulated" if dry_run else "ok",
            "dry_run": dry_run,
            "candidates": len(plan),
            "written": written,
            "production_ids": [r["production_id"] for r in plan],
            "pending": plan,
            "diff": diff,
        }

    def sync_productions_from_plan(
        self,
        plan: dict,
        dry_run: bool,
        active_only: bool = False,
    ) -> dict:
        cfg = get_runtime_config()
        pending_ids = list(plan.get("pending_insert", []) or [])
        if not pending_ids:
            return {
                "phase": "productions",
                "status": "already_synced",
                "processed": 0,
                "plan": plan,
            }

        rows: list[dict] = []
        pending_details: list[dict] = []
        for production_id in pending_ids:
            item = self.production_dynamo(production_id)
            if not item:
                continue
            if active_only and str(item.get("estatus", "")).upper() != "OPEN":
                continue
            desired = T.production_dynamo_to_mysql(item, cfg.sync_prefix_template)
            rows.append(desired)
            pending_details.append({
                "production_id": desired["produccion_id"],
                "updates": desired,
            })

        written = 0
        if not dry_run:
            for i in range(0, len(rows), cfg.scheduler_batch_size or 20):
                batch = rows[i:i + (cfg.scheduler_batch_size or 20)]
                written += get_repo().upsert_productions(batch)

        return {
            "phase": "productions",
            "status": "simulated" if dry_run else "ok",
            "dry_run": dry_run,
            "candidates": len(rows),
            "written": written,
            "production_ids": [row["produccion_id"] for row in rows],
            "pending": pending_details,
            "plan": plan,
        }

    # =====================================================================
    # FASE: ESCENAS
    # =====================================================================
    def sync_scenes(self, production_id: int, dry_run: bool,
                    include_scene_json: bool = False,
                    scene_name: Optional[str] = None) -> dict:
        cfg = get_runtime_config()
        s3_prod_id = self._resolve_s3_prod_id(production_id)
        if s3_prod_id is None:
            return {
                "phase": "scenes", "status": "not_found",
                "message": f"Produccion {production_id} no existe en MySQL. "
                           f"Sincroniza producciones primero.",
            }

        dyn_scenes = self.scenes_dynamo(production_id)
        if scene_name:
            dyn_scenes = [d for d in dyn_scenes if d.get("clave") == scene_name]

        rows = [T.scene_dynamo_to_mysql(d, include_scene_json) for d in dyn_scenes]
        if not rows:
            return {"phase": "scenes", "status": "already_synced", "processed": 0}

        existing_map = get_repo().get_scene_id_map(s3_prod_id)
        pending = [r for r in rows if r.get("scene_name") not in existing_map]
        if not pending:
            return {
                "phase": "scenes",
                "status": "already_synced",
                "dry_run": dry_run,
                "production_id": production_id,
                "candidates": 0,
                "written": 0,
                "scenes": [],
            }

        written = 0
        latest_scene_date = None
        if not dry_run:
            for i in range(0, len(pending), cfg.scheduler_batch_size or 20):
                batch = pending[i:i + (cfg.scheduler_batch_size or 20)]
                written += get_repo().insert_scenes_only(s3_prod_id, batch)
            scene_dates = [
                self._parse_date(row.get("fecha"))
                for row in pending
                if self._parse_date(row.get("fecha")) is not None
            ]
            if scene_dates:
                latest_scene_date = max(scene_dates).isoformat()
                get_repo().update_production_scene_sync_date(production_id, latest_scene_date)

        return {
            "phase": "scenes",
            "status": "simulated" if dry_run else "ok",
            "dry_run": dry_run,
            "production_id": production_id,
            "candidates": len(pending),
            "written": written,
            "scenes": [r["scene_name"] for r in pending],
            "latest_scene_date": latest_scene_date,
        }

    def sync_scenes_from_plan(
        self,
        production_id: int,
        scene_names: list[str],
        dry_run: bool,
        include_scene_json: bool = False,
        scenes_data: Optional[list[dict]] = None,
        sync_date_to: Optional[str] = None,
    ) -> dict:
        cfg = get_runtime_config()
        s3_prod_id = self._resolve_s3_prod_id(production_id)
        if s3_prod_id is None:
            return {
                "phase": "scenes",
                "status": "not_found",
                "message": f"Produccion {production_id} no existe en MySQL. Sincroniza producciones primero.",
            }

        existing_map = get_repo().get_scene_id_map(s3_prod_id)
        pending_names = [name for name in scene_names if name not in existing_map]
        if not pending_names:
            return {
                "phase": "scenes",
                "status": "already_synced",
                "dry_run": dry_run,
                "production_id": production_id,
                "candidates": 0,
                "written": 0,
                "scenes": [],
            }

        dyn_scenes = scenes_data if scenes_data is not None else self.scenes_dynamo(production_id)
        dyn_rows = [
            T.scene_dynamo_to_mysql(scene, include_scene_json)
            for scene in dyn_scenes
            if scene.get("clave") in set(pending_names)
        ]
        if not dyn_rows:
            return {
                "phase": "scenes",
                "status": "already_synced",
                "dry_run": dry_run,
                "production_id": production_id,
                "candidates": 0,
                "written": 0,
                "scenes": [],
            }

        written = 0
        latest_scene_date = None
        if not dry_run:
            for i in range(0, len(dyn_rows), cfg.scheduler_batch_size or 20):
                batch = dyn_rows[i:i + (cfg.scheduler_batch_size or 20)]
                written += get_repo().insert_scenes_only(s3_prod_id, batch)
            scene_dates = [
                self._parse_date(row.get("fecha"))
                for row in dyn_rows
                if self._parse_date(row.get("fecha")) is not None
            ]
            if scene_dates:
                latest_scene_date = max(scene_dates).isoformat()
                get_repo().update_production_scene_sync_date(
                    production_id,
                    latest_scene_date,
                    sync_date_to,
                )

        return {
            "phase": "scenes",
            "status": "simulated" if dry_run else "ok",
            "dry_run": dry_run,
            "production_id": production_id,
            "candidates": len(dyn_rows),
            "written": written,
            "scenes": [r["scene_name"] for r in dyn_rows],
            "latest_scene_date": latest_scene_date,
            "ultima_sincronizacion_target": sync_date_to or "NOW()",
        }

    # =====================================================================
    # FASE 2: ARCHIVOS DERIVADOS (S3 -> MySQL)
    # =====================================================================
    def sync_files(self, production_id: int, dry_run: bool,
                   scene_name: Optional[str] = None,
                   date_to: Optional[str] = None) -> dict:
        cfg = get_runtime_config()
        if not cfg.s3_bucket:
            return {"phase": "files", "status": "validation_error",
                    "message": "MONITORING_S3_BUCKET no esta configurado."}

        s3_prod_id = self._resolve_s3_prod_id(production_id)
        if s3_prod_id is None:
            return {"phase": "files", "status": "not_found",
                    "message": f"Produccion {production_id} no esta en MySQL."}

        scene_map = get_repo().get_scene_id_map(s3_prod_id)
        if scene_name:
            scene_map = {k: v for k, v in scene_map.items() if k == scene_name}
        if not scene_map:
            return {"phase": "files", "status": "already_synced", "processed": 0}

        scene_targets = list(scene_map.keys())

        file_rows: list[dict] = []
        tipos_por_escena: dict[int, set[str]] = {}

        for matched_scene in scene_targets:
            scene_id = scene_map.get(matched_scene)
            if scene_id is None:
                continue

            prefix = cfg.s3_phase2_files_template.format(
                production_id=production_id,
                scene_name=matched_scene,
                scene_id=scene_id,
            )
            objects = get_s3().list_objects(cfg.s3_bucket, prefix)

            # Agrupar objetos por escena segun el prefijo de la escena
            for obj in objects:
                key = obj["key"]
                info = T.classify_s3_file(key)
                if info["tipo"] == "unknown" and not key.lower().endswith(".png"):
                    continue

                json_content = None
                if info["tipo"] in ("params", "json") and not dry_run:
                    json_content_obj = get_s3().get_json(cfg.s3_bucket, key)
                    if json_content_obj is not None:
                        import json as _json
                        json_content = _json.dumps(json_content_obj, ensure_ascii=False)

                row = T.file_s3_to_mysql(obj, cfg.s3_bucket, scene_id, json_content)
                file_rows.append(row)
                tipos_por_escena.setdefault(scene_id, set()).add(row["tipo"])

        if not file_rows:
            return {"phase": "files", "status": "already_synced", "processed": 0}

        written = 0
        flags_updated = 0
        if not dry_run:
            for i in range(0, len(file_rows), cfg.scheduler_batch_size or 20):
                batch = file_rows[i:i + (cfg.scheduler_batch_size or 20)]
                written += get_repo().upsert_files(batch)
            for scene_id, tipos in tipos_por_escena.items():
                flags = T.file_flags_from_tipos(tipos)
                flags_updated += get_repo().update_scene_file_flags(scene_id, flags)
            if written > 0:
                phase2_date = date_to or datetime.now(ZoneInfo("America/Mexico_City")).date().isoformat()
                get_repo().update_production_phase2_date(production_id, phase2_date)

        return {
            "phase": "files",
            "status": "simulated" if dry_run else "ok",
            "dry_run": dry_run,
            "production_id": production_id,
            "files_candidates": len(file_rows),
            "files_written": written,
            "scenes_flagged": flags_updated,
        }

    def sync_files_from_plan(
        self,
        production_id: int,
        scene_items: list[dict],
        dry_run: bool,
        date_to: Optional[str] = None,
    ) -> dict:
        cfg = get_runtime_config()
        if not cfg.s3_bucket:
            return {"phase": "files", "status": "validation_error",
                    "message": "MONITORING_S3_BUCKET no esta configurado."}

        s3_prod_id = self._resolve_s3_prod_id(production_id)
        if s3_prod_id is None:
            return {"phase": "files", "status": "not_found",
                    "message": f"Produccion {production_id} no esta en MySQL."}

        scene_map = get_repo().get_scene_id_map(s3_prod_id)
        file_rows: list[dict] = []
        tipos_por_escena: dict[int, set[str]] = {}
        total_pending = 0

        for scene_item in scene_items:
            scene_name = scene_item.get("scene_name")
            if not scene_name:
                continue
            scene_id = scene_item.get("scene_id")
            if scene_id is None:
                if scene_map is None:
                    scene_map = get_repo().get_scene_id_map(s3_prod_id)
                scene_id = scene_map.get(scene_name)
            if scene_id is None:
                continue

            pending_keys = {
                row.get("s3_key")
                for row in scene_item.get("pending", [])
                if row.get("s3_key")
            }
            if not pending_keys:
                continue

            total_pending += len(pending_keys)

            for key in sorted(pending_keys):
                info = T.classify_s3_file(key)
                if info["tipo"] == "unknown" and not key.lower().endswith(".png"):
                    continue

                json_content = None
                if info["tipo"] in ("params", "json") and not dry_run:
                    json_content_obj = get_s3().get_json(cfg.s3_bucket, key)
                    if json_content_obj is not None:
                        import json as _json
                        json_content = _json.dumps(json_content_obj, ensure_ascii=False)

                obj = {
                    "key": key,
                    "size": scene_item.get("size_bytes"),
                    "last_modified": scene_item.get("last_modified"),
                }
                row = T.file_s3_to_mysql(obj, cfg.s3_bucket, scene_id, json_content)
                file_rows.append(row)
                tipos_por_escena.setdefault(scene_id, set()).add(row["tipo"])

        if not file_rows:
            return {
                "phase": "files",
                "status": "already_synced",
                "dry_run": dry_run,
                "production_id": production_id,
                "files_candidates": 0,
                "files_written": 0,
                "scenes_flagged": 0,
            }

        written = 0
        flags_updated = 0
        if not dry_run:
            for i in range(0, len(file_rows), cfg.scheduler_batch_size or 20):
                batch = file_rows[i:i + (cfg.scheduler_batch_size or 20)]
                written += get_repo().upsert_files(batch)
            for scene_id, tipos in tipos_por_escena.items():
                flags = T.file_flags_from_tipos(tipos)
                flags_updated += get_repo().update_scene_file_flags(scene_id, flags)
            phase2_date = date_to or datetime.now(ZoneInfo("America/Mexico_City")).date().isoformat()
            get_repo().update_production_phase2_date(production_id, phase2_date)

        return {
            "phase": "files",
            "status": "simulated" if dry_run else "ok",
            "dry_run": dry_run,
            "production_id": production_id,
            "files_candidates": len(file_rows),
            "files_written": written,
            "scenes_flagged": flags_updated,
            "pending_sources": total_pending,
        }

    # =====================================================================
    # FASE 3: IA (S3 multiband.ia.json -> MySQL)
    # =====================================================================
    def sync_ia(self, production_id: Optional[int], dry_run: bool) -> dict:
        cfg = get_runtime_config()
        if not cfg.s3_bucket:
            return {"phase": "ia", "status": "validation_error",
                    "message": "MONITORING_S3_BUCKET no esta configurado."}

        ia_pending_rows = get_repo().list_ia_pending(production_id)
        if not ia_pending_rows:
            return {"phase": "ia", "status": "already_synced", "processed": 0}

        ia_rows: list[dict] = []
        flag_updates: list[tuple] = []

        for ia_file in ia_pending_rows:
            scene_id = ia_file.get("s3_monitoring_escena_id")
            if not scene_id:
                continue
            ia = None
            json_content = ia_file.get("json_content")
            if json_content:
                try:
                    ia = json.loads(json_content) if isinstance(json_content, str) else json_content
                except Exception:  # noqa: BLE001
                    ia = None
            if ia is None:
                ia = get_s3().get_json(cfg.s3_bucket, ia_file["s3_key"])
            if not ia:
                continue

            row = T.ia_json_to_mysql(ia, scene_id)
            ia_rows.append(row)
            flag_updates.append((scene_id, row.get("riesgo_nivel"), row.get("fecha_analisis")))

        if not ia_rows:
            return {"phase": "ia", "status": "already_synced", "processed": 0}

        written = 0
        if not dry_run:
            for i in range(0, len(ia_rows), cfg.scheduler_batch_size or 20):
                batch = ia_rows[i:i + (cfg.scheduler_batch_size or 20)]
                written += get_repo().upsert_ia(batch)
            for scene_id, nivel, fecha in flag_updates:
                get_repo().update_scene_ia_flag(scene_id, nivel, fecha)

        return {
            "phase": "ia",
            "status": "simulated" if dry_run else "ok",
            "dry_run": dry_run,
            "ia_candidates": len(ia_rows),
            "ia_written": written,
        }

    def ia_pending(self, production_id: Optional[int] = None) -> dict:
        """Escenas con archivo IA indexado pero sin resumen IA."""
        pending = [
            {
                "production_id": row.get("produccion_id"),
                "scene_name": row.get("scene_name"),
                "s3_key": row.get("s3_key"),
                "scene_id": row.get("s3_monitoring_escena_id"),
            }
            for row in get_repo().list_ia_pending(production_id)
        ]
        return {"pending_count": len(pending), "pending": pending}

    # =====================================================================
    # EJECUCION MAESTRA
    # =====================================================================
    def sync_full(self, production_id: Optional[int], dry_run: bool,
                  active_only: bool = False, include_scene_json: bool = False,
                  job: Optional[Any] = None) -> dict:
        def _progress(percent: int, note: str) -> None:
            if job is not None:
                try:
                    from app.services.job_manager import get_job_manager
                    get_job_manager().set_progress(job.job_id, percent, note)
                except Exception:
                    pass

        results: dict[str, Any] = {
            "dry_run": dry_run,
            "active_only": active_only,
            "production_id": production_id,
        }
        _progress(0, "Iniciando sync full")

        _progress(5, "Etapa 1/5: producciones - buscando candidatos")
        productions_plan = self.production_inserts(production_id)
        results["productions"] = self.sync_productions_from_plan(productions_plan, dry_run, active_only)
        _progress(20, f"Etapa 1/5: producciones - terminada ({results['productions'].get('status', 'ok')})")

        _progress(21, "Etapa 2/5: escenas - obteniendo producciones objetivo")
        prod_ids = self._target_production_ids(production_id, active_only)
        _progress(22, f"Etapa 2/5: escenas - {len(prod_ids)} producciones objetivo")
        scenes_summary = []
        _progress(23, "Etapa 2/5: escenas - construyendo plan Dynamo vs MySQL")
        scenes_plan = self.scenes_inserts_grouped_for_productions(
            prod_ids,
            active_only=active_only,
            progress_cb=lambda index, total, pid: _progress(
                23 + int((index / max(total, 1)) * 2),
                f"Etapa 2/5: escenas - comparando produccion {pid} ({index}/{total})",
            ),
        )
        _progress(24, f"Etapa 2/5: escenas - plan listo ({scenes_plan.get('total_pending', 0)} escenas pendientes)")
        scenes_pending_by_production = {
            item["production_id"]: item["pending_insert"]
            for item in scenes_plan.get("by_production", [])
            if item.get("pending_count", 0) > 0
        }
        total_scene_targets = max(sum(len(v) for v in scenes_pending_by_production.values()), 1)
        scene_counter = 0
        _progress(25, f"Etapa 2/5: escenas - pendientes {total_scene_targets}")
        for pid in prod_ids:
            if job is not None and getattr(job, "cancel_requested", False):
                results["cancelled_at"] = "Etapa 2/5: escenas"
                return results
            pending_scenes = set(scenes_pending_by_production.get(pid, []))
            if not pending_scenes:
                continue
            dyn_scenes = self.scenes_dynamo_grouped(
                active_only=active_only,
                production_id=pid if production_id is None else production_id,
            ).get(pid, [])
            scene_counter += len(pending_scenes)
            _progress(25 + int((scene_counter / total_scene_targets) * 10),
                      f"Etapa 2/5: escenas - produccion {pid} ({len(pending_scenes)}/{total_scene_targets})")
            scenes_summary.append(self.sync_scenes_from_plan(
                production_id=pid,
                scene_names=list(pending_scenes),
                dry_run=dry_run,
                include_scene_json=include_scene_json,
                scenes_data=dyn_scenes,
                sync_date_to=None,
            ))
        results["scenes"] = scenes_summary

        files_summary = []
        files_plan = self.files_inserts_global(
            production_id=production_id,
            active_only=active_only,
        )
        pending_file_productions = [item for item in files_plan.get("by_production", []) if item.get("pending_count", 0) > 0]
        total_files = max(int(files_plan.get("total_pending", 0) or 0), 1)
        processed_files = 0
        _progress(40, f"Etapa 3/5: archivos - plan global listo ({files_plan.get('total_pending', 0)} archivos pendientes)")
        for prod_item in pending_file_productions:
            if job is not None and getattr(job, "cancel_requested", False):
                results["cancelled_at"] = "Etapa 3/5: archivos"
                return results
            pid = prod_item.get("production_id")
            scene_items = [scene for scene in prod_item.get("by_scene", []) if scene.get("pending_count", 0) > 0]
            if not scene_items:
                continue
            prod_files = sum(int(scene.get("pending_count", 0) or 0) for scene in scene_items)
            prod_scenes = len(scene_items)
            _progress(
                40 + int((processed_files / total_files) * 50),
                f"Etapa 3/5: archivos - prod {pid} | escenas {prod_scenes} | archivos {prod_files} | avance {processed_files}/{total_files}",
            )
            file_result = self.sync_files_from_plan(pid, scene_items, dry_run)
            files_summary.append(file_result)
            processed_files += prod_files
            _progress(
                40 + int((processed_files / total_files) * 50),
                f"Etapa 3/5: archivos - prod {pid} finalizada | escenas {prod_scenes} | archivos {prod_files} | avance {processed_files}/{total_files}",
            )
        results["files"] = files_summary
        _progress(
            52,
            f"Etapa 3/5: archivos - terminada ({len(files_summary)} producciones), fase2_completa_at gestionado por sync_files_from_plan",
        )

        _progress(55, "Etapa 4/5: IA - iniciando")
        results["ia"] = self.sync_ia(production_id, dry_run)
        _progress(75, f"Etapa 4/5: IA - terminada ({results['ia'].get('status', 'ok')})")

        geometry_summary = []
        geometry_plan = self.geometry_inserts(production_id=production_id, active_only=active_only)
        geometry_pending = geometry_plan.get("pending", []) if isinstance(geometry_plan, dict) else list(geometry_plan or [])
        total_geo = max(len(geometry_pending), 1)
        _progress(80, f"Etapa 5/5: geometría - procesando 0/{total_geo}")
        for index, item in enumerate(geometry_pending, start=1):
            if job is not None and getattr(job, "cancel_requested", False):
                results["cancelled_at"] = "Etapa 5/5: geometría"
                return results
            pid = item.get("production_id")
            geometry_summary.append(self.sync_geometry_item(item=item, dry_run=dry_run))
            _progress(80 + int((index / total_geo) * 20), f"Etapa 5/5: geometría - procesada produccion {pid} ({index}/{total_geo})")
        results["geometry"] = geometry_summary
        results["geometry_plan"] = geometry_plan
        _progress(100, "sync full completado")
        return results

    # =====================================================================
    # Helpers
    # =====================================================================
    def _resolve_s3_prod_id(self, production_id: int) -> Optional[int]:
        m = get_repo().get_production_id_map([production_id])
        return m.get(production_id)

    def _target_production_ids(self, production_id: Optional[int],
                               active_only: bool) -> list[int]:
        if production_id is not None:
            return [production_id]
        try:
            return get_repo()._target_production_ids_mysql(active_only=active_only)
        except Exception as exc:
            log.warning("No se pudieron obtener producciones objetivo desde MySQL, usando Dynamo fallback: %s", exc)
        s = self._settings
        items = get_dynamo().scan(s.PRODUCTION_MONITORING_TABLE_NAME)
        if active_only:
            items = [i for i in items if str(i.get("estatus", "")).upper() == "OPEN"]
        return [i["produccion_id"] for i in items if i.get("produccion_id") is not None]
    
    def _productions_to_process(self, production_id: Optional[int]) -> dict[int, int]:
        """Devuelve {produccion_id: s3_monitoring_produccion_id}."""
        if production_id is not None:
            m = get_repo().get_production_id_map([production_id])
            return m
        # todas las que ya estan en MySQL
        cfg = get_runtime_config()
        from app.clients.mysql_client import get_mysql
        rows = get_mysql().fetch_all(
            f"SELECT produccion_id, s3_monitoring_produccion_id FROM {cfg.mysql_target_table}"
        )
        return {r["produccion_id"]: r["s3_monitoring_produccion_id"] for r in rows}

    def _geometry_desired_from_dyn(self, production_id: int, dyn: dict) -> dict:
        center = T.geometry_center_from_pbox(dyn)
        tile_edge_meters = 1000
        tile = T.geometry_tile_from_center(
            center.get("tile_center_lat"),
            center.get("tile_center_lon"),
            tile_edge_meters,
        )
        return {
            "produccion_id": production_id,
            "pbox": tile.get("pbox"),
            "polygon_bbox": tile.get("polygon_bbox"),
            **center,
            "tile_bbox": tile.get("tile_bbox"),
            "tile_edge_meters": tile_edge_meters,
        }

    def _geometry_diff(self, current: dict, desired: dict) -> dict:
        reasons: list[str] = []
        current_view = {
            "tile_center_lat": current.get("tile_center_lat"),
            "tile_center_lon": current.get("tile_center_lon"),
            "tile_edge_meters": current.get("tile_edge_meters"),
            "pbox": current.get("pbox"),
            "polygon_bbox": current.get("polygon_bbox"),
            "tile_bbox": current.get("tile_bbox"),
        }
        for key in ("tile_center_lat", "tile_center_lon", "tile_edge_meters"):
            if not self._numeric_close(current.get(key), desired.get(key)):
                reasons.append(f"{key}_changed")
        for key in ("pbox", "polygon_bbox", "tile_bbox"):
            if current.get(key) is None:
                reasons.append(f"{key}_missing")
        return {
            "needs_update": bool(reasons),
            "reasons": reasons,
            "current": current_view,
        }

    def _production_diff(self, current: dict, desired: dict) -> dict:
        reasons: list[str] = []
        current_view = {
            "prefix": current.get("prefix"),
            "monitoring": current.get("monitoring"),
            "max_dias_monitoring": current.get("max_dias_monitoring"),
            "fecha_fin": current.get("fecha_fin"),
            "fecha_plantacion": current.get("fecha_plantacion"),
            "pbox": current.get("pbox"),
            "polygon_bbox": current.get("polygon_bbox"),
        }
        for key in ("prefix", "monitoring", "max_dias_monitoring", "fecha_fin", "fecha_plantacion"):
            if key in ("fecha_fin", "fecha_plantacion"):
                if self._normalize_date(current.get(key)) != self._normalize_date(desired.get(key)):
                    reasons.append(f"{key}_changed")
            elif current.get(key) != desired.get(key):
                reasons.append(f"{key}_changed")
        for key in ("pbox", "polygon_bbox"):
            if current.get(key) is None:
                reasons.append(f"{key}_missing")
        return {
            "needs_update": bool(reasons),
            "reasons": reasons,
            "current": current_view,
        }

    def _normalize_date(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value).strip()

    def _numeric_close(self, current: Any, desired: Any, tolerance: float = 1e-9) -> bool:
        if current is None or desired is None:
            return current is None and desired is None
        try:
            return abs(float(current) - float(desired)) <= tolerance
        except (TypeError, ValueError):
            return current == desired

    def _geometry_equivalent(self, current: Any, desired: Any) -> bool:
        if current is None and desired is None:
            return True
        if current is None or desired is None:
            return False
        try:
            return self._canonical_json(current) == self._canonical_json(desired)
        except Exception:  # noqa: BLE001
            return current == desired

    def _canonical_json(self, value: Any) -> str:
        obj = json.loads(value) if isinstance(value, str) else value
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def production_dynamo_by_key(self, production_id: int) -> Optional[dict]:
        """Compatibilidad: alias para lectura por clave primaria de Dynamo."""
        return self.production_dynamo(production_id)


_service: Optional[SyncService] = None


def get_sync_service() -> SyncService:
    global _service
    if _service is None:
        _service = SyncService()
    return _service
