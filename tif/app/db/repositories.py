"""
Repositorios de LECTURA contra MySQL.

Solo SELECT. Cubren las tres tablas del README:
  - s3_monitoring_producciones
  - s3_monitoring_escenas
  - s3_monitoring_escena_archivos

Campos exactamente como documenta el README.
"""

from __future__ import annotations

import hashlib

from typing import Any, Optional
from fastapi import APIRouter, Query

from app.db.mysql_client import MySQLReadOnlyClient, MySQLWriteClient

PRODUCTION_FIELDS = [
    "s3_monitoring_produccion_id",
    "produccion_id",
    "prefix",
    "monitoring",
    "max_dias_monitoring",
    "fecha_plantacion",
    "fecha_fin",
    "pbox",
    "polygon_bbox",
    "tile_bbox",
    "tile_center_lat",
    "tile_center_lon",
    "tile_edge_meters",
    "fase2_completa_at",
    "poligono",
]

SCENE_FIELDS = [
    "s3_monitoring_escena_id",
    "s3_monitoring_produccion_id",
    "scene_name",
    "fecha",
    "scene_json_key",
    "scene_json_uri",
    "cloud_cover",
    "status",
    "urls_bandas",
    "truth_tif_exists",
    "render_tif_exists",
    "params_exists",
    "ia_exists",
    "fase2_completa_at",
    "latest_ia_riesgo_nivel",
    "latest_ia_fecha_analisis",
    "production_cloud",
    "usable",
    "analysis",
]

FILE_FIELDS = [
    "s3_monitoring_escena_id",
    "tipo",
    "s3_key",
    "s3_key_hash",
    "s3_uri",
    "extension",
    "size_bytes",
    "last_modified",
    "existe",
    "json_content",
]


def _cols(fields: list[str]) -> str:
    return ", ".join(f"`{f}`" for f in fields)
def _get_cte()->str:
    return """
            WITH ru AS (
            SELECT
                %s AS date_from,
                %s AS date_to,
                %s AS active_only,
                %s AS sobreescribir,
                %s AS produccion_id
        ), rules AS (
            SELECT
                IF(date_from IS NOT NULL AND date_from <> '', CAST(date_from AS DATE), NULL) AS date_from,
                IF(date_to IS NOT NULL AND date_to <> '', CAST(date_to AS DATE), NULL) AS date_to,
                IF(active_only IS NOT NULL AND active_only <> '', active_only, 0) AS active_only,
                IF(sobreescribir IS NOT NULL AND sobreescribir <> '', sobreescribir, 0) AS sobreescribir,
                IF(produccion_id IS NOT NULL AND produccion_id > 0, CAST(produccion_id AS UNSIGNED), NULL) AS produccion_id
            FROM ru
            LIMIT 1
        ), p AS (
            SELECT
                coalesce(date(date_add(p.tif_complete_at, interval 1 day)),MIN(es.fecha)) AS date_from,
		        MIN(es.fecha) AS date_min,
                date_add(p.tif_complete_at, interval 1 day) tif_complete_at,
                p.produccion_id,
                p.s3_monitoring_produccion_id,
                pr.cantidad AS area,
                azp.poligono,
                cc.nombre AS rancho
            FROM s3_monitoring_producciones p
            JOIN rules r
            JOIN producciones pr ON pr.produccion_id = p.produccion_id
            JOIN asignaciones_zonas_producciones azp ON azp.produccion_id = p.produccion_id
            JOIN centros_costos cc ON cc.centro_costo_id = pr.centro_costo_id
            left JOIN s3_monitoring_escenas es ON es.s3_monitoring_produccion_id = p.s3_monitoring_produccion_id
            WHERE (
                    NOT r.active_only
                    OR (
                        p.monitoring = 1
                        AND DATEDIFF(COALESCE(r.date_to, NOW()), p.fecha_plantacion) <= p.max_dias_monitoring
                    )
                )
            AND (
                    r.produccion_id IS NULL
                    OR r.produccion_id = p.produccion_id
                )
            GROUP BY p.s3_monitoring_produccion_id
        ), es_cand AS (
             SELECT
                 p.tif_complete_at,
                 p.produccion_id,
                 p.area,
                 p.poligono,
                 p.rancho,
                 es.s3_monitoring_produccion_id,
                 es.s3_monitoring_escena_id,
                 es.scene_name,
                 es.fecha,
                 es.cloud_cover
                 -- COALESCE(r.date_from, p.date_min)
             FROM p
             JOIN rules r
             JOIN s3_monitoring_escenas es
                 ON es.s3_monitoring_produccion_id = p.s3_monitoring_produccion_id
             WHERE es.fecha BETWEEN if(datediff(COALESCE(r.date_to, NOW()),COALESCE(r.date_from,p.tif_complete_at, p.date_from))<0,
												COALESCE(r.date_to, NOW()),
												COALESCE(r.date_from,p.tif_complete_at, p.date_from)) 
							AND COALESCE(r.date_to, NOW())
         ), es as(
			select es.*,ar.s3_monitoring_escena_archivo_id from es_cand es
            join rules r
            left join s3_monitoring_escena_archivos ar on ar.s3_monitoring_escena_id=es.s3_monitoring_escena_id and ar.tipo in('truth_tif','multiband_tif')
            where r.sobreescribir or ar.s3_monitoring_escena_archivo_id is null
            order by es.fecha, es.scene_name ,es.cloud_cover
     )
        """
    


