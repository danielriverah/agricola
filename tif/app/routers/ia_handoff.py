"""
Router de handoff hacia IA.

Estas rutas NO ejecutan IA: solo preparan o muestran el payload que consumiría
el siguiente módulo. TIF no debe disparar IA automáticamente ni depender de
targets.ia (regla del README).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path

from app.config.models import AppConfig
from app.core.dependencies import require_mysql
from app.core.errors import ValidationError
from app.services.tif_service import build_service

router = APIRouter(prefix="/monitoring/ia", tags=["ia-handoff"])


def _candidate_scenes(svc, prod: dict) -> list[dict]:
    """Escenas candidatas: con tif disponible y production_cloud apto si existe."""
    scenes = svc.scenes_for_production(prod)
    threshold = svc.cfg.processing.max_production_cloud
    out = []
    for s in scenes:
        pc = s.get("production_cloud")
        apt = pc is None or (isinstance(pc, (int, float)) and pc <= threshold)
        if s.get("truth_tif_exists") and apt:
            out.append(s)
    return out


@router.get("/preview/{production_id}/input", summary="Payload de entrada para IA")
def preview_input(production_id: str = Path(...), cfg: AppConfig = Depends(require_mysql)):
    svc = build_service(cfg)
    prod = svc.get_production_or_none(production_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {production_id}", "production_id")
    candidates = _candidate_scenes(svc, prod)
    return {
        "production_id": production_id,
        "poligono": prod.get("poligono"),
        "tile_bbox": prod.get("tile_bbox"),
        "candidate_scenes": candidates,
        "note": "TIF no ejecuta IA; este es solo el payload de entrada.",
    }


@router.get("/pending-productions/list", summary="Producciones pendientes de IA")
def pending_productions(cfg: AppConfig = Depends(require_mysql)):
    svc = build_service(cfg)
    prods = svc.productions.list_all(500)
    pending = []
    for p in prods:
        scenes = svc.scenes_for_production(p)
        has_tif = any(s.get("truth_tif_exists") for s in scenes)
        needs_ia = any(not s.get("ia_exists") for s in scenes)
        if has_tif and needs_ia:
            pending.append({"production_id": p.get("produccion_id"), "scenes": len(scenes)})
    return {"items": pending}


@router.get("/candidates/{production_id}", summary="Escenas candidatas para IA")
def candidates(production_id: str = Path(...), cfg: AppConfig = Depends(require_mysql)):
    svc = build_service(cfg)
    prod = svc.get_production_or_none(production_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {production_id}", "production_id")
    return {"production_id": production_id, "items": _candidate_scenes(svc, prod)}


@router.get("/selected/{production_id}", summary="Escena seleccionada para IA")
def selected(production_id: str = Path(...), cfg: AppConfig = Depends(require_mysql)):
    svc = build_service(cfg)
    prod = svc.get_production_or_none(production_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {production_id}", "production_id")
    cands = _candidate_scenes(svc, prod)
    # selección: la más reciente con menor production_cloud conocido.
    def sort_key(s):
        pc = s.get("production_cloud")
        return (pc if isinstance(pc, (int, float)) else 999, str(s.get("fecha")))
    chosen = sorted(cands, key=sort_key)[0] if cands else None
    return {"production_id": production_id, "selected": chosen}


@router.get("/payload/{production_id}", summary="Payload final que consumiría IA")
def payload(production_id: str = Path(...), cfg: AppConfig = Depends(require_mysql)):
    svc = build_service(cfg)
    prod = svc.get_production_or_none(production_id)
    if not prod:
        raise ValidationError(f"Producción no encontrada: {production_id}", "production_id")
    cands = _candidate_scenes(svc, prod)
    return {
        "production_id": production_id,
        "geometry": {"poligono": prod.get("poligono"), "tile_bbox": prod.get("tile_bbox")},
        "scenes": cands,
        "outputs_expected": {
            "multiband": cfg.outputs.multiband_filename,
            "params": cfg.outputs.params_filename,
        },
        "db_writes": False,
    }
