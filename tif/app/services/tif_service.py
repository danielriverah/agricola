"""
Capa de servicio del TIF.

Ensambla configuración + repositorios MySQL (solo lectura) + storage + procesador
raster. Es el punto que usan los routers. No contiene FastAPI ni HTTP.
"""

from __future__ import annotations

import json

from app.config.models import AppConfig
from app.db.mysql_client import MySQLReadOnlyClient
from app.db.repositories import (
    ProductionRepository,
    SceneFileRepository,
    SceneRepository,
)
from app.processing.raster import MULTIBAND_BANDS, RasterProcessor, SceneResult, _derive_band_template, _normalize_urls_bandas_value
from app.storage.storage import build_storage_driver


class TifService:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.mysql = MySQLReadOnlyClient(cfg.mysql)
        self.productions = ProductionRepository(self.mysql)
        self.scenes = SceneRepository(self.mysql)
        self.files = SceneFileRepository(self.mysql)
        self.storage = build_storage_driver(cfg.storage)
        self.processor = RasterProcessor(cfg, self.storage)

    # ---------- selección ----------

    def get_production_or_none(self, production_id) -> dict | None:
        return self.productions.get_by_production_id(production_id)

    def production_has_tile_bbox(self, production: dict | None) -> bool:
        if not production:
            return False
        tile_bbox = production.get("tile_bbox")
        return tile_bbox is not None and str(tile_bbox).strip() not in {"", "null", "None"}

    def scenes_for_production(self, production: dict) -> list[dict]:
        if not self.production_has_tile_bbox(production):
            return []
        internal_id = production.get("s3_monitoring_produccion_id")
        return self.scenes.list_by_internal_production(internal_id)

    def missing_tif_for_production(self, production: dict) -> list[dict]:
        if not self.production_has_tile_bbox(production):
            return []
        internal_id = production.get("s3_monitoring_produccion_id")
        return self.scenes.list_missing_tif(internal_id)

    def _persist_scene_urls_bandas(self, scene: dict, resolved: dict[str, str]) -> None:
        scene_id = scene.get("s3_monitoring_escena_id")
        if scene_id is not None:
            self.scenes.update_urls_bandas(scene_id, json.dumps(resolved, ensure_ascii=False))
        scene["urls_bandas"] = resolved

    def _persist_scene_urls_bandas_to_peers(self, scene: dict, resolved: dict[str, str], peers: list[dict] | None = None) -> None:
        internal_id = scene.get("s3_monitoring_produccion_id")
        scene_name = str(scene.get("scene_name") or "")
        if internal_id is None or not scene_name:
            return
        peer_list = peers if peers is not None else self.scenes.list_by_internal_production(internal_id)
        for peer in peer_list:
            if str(peer.get("scene_name") or "") != scene_name:
                continue
            if _normalize_urls_bandas_value(peer.get("urls_bandas"), scene_name):
                continue
            peer_id = peer.get("s3_monitoring_escena_id")
            if peer_id is not None:
                self.scenes.update_urls_bandas(peer_id, json.dumps(resolved, ensure_ascii=False))

    def _derive_urls_from_scene_json(self, scene_json_uri: str, scene_name: str) -> dict[str, str]:
        scene_json_text = self.storage.read_text(scene_json_uri)
        scene_json = json.loads(scene_json_text)
        if not isinstance(scene_json, dict):
            return {}
        resolved = _normalize_urls_bandas_value(scene_json.get("urls_bandas"), scene_name)
        if resolved:
            return resolved
        resolved = _normalize_urls_bandas_value(scene_json.get("bands"), scene_name)
        if resolved:
            return resolved
        sample_url = scene_json.get("scene_json_uri") or scene_json.get("scene_json_url") or scene_json.get("band_url")
        return _normalize_urls_bandas_value(sample_url, scene_name)

    def _derive_urls_from_sibling(self, sibling: dict, target_scene_name: str) -> dict[str, str]:
        sibling_name = str(sibling.get("scene_name") or "")
        sibling_urls = _normalize_urls_bandas_value(sibling.get("urls_bandas"), sibling_name)
        if not sibling_urls:
            return {}
        sample_url = next(iter(sibling_urls.values()), None)
        if not isinstance(sample_url, str) or not sample_url:
            return {}
        if sibling_name and sibling_name in sample_url and target_scene_name != sibling_name:
            sample_url = sample_url.replace(sibling_name, target_scene_name)
        template = _derive_band_template(sample_url, target_scene_name)
        if template is None:
            return {}
        return {
            band: template.format(band=band, escena_name=target_scene_name, escene_name=target_scene_name)
            for band in MULTIBAND_BANDS
        }

    def ensure_scene_urls_bandas(self, scene: dict, candidates: list[dict] | None = None) -> dict:
        """
        Si la escena no tiene `urls_bandas`, intenta:
        1) usar su propio `scene_json_uri`
        2) tomar una escena candidata con `urls_bandas`
        3) tomar cualquier escena candidata con JSON v?lido y persistir la plantilla
        """
        scene_name = str(scene.get("scene_name") or "")
        current = scene.get("urls_bandas")
        resolved = _normalize_urls_bandas_value(current, scene_name)
        if resolved:
            self._persist_scene_urls_bandas(scene, resolved)
            return scene

        scene_json_uri = scene.get("scene_json_uri")
        if scene_json_uri:
            try:
                resolved = self._derive_urls_from_scene_json(scene_json_uri, scene_name)
            except Exception:
                resolved = {}
            if resolved:
                self._persist_scene_urls_bandas(scene, resolved)
                self._persist_scene_urls_bandas_to_peers(scene, resolved, candidates)
                return scene

        internal_id = scene.get("s3_monitoring_produccion_id")
        if internal_id is not None:
            siblings = candidates if candidates is not None else self.scenes.list_by_internal_production(internal_id)
            same_name = [s for s in siblings if str(s.get("scene_name") or "") == scene_name and s.get("urls_bandas")]
            for sibling in same_name:
                sibling_urls = _normalize_urls_bandas_value(sibling.get("urls_bandas"), scene_name)
                if sibling_urls:
                    self._persist_scene_urls_bandas(scene, sibling_urls)
                    self._persist_scene_urls_bandas_to_peers(scene, sibling_urls, siblings)
                    return scene
            for sibling in siblings:
                if str(sibling.get("scene_name") or "") == scene_name:
                    continue
                if not sibling.get("urls_bandas") and not sibling.get("scene_json_uri"):
                    continue
                try:
                    sibling_resolved = {}
                    if sibling.get("scene_json_uri"):
                        sibling_resolved = self._derive_urls_from_scene_json(sibling.get("scene_json_uri"), scene_name)
                    if not sibling_resolved:
                        sibling_resolved = self._derive_urls_from_sibling(sibling, scene_name)
                except Exception:
                    sibling_resolved = {}
                if sibling_resolved:
                    self._persist_scene_urls_bandas(scene, sibling_resolved)
                    self._persist_scene_urls_bandas_to_peers(scene, sibling_resolved, siblings)
                    return scene
        return scene
    def ensure_scene_urls_bandas_norm(self, scene_name:str, candidates: list[dict] | None = None) :
        prods_filter = [ x for x in candidates if x["urls_bandas"]!=None]
        if(len(prods_filter)==0):
            scene=candidates[0]
            scene_json_uri = scene.get("scene_json_uri")
            if scene_json_uri:
                try:
                    resolved = self._derive_urls_from_scene_json(scene_json_uri, scene_name)
                except Exception:
                    resolved = {}
                #return resolved
                #if resolved:
                #    self._persist_scene_urls_bandas(scene, resolved)
                #    self._persist_scene_urls_bandas_to_peers(scene, resolved, candidates)
                #    return scene
        else:
            bandas=prods_filter[0].get("urls_bandas")
            resolved = _normalize_urls_bandas_value(bandas, scene_name)
        return resolved
        current = scene.get("urls_bandas")
        result={}
        return result

    # ---------- procesamiento ----------

    def process_one_scene(
        self, production: dict, scene: dict, generate_files: bool, dry_run: bool
    ) -> SceneResult:
        return self.processor.process_scene(production, scene, generate_files, dry_run)


def build_service(cfg: AppConfig) -> TifService:
    return TifService(cfg)