class ProductionRepository:
    TABLE = "s3_monitoring_producciones"
    def __init__(self, client: MySQLReadOnlyClient) -> None:
        self.client = client
    def _writer(self) -> MySQLWriteClient:
        if isinstance(self.client, MySQLWriteClient):
            return self.client
        return MySQLWriteClient(self.client.cfg)
    
    def update_tif_complete_at(self, s3_monitoring_produccion_id: int | str, fecha: str) -> int:
        sql = (
            f"UPDATE `s3_monitoring_producciones` "
            f"SET `tif_complete_at` = %s "
            f"WHERE `s3_monitoring_produccion_id` = %s "
            f"AND (`tif_complete_at` IS NULL OR `tif_complete_at` < %s)"
        )
        writer = self._writer()
        return writer.execute(sql, (fecha, s3_monitoring_produccion_id, fecha))
    def list_all(self, limit: int = 500, offset: int = 0) -> list[dict]:
        sql = (
            f"SELECT {_cols(PRODUCTION_FIELDS)} FROM `{self.TABLE}` "
            f"ORDER BY `s3_monitoring_produccion_id` LIMIT %s OFFSET %s"
        )
        return self.client.query(sql, (limit, offset))

    def get_by_production_id(self, production_id: int | str) -> dict | None:
        print(f"TABLA CONSULTADA: {self.TABLE} \n campos: {PRODUCTION_FIELDS}")
        sql = (
            f"SELECT {_cols(PRODUCTION_FIELDS)} FROM `{self.TABLE}` "
            f"WHERE `produccion_id` = %s LIMIT 1"
        )
        return self.client.query_one(sql, (production_id,))

    def get_by_internal_id(self, s3_monitoring_produccion_id: int | str) -> dict | None:
        sql = (
            f"SELECT {_cols(PRODUCTION_FIELDS)} FROM `{self.TABLE}` "
            f"WHERE `s3_monitoring_produccion_id` = %s LIMIT 1"
        )
        return self.client.query_one(sql, (s3_monitoring_produccion_id,))

    def list_active(self, limit: int = 500) -> list[dict]:
        # "Activa" = monitoring habilitado y fase2 completa o sin fecha de fin pasada.
        sql = (
            f"SELECT {_cols(PRODUCTION_FIELDS)} FROM `{self.TABLE}` "
            f"WHERE `monitoring` = 1 "
            f"ORDER BY `s3_monitoring_produccion_id` LIMIT %s"
        )
        return self.client.query(sql, (limit,))

    def list_history(self, limit: int = 500) -> list[dict]:
        sql = (
            f"SELECT {_cols(PRODUCTION_FIELDS)} FROM `{self.TABLE}` "
            f"WHERE `fecha_fin` IS NOT NULL "
            f"ORDER BY `fecha_fin` DESC LIMIT %s"
        )
        return self.client.query(sql, (limit,))

    def page_ids(self, limit: int = 1000, offset: int = 0) -> list[dict]:
        sql = (
            f"SELECT `s3_monitoring_produccion_id`, `produccion_id` "
            f"FROM `{self.TABLE}` ORDER BY `s3_monitoring_produccion_id` "
            f"LIMIT %s OFFSET %s"
        )
        return self.client.query(sql, (limit, offset))
    def get_producciones(
        self,
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
        active_only: Optional[bool] = Query(None),
        sobreescribir: Optional[bool] = Query(None),
        produccion_id: Optional[int] = Query(None),
    )->list[dict]:
        sql=_get_cte()
        params=[
            date_from,
            date_to,
            active_only,
            sobreescribir,
            produccion_id,
        ]
        sql+=f"""
            select distinct 
                es.s3_monitoring_produccion_id,
                pr.produccion_id,
                pr.prefix,
                pr.monitoring,
                pr.max_dias_monitoring,
                pr.fecha_plantacion,
                pr.fecha_fin,
                pr.pbox,
                pr.polygon_bbox,
                pr.tile_bbox,
                pr.tile_center_lat,
                pr.tile_center_lon,
                pr.tile_edge_meters,
                pr.fase2_completa_at,
                pr.tif_complete_at,
                pr.poligono
            from es
            join s3_monitoring_producciones pr on pr.produccion_id=es.produccion_id
        """
        return self.client.query(sql,(params))


