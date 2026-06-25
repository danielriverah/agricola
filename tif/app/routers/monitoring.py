"""Router de monitoreo: producciones, escenas, catálogos y lotes (solo lectura)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path as FsPath

from fastapi import APIRouter, Depends, Path, Query

from app.config.models import AppConfig
from app.core.dependencies import require_mysql
from app.core.errors import HeavyJobBusy, ValidationError
from app.jobs.manager import Job, get_job_manager
from app.services.tif_service import build_service

from typing import Any, Optional

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


def _parse_date(value) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None


def _scene_sort_key(scene: dict) -> tuple:
    fecha = _parse_date(scene.get("fecha"))
    cloud = scene.get("cloud_cover")
    cloud_value = float(cloud) if isinstance(cloud, (int, float)) else 9999.0
    return (
        (fecha.timestamp() if fecha else 0.0),
        cloud_value,
        str(scene.get("scene_name") or ""),
    )


def _date_value(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return _parse_date(value)


def _scene_date(scene: dict) -> datetime | None:
    return _parse_date(scene.get("fecha"))


# --------------------------- Producciones ---------------------------

@router.get("/productions/raw", summary="Producciones crudas desde MySQL")
def productions_raw(
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    cfg: AppConfig = Depends(require_mysql),
):
    return {"items": build_service(cfg).productions.list_all(limit, offset)}


@router.get("/productions", summary="Producciones (lista normalizada)")
def productions(
    limit: int = Query(500, ge=1, le=2000),
    cfg: AppConfig = Depends(require_mysql),
):
    return {"items": build_service(cfg).productions.list_all(limit)}


@router.get("/productions/enriched", summary="Producciones con conteo de escenas")
def productions_enriched(
    limit: int = Query(200, ge=1, le=1000),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    out = []
    for p in svc.productions.list_all(limit):
        scenes = svc.scenes_for_production(p)
        missing = [s for s in scenes if not s.get("truth_tif_exists")]
        out.append(
            {
                **p,
                "scene_count": len(scenes),
                "missing_tif_count": len(missing),
            }
        )
    return {"items": out}


@router.get("/productions/active", summary="Producciones activas")
def productions_active(cfg: AppConfig = Depends(require_mysql)):
    return {"items": build_service(cfg).productions.list_active()}


@router.get("/productions/history", summary="Producciones históricas")
def productions_history(cfg: AppConfig = Depends(require_mysql)):
    return {"items": build_service(cfg).productions.list_history()}


@router.get("/productions/page-ids", summary="IDs de producciones paginados")
def productions_page_ids(
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    cfg: AppConfig = Depends(require_mysql),
):
    return {"items": build_service(cfg).productions.page_ids(limit, offset)}


# --------------------------- Escenas ---------------------------

@router.get("/scenes/all", summary="Todas las escenas")
def scenes_all(
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    cfg: AppConfig = Depends(require_mysql),
):
    return {"items": build_service(cfg).scenes.list_all(limit, offset)}


@router.get("/scenes/active", summary="Escenas activas")
def scenes_active(cfg: AppConfig = Depends(require_mysql)):
    return {"items": build_service(cfg).scenes.list_active()}


@router.get("/scenes/history", summary="Escenas históricas")
def scenes_history(cfg: AppConfig = Depends(require_mysql)):
    return {"items": build_service(cfg).scenes.list_history()}


@router.get("/scenes/raw", summary="Escenas crudas")
def scenes_raw(
    limit: int = Query(1000, ge=1, le=5000),
    cfg: AppConfig = Depends(require_mysql),
):
    return {"items": build_service(cfg).scenes.list_all(limit)}


@router.get("/scenes/{production_id}", summary="Escenas de una producción")
def scenes_by_production(
    production_id: str = Path(...),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    prod = svc.get_production_or_none(production_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {production_id}", "production_id")
    return {"production_id": production_id, "items": svc.scenes_for_production(prod)}


@router.get("/productions/escenes", summary="Producciones con escenas agrupadas y ordenadas")
def productions_escenes(
    limit: int = Query(500, ge=1, le=5000),
    productions_ids: list[int] | None = Query(None, description="IDs de producción a incluir; si no se envía, trae todas."),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    productions = svc.productions.list_all(limit)
    if productions_ids:
        wanted = {int(pid) for pid in productions_ids}
        productions = [prod for prod in productions if int(prod.get("produccion_id") or 0) in wanted]
    grouped: list[dict] = []

    for prod in productions:
        if not svc.production_has_tile_bbox(prod):
            continue
        scenes = svc.scenes_for_production(prod)
        scenes = sorted(scenes, key=_scene_sort_key)
        latest_scene = _parse_date(scenes[0].get("fecha")) if scenes else None
        grouped.append(
            {
                "production": prod,
                "latest_scene_date": latest_scene.isoformat() if latest_scene else None,
                "scene_count": len(scenes),
                "scenes": scenes,
            }
        )

    grouped.sort(
        key=lambda item: _parse_date(item.get("latest_scene_date")) or datetime.min,
        reverse=True,
    )
    return {"items": grouped}


# --------------------------- Catálogo y lotes ---------------------------

@router.get("/catalog/{production_id}", summary="Catálogo de una producción")
def catalog_production(
    production_id: str = Path(...),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    prod = svc.get_production_or_none(production_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {production_id}", "production_id")
    scenes = svc.scenes_for_production(prod)
    return {"production": prod, "scenes": scenes}


@router.get("/catalogs", summary="Catálogo global resumido")
def catalogs(
    limit: int = Query(100, ge=1, le=500),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    prods = svc.productions.list_all(limit)
    return {"productions": len(prods), "items": prods}


@router.get("/catalogs/producciones", summary="Catálogo de producciones")
def catalogs_producciones(
    limit: int = Query(500, ge=1, le=2000),
    cfg: AppConfig = Depends(require_mysql),
):
    return {"items": build_service(cfg).productions.list_all(limit)}


@router.get("/catalogs/escenas", summary="Catálogo de escenas")
def catalogs_escenas(
    limit: int = Query(1000, ge=1, le=5000),
    cfg: AppConfig = Depends(require_mysql),
):
    return {"items": build_service(cfg).scenes.list_all(limit)}


@router.get("/catalogs/escenas/detalle", summary="Catálogo de escenas detallado")
def catalogs_escenas_detalle(
    limit: int = Query(500, ge=1, le=2000),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    scenes = svc.scenes.list_all(limit)
    out = []
    for s in scenes:
        files = svc.files.list_by_scene(s.get("s3_monitoring_escena_id"))
        out.append({**s, "files_indexed": len(files)})
    return {"items": out}


@router.get("/lots", summary="Lotes lógicos (una producción = un lote)")
def lots(
    limit: int = Query(200, ge=1, le=1000),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    prods = svc.productions.list_all(limit)
    return {
        "items": [
            {"lot_id": p.get("produccion_id"), "prefix": p.get("prefix")} for p in prods
        ]
    }


@router.get("/lots/{lot_id}/results", summary="Resultados (escenas) de un lote")
def lot_results(
    lot_id: str = Path(...),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    prod = svc.get_production_or_none(lot_id)
    if not prod:
        raise ValidationError(f"Lote/producción no encontrado: {lot_id}", "lot_id")
    return {"lot_id": lot_id, "scenes": svc.scenes_for_production(prod)}


# --------------------------- TIF temporal por escena ---------------------------


@router.post("/productions/{produccion_id}/escenes/{escene_name}/get_bandas", summary="Descargar bandas temporales de una escena")
def scene_get_bandas(
    produccion_id: str = Path(...),
    escene_name: str = Path(...),
    dry_run: bool = Query(False),
    regenerate: bool = Query(False),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    prod = svc.get_production_or_none(produccion_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {produccion_id}", "produccion_id")
    scenes = [s for s in svc.scenes_for_production(prod) if str(s.get("scene_name")) == str(escene_name)]
    if not scenes:
        raise ValidationError(f"Escena no encontrada: {escene_name}", "escene_name")
    scene = svc.ensure_scene_urls_bandas(scenes[0])

    jm = get_job_manager()
    job, active = jm.try_create_heavy(kind=f"bands:{produccion_id}:{escene_name}", total=1)
    if job is None:
        raise HeavyJobBusy(active.as_dict() if active else None)

    def work(j: Job) -> None:
        if j.cancel_requested():
            return
        j.progress_note = "Etapa 1/3: preparando bandas temporales"
        res = svc.processor.cache_bands_for_scene(prod, scene, dry_run=dry_run, cancel_requested=j.cancel_requested)
        j.results.append(res)
        j.processed = 1
        j.progress_note = "Etapa 1/3: bandas temporales listas"

    jm.run_async(job, work)
    return {"job_id": job.id}


@router.post("/productions/{produccion_id}/escenes/{escene_name}/genera_tif", summary="Generar multiband temporal de una escena")
def scene_genera_tif(
    produccion_id: str = Path(...),
    escene_name: str = Path(...),
    dry_run: bool = Query(False),
    regenerate: bool = Query(False),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    prod = svc.get_production_or_none(produccion_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {produccion_id}", "produccion_id")
    if not svc.production_has_tile_bbox(prod):
        raise ValidationError("La producción no tiene tile_bbox.", "tile_bbox")
    scenes = [s for s in svc.scenes_for_production(prod) if str(s.get("scene_name")) == str(escene_name)]
    if not scenes:
        raise ValidationError(f"Escena no encontrada: {escene_name}", "escene_name")
    scene = svc.ensure_scene_urls_bandas(scenes[0])

    jm = get_job_manager()
    job, active = jm.try_create_heavy(kind=f"multiband:{produccion_id}:{escene_name}", total=3)
    if job is None:
        raise HeavyJobBusy(active.as_dict() if active else None)

    def work(j: Job) -> None:
        if j.cancel_requested():
            return
        j.progress_note = "Etapa 1/3: preparando bandas temporales"
        svc.processor.cache_bands_for_scene(prod, scene, dry_run=dry_run, cancel_requested=j.cancel_requested)
        j.processed = 1
        if j.cancel_requested():
            return
        j.progress_note = "Etapa 2/3: construyendo multiband temporal"
        res = svc.processor.build_temp_multiband_from_cache(prod, scene, dry_run=dry_run)
        multiband_path = res.get("multiband_path")
        if not dry_run and (not multiband_path or not FsPath(str(multiband_path)).exists()):
            raise ValidationError(
                f"No se gener? el multiband temporal para {escene_name} en {multiband_path}.",
                "multiband_path",
            )
        j.results.append(res)
        j.processed = 2
        if j.cancel_requested():
            return
        j.progress_note = "Etapa 3/3: subiendo multiband, creando vistas y params"
        out = svc.processor.generate_scene_assets_from_temp_bands(prod, scene, dry_run=dry_run, regenerate=regenerate)
        j.results.append(out)
        j.processed = 3
        j.progress_note = "Etapa 3/3: multiband, vistas y params listos"

    jm.run_async(job, work)
    return {"job_id": job.id}


@router.post("/productions/{produccion_id}/escenes/{escene_name}/subir_tif", summary="Subir multiband temporal a S3")
def scene_subir_tif(
    produccion_id: str = Path(...),
    escene_name: str = Path(...),
    dry_run: bool = Query(False),
    regenerate: bool = Query(False),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    prod = svc.get_production_or_none(produccion_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {produccion_id}", "produccion_id")
    if not svc.production_has_tile_bbox(prod):
        raise ValidationError("La producción no tiene tile_bbox.", "tile_bbox")
    scenes = [s for s in svc.scenes_for_production(prod) if str(s.get("scene_name")) == str(escene_name)]
    if not scenes:
        raise ValidationError(f"Escena no encontrada: {escene_name}", "escene_name")
    scene = svc.ensure_scene_urls_bandas(scenes[0])

    jm = get_job_manager()
    job, active = jm.try_create_heavy(kind=f"upload_tif:{produccion_id}:{escene_name}", total=1)
    if job is None:
        raise HeavyJobBusy(active.as_dict() if active else None)

    def work(j: Job) -> None:
        if j.cancel_requested():
            return
        j.progress_note = "Etapa 3/3: subiendo e indexando multiband"
        res = svc.processor.upload_temp_multiband(prod, scene, dry_run=dry_run, regenerate=regenerate)
        j.results.append(res)
        j.processed = 1
        j.progress_note = "Etapa 3/3: multiband subida e indexada"

    jm.run_async(job, work)
    return {"job_id": job.id}

@router.post("/productions/scenes/generate/all", summary="Generar escenas y archivos en cadena")
def scenes_generate_all(
    dry_run: bool = Query(False),
    active_only: bool = Query(True),
    sobreescribir: bool = Query(False),
    production_id: int | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    cfg: AppConfig = Depends(require_mysql),
):
    svc = build_service(cfg)
    
    producciones=svc.productions.get_producciones(
        date_from,
        date_to,
        active_only,
        sobreescribir,
        produccion_id=production_id,
    )
    escenes_dw=svc.scenes.get_escenes_download(
        date_from,
        date_to,
        active_only,
        sobreescribir,
        produccion_id=production_id,
    )
    escenes_mysql=svc.scenes.get_escenes_mysql(
        date_from,
        date_to,
        active_only,
        sobreescribir,
        produccion_id=production_id,
    )
    scene_groups: dict[str, dict[str, Any]] = {}
    for it in escenes_dw:
        scene_name = str(it.get("scene_name") or "").strip()
        if not scene_name:
            continue
        prods_filter=[x for x in escenes_mysql if x["scene_name"] ==scene_name]
        
        scenes=[]
        for scene in prods_filter:
            #return {"result":scene}
            scenes.append({
                    "production_id":scene.get("produccion_id"),
                    "produccion_id":scene.get("produccion_id"),
                    "s3_monitoring_produccion_id":scene.get("s3_monitoring_produccion_id"),
                    "s3_monitoring_escena_id":scene.get("s3_monitoring_escena_id"),
                    "fecha":scene.get("fecha"),
                    "scene_name":scene.get("scene_name"),
                    "scene_json_uri":scene.get("scene_json_uri"),
                    "urls_bandas":scene.get("urls_bandas"),
                    "tile_bbox":scene.get("tile_bbox"),
                    "poligono":scene.get("poligono")
                }
            )
        group = scene_groups.setdefault(scene_name, {"scenes":scenes})
            
        #scene_groups[it.get("scene_name")]={}

    
    '''if production_id is not None:
        prod = svc.get_production_or_none(production_id)
        if not prod:
            raise ValidationError(f"Producci?n no encontrada: {production_id}", "production_id")
        productions = [prod]
    else:
        productions = svc.productions.list_active() if active_only else svc.productions.list_all(limit=5000)
    
    today = datetime.now()
    requested_date_from = _date_value(date_from)
    requested_date_to = _date_value(date_to) or today
    if requested_date_from and requested_date_from > requested_date_to:
        requested_date_from = requested_date_to

    plans: list[dict] = []
    for prod in productions:
        if not prod or not svc.production_has_tile_bbox(prod):
            continue
        scenes = svc.scenes_for_production(prod)
        if not scenes:
            continue

        prod_start = requested_date_from
        if prod_start is None:
            tif_complete_at = _date_value(prod.get("tif_complete_at"))
            if tif_complete_at is not None:
                prod_start = tif_complete_at - timedelta(days=10)
            else:
                scene_dates = [d for d in (_scene_date(scene) for scene in scenes) if d is not None]
                prod_start = min(scene_dates) if scene_dates else requested_date_to
        if prod_start > requested_date_to:
            prod_start = requested_date_to

        prod_scenes = [
            scene for scene in scenes
            if (scene_date := _scene_date(scene)) is not None and prod_start <= scene_date <= requested_date_to
        ]
        prod_scenes.sort(key=_scene_sort_key)
        if not sobreescribir:
            prod_scenes = [scene for scene in prod_scenes if not scene.get("truth_tif_exists")]
        if prod_scenes:
            plans.append({"production": prod, "scenes": prod_scenes, "start": prod_start, "end": requested_date_to})
    scene_groups: dict[str, dict[str, Any]] = {}
    for item in plans:
        prod = item["production"]
        for scene in item["scenes"]:
            scene_name = str(scene.get("scene_name") or "").strip()
            if not scene_name:
                continue
            group = scene_groups.setdefault(scene_name, {"scene": scene, "targets": []})
            if not group["scene"].get("urls_bandas") and scene.get("urls_bandas"):
                group["scene"] = scene
            group["targets"].append({"production": prod, "scene": scene})'''

    total_unique_scenes = len(scene_groups)
    # total_targets = sum(len(group["targets"]) for group in scene_groups.values())
    total_targets = sum(len(group["scenes"]) for group in scene_groups.values())
    total_steps = max(1, total_unique_scenes + (total_targets * 3) + total_unique_scenes)
    #return {"result":total_steps}
    jm = get_job_manager()
    job, active = jm.try_create_heavy(kind="scenes_generate_all", total=total_steps)
    if job is None:
        raise HeavyJobBusy(active.as_dict() if active else None)
    def work(j: Job) -> None:
        processed = 0
        for scene_name, group in scene_groups.items():
            print(f">>>SCENES GENERATE ALL INICIO: {scene_name}")
            if j.cancel_requested():
                return
            #candidate_scenes = #[target["scene"] for target in group["targets"] if target.get("scene") is not None]
            representative_scene = svc.ensure_scene_urls_bandas_norm(scene_name, candidates=group["scenes"])
            j.progress_note = f"{processed + 1}/{total_steps}: {scene_name} - descargando escena compartida"
            cache_result = svc.processor.cache_bands_for_scene_get(group, scene_name, representative_scene, dry_run=dry_run)
            j.results.append({"scene_name": scene_name, "cache": cache_result, "targets": len(group["scenes"])})
            processed += 1
            j.processed = processed
            processed_targets: list[tuple[Any, Any]] = []
            print(f">>>SCENES GENERATE ALL INICIO RECORRIDO: {group}")
            for target in group["scenes"]:
                if j.cancel_requested():
                    return
                production_id = target.get("production_id") or target.get("produccion_id")
                print(f">>>RECORRIDO: {production_id}")
                prod_internal_id = target.get("s3_monitoring_produccion_id")

                production_payload = {
                    "production_id": production_id,
                    "produccion_id": production_id,
                    "s3_monitoring_produccion_id": prod_internal_id,
                    "tile_bbox": target.get("tile_bbox"),
                    "poligono": target.get("poligono"),
                }
                scene_payload = {
                    "s3_monitoring_escena_id": target.get("s3_monitoring_escena_id"),
                    "s3_monitoring_produccion_id": prod_internal_id,
                    "fecha": target.get("fecha"),
                    "scene_name": target.get("scene_name"),
                    "scene_json_uri": target.get("scene_json_uri"),
                    "urls_bandas": target.get("urls_bandas"),
                }
                if not production_payload.get("poligono"):
                    raise ValidationError(
                        f"La producci?n {production_id} no trae poligono para {scene_name}.",
                        "poligono",
                    )
                if not production_payload.get("tile_bbox"):
                    raise ValidationError(
                        f"La producci?n {production_id} no trae tile_bbox para {scene_name}.",
                        "tile_bbox",
                    )

                j.progress_note = f"{processed + 1}/{total_steps}: {production_id}/{scene_name} - generando multiband"
                multiband_target = {**scene_payload, **production_payload}
                multiband_result = svc.processor.build_temp_multiband_from_cache_create(multiband_target, sobreescribir, dry_run=dry_run)
                multiband_path = multiband_result.get("multiband_path")
                if not dry_run and (not multiband_path or not FsPath(str(multiband_path)).exists()):
                    raise ValidationError(
                        f"No se gener? el multiband temporal para {production_id}/{scene_name} en {multiband_path}.",
                        "multiband_path",
                    )
                processed += 1
                j.processed = processed
                if j.cancel_requested():
                    return
                j.progress_note = f"{processed + 1}/{total_steps}: {production_id}/{scene_name} - creando vistas e indexando"
                result = svc.processor.generate_scene_assets_from_temp_bands(
                    production_payload,
                    scene_payload,
                    dry_run=dry_run,
                    regenerate=sobreescribir,
                )
                j.results.append({
                    "production_id": production_id,
                    "scene_name": scene_name,
                    "result": result,
                    "multiband_path": multiband_path,
                    "polygon_present": bool(production_payload.get("poligono")),
                })
                processed += 1
                j.processed = processed
                if not dry_run and scene_payload.get("fecha") is not None:
                    from app.db.mysql_client import MySQLWriteClient
                    from app.db.repositories import ProductionRepository
                    writer = MySQLWriteClient(cfg.mysql)
                    prod_repo = ProductionRepository(writer)
                    prod_repo.update_tif_complete_at(prod_internal_id, str(scene_payload.get("fecha"))[:10])
                processed_targets.append((production_id, prod_internal_id))
                print(f">>>FIN RECORRIDO: {production_id}")

            print(f">>>SCENES GENERATE ALL processed_targets: {processed_targets}")
            for production_id, prod_internal_id in processed_targets:
                if j.cancel_requested():
                    return
                j.progress_note = f"{processed + 1}/{total_steps}: {production_id}/{scene_name} - limpiando temporales de producci?n"
                #cleanup = svc.processor.cleanup_temp_scene(scene_name, prod_internal_id)
                j.results.append({"production_id": production_id, "scene_name": scene_name, "cleanup": "cleanup"})
                processed += 1
                j.processed = processed

            if j.cancel_requested():
                return
            j.progress_note = f"{processed + 1}/{total_steps}: {scene_name} - limpiando bandas compartidas"
            #shared_cleanup = svc.processor.cleanup_temp_scene(scene_name)
            j.results.append({"scene_name": scene_name, "cleanup_shared": "shared_cleanup"})
            processed += 1
            j.processed = processed
            j.progress_note = f"{processed}/{total_steps}: {scene_name} - terminado"
            print(f">>>SCENES GENERATE ALL FIN: {scene_name}")
        '''
        for scene_name, group in scene_groups.items():
            if j.cancel_requested():
                return
            representative_scene = group["scene"]
            candidate_scenes = [target["scene"] for target in group["targets"] if target.get("scene") is not None]
            representative_scene = svc.ensure_scene_urls_bandas(representative_scene, candidates=candidate_scenes)
            j.progress_note = f"{processed + 1}/{total_steps}: {scene_name} - descargando escena compartida"
            cache_result = svc.processor.cache_bands_for_scene(group["targets"][0]["production"], representative_scene, dry_run=dry_run, cancel_requested=j.cancel_requested)
            j.results.append({"scene_name": scene_name, "cache": cache_result, "targets": len(group["targets"])})
            processed += 1
            j.processed = processed
            for target in group["targets"]:
                if j.cancel_requested():
                    return
                prod = target["production"]
                scene = target["scene"]
                prod_internal_id = prod.get("s3_monitoring_produccion_id")

                j.progress_note = f"{processed + 1}/{total_steps}: {prod.get('produccion_id')}/{scene_name} - generando multiband"
                multiband_result = svc.processor.build_temp_multiband_from_cache(prod, scene, dry_run=dry_run)
                multiband_path = multiband_result.get("multiband_path")
                if not dry_run and (not multiband_path or not FsPath(str(multiband_path)).exists()):
                    raise ValidationError(
                        f"No se gener? el multiband temporal para {prod.get('produccion_id')}/{scene_name}.",
                        "multiband_path",
                    )
                processed += 1
                j.processed = processed
                if j.cancel_requested():
                    return
                j.progress_note = f"{processed + 1}/{total_steps}: {prod.get('produccion_id')}/{scene_name} - creando vistas e indexando"
                result = svc.processor.generate_scene_assets_from_temp_bands(
                    prod,
                    scene,
                    dry_run=dry_run,
                    regenerate=sobreescribir,
                )
                j.results.append({"production_id": prod.get("produccion_id"), "scene_name": scene_name, "result": result})
                processed += 1
                j.processed = processed
                if not dry_run and scene.get("fecha") is not None:
                    from app.db.mysql_client import MySQLWriteClient
                    from app.db.repositories import ProductionRepository
                    writer = MySQLWriteClient(cfg.mysql)
                    prod_repo = ProductionRepository(writer)
                    prod_repo.update_tif_complete_at(prod_internal_id, str(scene.get("fecha"))[:10])
                j.progress_note = f"{processed + 1}/{total_steps}: {prod.get('produccion_id')}/{scene_name} - limpiando temporales de producci?n"
                cleanup = svc.processor.cleanup_temp_scene(scene_name, prod_internal_id)
                j.results.append({"production_id": prod.get("produccion_id"), "scene_name": scene_name, "cleanup": cleanup})
                processed += 1
                j.processed = processed
            j.progress_note = f"{processed + 1}/{total_steps}: {scene_name} - limpiando bandas compartidas"
            shared_cleanup = svc.processor.cleanup_temp_scene(scene_name)
            j.results.append({"scene_name": scene_name, "cleanup_shared": shared_cleanup})
            processed += 1
            j.processed = processed
            j.progress_note = f"{processed}/{total_steps}: {scene_name} - terminado"'''

    jm.run_async(job, work)
    return {"job_id": job.id}