class SceneRepository:
    TABLE = "s3_monitoring_escenas"

    def __init__(self, client: MySQLReadOnlyClient) -> None:
        self.client = client

    def _writer(self) -> MySQLWriteClient:
        if isinstance(self.client, MySQLWriteClient):
            return self.client
        return MySQLWriteClient(self.client.cfg)

    def list_all(self, limit: int = 1000, offset: int = 0) -> list[dict]:
        sql = (
            f"SELECT {_cols(SCENE_FIELDS)} FROM `{self.TABLE}` "
            f"ORDER BY `s3_monitoring_escena_id` LIMIT %s OFFSET %s"
        )
        return self.client.query(sql, (limit, offset))

    def list_by_internal_production(self, s3_monitoring_produccion_id: int | str) -> list[dict]:
        sql = (
            f"SELECT {_cols(SCENE_FIELDS)} FROM `{self.TABLE}` "
            f"WHERE `s3_monitoring_produccion_id` = %s "
            f"ORDER BY `fecha` DESC"
        )
        return self.client.query(sql, (s3_monitoring_produccion_id,))

    def get_scene(self, s3_monitoring_escena_id: int | str) -> dict | None:
        sql = (
            f"SELECT {_cols(SCENE_FIELDS)} FROM `{self.TABLE}` "
            f"WHERE `s3_monitoring_escena_id` = %s LIMIT 1"
        )
        return self.client.query_one(sql, (s3_monitoring_escena_id,))


    def update_params_exists(self, s3_monitoring_escena_id: int | str, value: int = 1) -> int:
        sql = (
            f"UPDATE `{self.TABLE}` SET `params_exists` = %s "
            f"WHERE `s3_monitoring_escena_id` = %s"
        )
        writer = self._writer()
        return writer.execute(sql, (value, s3_monitoring_escena_id))

    def update_tif_complete_at(self, s3_monitoring_produccion_id: int | str, fecha: str) -> int:
        sql = (
            f"UPDATE `s3_monitoring_producciones` "
            f"SET `tif_complete_at` = %s "
            f"WHERE `s3_monitoring_produccion_id` = %s "
            f"AND (`tif_complete_at` IS NULL OR `tif_complete_at` < %s)"
        )
        writer = self._writer()
        return writer.execute(sql, (fecha, s3_monitoring_produccion_id, fecha))

    def update_cloud_metrics(
        self,
        s3_monitoring_escena_id: int | str,
        production_cloud: float,
        usable: int,
    ) -> int:
        sql = (
            f"UPDATE `{self.TABLE}` "
            f"SET `production_cloud` = %s, `usable` = %s "
            f"WHERE `s3_monitoring_escena_id` = %s "
            f"AND ("
            f"`production_cloud` IS NULL OR `usable` IS NULL OR "
            f"ROUND(`production_cloud`, 2) <> ROUND(%s, 2) OR COALESCE(`usable`, -1) <> %s"
            f")"
        )
        writer = self._writer()
        return writer.execute(sql, (production_cloud, usable, s3_monitoring_escena_id, production_cloud, usable))

    def update_urls_bandas(self, s3_monitoring_escena_id: int | str, urls_bandas_json: str) -> int:
        sql = (
            f"UPDATE `{self.TABLE}` SET `urls_bandas` = %s "
            f"WHERE `s3_monitoring_escena_id` = %s "
            f"AND (`urls_bandas` IS NULL OR `urls_bandas` = '' OR `urls_bandas` = 'null')"
        )
        if not isinstance(self.client, MySQLWriteClient):
            writer = MySQLWriteClient(self.client.cfg)
            return writer.execute(sql, (urls_bandas_json, s3_monitoring_escena_id))
        return self.client.execute(sql, (urls_bandas_json, s3_monitoring_escena_id))

    def list_active(self, limit: int = 1000) -> list[dict]:
        sql = (
            f"SELECT {_cols(SCENE_FIELDS)} FROM `{self.TABLE}` "
            f"WHERE `status` IN ('active', 'ready', 'ok') OR `status` IS NULL "
            f"ORDER BY `fecha` DESC LIMIT %s"
        )
        return self.client.query(sql, (limit,))

    def list_history(self, limit: int = 1000) -> list[dict]:
        sql = (
            f"SELECT {_cols(SCENE_FIELDS)} FROM `{self.TABLE}` "
            f"ORDER BY `fecha` DESC LIMIT %s"
        )
        return self.client.query(sql, (limit,))

    def list_missing_tif(self, s3_monitoring_produccion_id: int | str | None = None) -> list[dict]:
        """Escenas sin multiband.tif indexado (truth_tif_exists falso/0/NULL)."""
        base = (
            f"SELECT {_cols(SCENE_FIELDS)} FROM `{self.TABLE}` "
            f"WHERE (`truth_tif_exists` IS NULL OR `truth_tif_exists` = 0)"
        )
        if s3_monitoring_produccion_id is not None:
            base += " AND `s3_monitoring_produccion_id` = %s ORDER BY `fecha` DESC"
            return self.client.query(base, (s3_monitoring_produccion_id,))
        base += " ORDER BY `s3_monitoring_produccion_id`, `fecha` DESC"
        return self.client.query(base)
    def get_escenes_download(
        self,
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
        active_only: Optional[bool] = Query(None),
        sobreescribir: Optional[bool] = Query(None),
        produccion_id: Optional[int] = Query(None),
    )->list[dict]:
        sql=_get_cte()
        params=[
            date_from,
            date_to,
            active_only,
            sobreescribir,
            produccion_id,
        ]
        sql+=f"""
            select distinct es.fecha,es.scene_name
            FROM p 
            JOIN rules r
            join es ON es.s3_monitoring_produccion_id = p.s3_monitoring_produccion_id
				WHERE es.fecha 
					BETWEEN 
						if(
							datediff(
								COALESCE(r.date_to, NOW()),COALESCE(r.date_from,p.tif_complete_at, p.date_from)
							)<0
                            ,
                            COALESCE(r.date_to, NOW()),
                            COALESCE(r.date_from,p.tif_complete_at, p.date_from)
						)
					AND COALESCE(r.date_to, NOW())
            order by es.fecha,es.cloud_cover
        """
        return self.client.query(sql,(params))
    def get_escenes_mysql(
        self,
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
        active_only: Optional[bool] = Query(None),
        sobreescribir: Optional[bool] = Query(None),
        produccion_id: Optional[int] = Query(None),
    )->list[dict]:
        sql=_get_cte()
        params=[
            date_from,
            date_to,
            active_only,
            sobreescribir,
            produccion_id,
        ]
        sql+=f"""
            select me.*,mp.* 
            from es
            join s3_monitoring_escenas me on me.s3_monitoring_escena_id=es.s3_monitoring_escena_id
            join s3_monitoring_producciones mp on mp.s3_monitoring_produccion_id=me.s3_monitoring_produccion_id
            order by es.fecha,es.cloud_cover
        """
        return self.client.query(sql,(params))

class SceneFileRepository:
    TABLE = "s3_monitoring_escena_archivos"

    def __init__(self, client: MySQLReadOnlyClient) -> None:
        self.client = client

    def _writer(self) -> MySQLWriteClient:
        if isinstance(self.client, MySQLWriteClient):
            return self.client
        return MySQLWriteClient(self.client.cfg)

    def list_by_scene(self, s3_monitoring_escena_id: int | str) -> list[dict]:
        sql = (
            f"SELECT {_cols(FILE_FIELDS)} FROM `{self.TABLE}` "
            f"WHERE `s3_monitoring_escena_id` = %s ORDER BY `tipo`"
        )
        return self.client.query(sql, (s3_monitoring_escena_id,))

    def list_by_scene_and_type(
        self, s3_monitoring_escena_id: int | str, tipo: str
    ) -> list[dict]:
        sql = (
            f"SELECT {_cols(FILE_FIELDS)} FROM `{self.TABLE}` "
            f"WHERE `s3_monitoring_escena_id` = %s AND `tipo` = %s"
        )
        return self.client.query(sql, (s3_monitoring_escena_id, tipo))

    def get_by_scene_and_key(
        self, s3_monitoring_escena_id: int | str, s3_key: str
    ) -> dict | None:
        sql = (
            f"SELECT {_cols(FILE_FIELDS)} FROM `{self.TABLE}` "
            f"WHERE `s3_monitoring_escena_id` = %s AND `s3_key` = %s LIMIT 1"
        )
        return self.client.query_one(sql, (s3_monitoring_escena_id, s3_key))

    def list_by_production_and_extension(
        self, s3_monitoring_produccion_id: int | str, extension: str
    ) -> list[dict]:
        """Archivos de todas las escenas de una producción con cierta extensión."""
        sql = (
            f"SELECT a.* FROM `{self.TABLE}` a "
            f"JOIN `s3_monitoring_escenas` e "
            f"  ON e.`s3_monitoring_escena_id` = a.`s3_monitoring_escena_id` "
            f"WHERE e.`s3_monitoring_produccion_id` = %s AND a.`extension` = %s"
        )
        return self.client.query(sql, (s3_monitoring_produccion_id, extension))

    def upsert_file(
        self,
        s3_monitoring_escena_id: int | str,
        s3_key: str,
        s3_uri: str,
        extension: str | None = None,
        tipo: str | None = None,
        size_bytes: int | None = None,
        last_modified: str | None = None,
        existe: int = 1,
        json_content: str | None = None,
    ) -> int:
        key_hash = hashlib.md5(str(s3_key).encode("utf-8")).hexdigest()
        sql = (
            f"INSERT INTO `{self.TABLE}` "
            f"(`s3_monitoring_escena_id`, `tipo`, `s3_key`, `s3_key_hash`, `s3_uri`, `extension`, `size_bytes`, `last_modified`, `existe`, `json_content`) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            f"ON DUPLICATE KEY UPDATE "
            f"`tipo` = VALUES(`tipo`), `s3_key` = VALUES(`s3_key`), `s3_uri` = VALUES(`s3_uri`), "
            f"`extension` = VALUES(`extension`), `size_bytes` = VALUES(`size_bytes`), "
            f"`last_modified` = VALUES(`last_modified`), `existe` = VALUES(`existe`), "
            f"`json_content` = VALUES(`json_content`), `fecha_actualizacion` = NOW()"
        )
        writer = self._writer()
        return writer.execute(
            sql,
            (
                s3_monitoring_escena_id,
                tipo,
                s3_key,
                key_hash,
                s3_uri,
                extension,
                size_bytes,
                last_modified,
                existe,
                json_content,
            ),
        )

    def update_scene_truth_tif_exists(self, s3_monitoring_escena_id: int | str, value: int = 1) -> int:
        sql = (
            f"UPDATE `s3_monitoring_escenas` SET `truth_tif_exists` = %s "
            f"WHERE `s3_monitoring_escena_id` = %s"
        )
        writer = self._writer()
        return writer.execute(sql, (value, s3_monitoring_escena_id))

    def upsert_json_file(
        self,
        s3_monitoring_escena_id: int | str,
        s3_key: str,
        s3_uri: str,
        tipo: str,
        json_content: str,
        size_bytes: int | None = None,
    ) -> int:
        key_hash = hashlib.md5(str(s3_key).encode("utf-8")).hexdigest()
        sql = (
            f"INSERT INTO `{self.TABLE}` "
            f"(`s3_monitoring_escena_id`, `tipo`, `s3_key`, `s3_key_hash`, `s3_uri`, `extension`, `size_bytes`, `last_modified`, `existe`, `json_content`) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s) "
            f"ON DUPLICATE KEY UPDATE "
            f"`tipo` = VALUES(`tipo`), `s3_key` = VALUES(`s3_key`), `s3_uri` = VALUES(`s3_uri`), `extension` = VALUES(`extension`), "
            f"`size_bytes` = VALUES(`size_bytes`), `last_modified` = VALUES(`last_modified`), `existe` = VALUES(`existe`), "
            f"`json_content` = VALUES(`json_content`), `fecha_actualizacion` = NOW()"
        )
        writer = self._writer()
        return writer.execute(sql, (s3_monitoring_escena_id, tipo, s3_key, key_hash, s3_uri, "json", size_bytes, 1, json_content))

    def get_latest_params_before(
        self,
        produccion_id: int | str,
        current_fecha: str,
        current_s3_monitoring_escena_id: int | str | None = None,
    ) -> dict | None:
        sql = (
            f"SELECT s.`s3_monitoring_escena_id`, s.`scene_name`, s.`fecha`, s.`production_cloud`, a.`json_content` "
            f"FROM `s3_monitoring_producciones` p "
            f"JOIN `s3_monitoring_escenas` s ON s.`s3_monitoring_produccion_id` = p.`s3_monitoring_produccion_id` "
            f"JOIN `{self.TABLE}` a ON a.`s3_monitoring_escena_id` = s.`s3_monitoring_escena_id` AND a.`tipo` = 'params' "
            f"WHERE p.`produccion_id` = %s "
            f"AND a.`json_content` IS NOT NULL "
        )
        params: list[Any] = [produccion_id]
        if current_s3_monitoring_escena_id is not None:
            sql += " AND (s.`fecha` < %s OR (s.`fecha` = %s AND s.`s3_monitoring_escena_id` < %s)) "
            params.extend([current_fecha, current_fecha, current_s3_monitoring_escena_id])
        else:
            sql += " AND s.`fecha` < %s "
            params.append(current_fecha)
        sql += (
            "ORDER BY s.`fecha` DESC, COALESCE(s.`production_cloud`, s.`cloud_cover`) ASC, "
            "s.`s3_monitoring_escena_id` DESC LIMIT 1"
        )
        return self.client.query_one(sql, tuple(params))
