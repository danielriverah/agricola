"""
Repositorio MySQL.

Reglas:
  - GET = SELECT puro.
  - Escrituras por lote con INSERT ... ON DUPLICATE KEY UPDATE,
    apoyado en los UNIQUE keys reales de cada tabla:
      producciones        -> UNIQUE(produccion_id)
      escenas             -> UNIQUE(s3_monitoring_produccion_id, scene_name)
      escena_archivos     -> UNIQUE(s3_monitoring_escena_id, s3_key_hash)
      escena_ia_resumen   -> UNIQUE(s3_monitoring_escena_id)
  - Nunca DDL.
"""
from typing import Any, Optional

from app.clients.mysql_client import get_mysql
from app.services.config_service import get_runtime_config

class MySQLRepo:
    @staticmethod
    def get_cte() -> str:
        cfg = get_runtime_config()
        return """
        WITH RECURSIVE ru AS (
            SELECT
                %s AS date_from,
                %s AS date_to,
                %s AS active_only,
                %s AS archivos,
                %s AS produccion_id
        ), rules AS (
            SELECT
                IF(date_from IS NOT NULL AND date_from <> '', CAST(date_from AS DATE), NULL) AS date_from,
                IF(date_to IS NOT NULL AND date_to <> '', CAST(date_to AS DATE), NULL) AS date_to,
                IF(active_only IS NOT NULL AND active_only <> '', active_only, 0) AS active_only,
                IF(archivos IS NOT NULL AND archivos <> '', CAST(archivos AS CHAR(5000)), NULL) AS archivos,
                IF(produccion_id IS NOT NULL AND produccion_id <> '', CAST(produccion_id AS UNSIGNED), NULL) AS produccion_id
            FROM ru
            LIMIT 1
        ), p AS (
            SELECT
                coalesce(date(date_add(p.ultima_sincronizacion, interval -10 day)),MIN(es.fecha)) AS date_from,
		        MIN(es.fecha) AS date_min,
                p.fase2_completa_at,
                p.produccion_id,
                p.s3_monitoring_produccion_id,
                pr.cantidad AS area,
                azp.poligono,
                cc.nombre AS rancho
            FROM s3_monitoring_producciones p
            JOIN rules r
            JOIN producciones pr
                ON pr.produccion_id = p.produccion_id
            JOIN asignaciones_zonas_producciones azp ON azp.produccion_id = p.produccion_id
            JOIN centros_costos cc
                ON cc.centro_costo_id = pr.centro_costo_id
            LEFT JOIN s3_monitoring_escenas es
                ON es.s3_monitoring_produccion_id = p.s3_monitoring_produccion_id
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
        ), es AS (
            SELECT
                p.fase2_completa_at,
                p.produccion_id,
                p.area,
                p.poligono,
                p.rancho,
                es.s3_monitoring_produccion_id,
                es.s3_monitoring_escena_id,
                es.scene_name,
                es.fecha,
                es.ultima_sincronizacion
            FROM p
            JOIN rules r
            JOIN s3_monitoring_escenas es
                ON es.s3_monitoring_produccion_id = p.s3_monitoring_produccion_id
            WHERE es.fecha BETWEEN COALESCE(r.date_from, p.date_min) AND COALESCE(r.date_to, NOW())
        ), d AS (
            SELECT
                1 AS indx,
                CAST(r.archivos AS CHAR(5000)) AS txt,
                CAST(strSplit(r.archivos, ',', 1) AS CHAR(255)) AS valor
            FROM rules r
            UNION ALL
            SELECT
                indx + 1,
                txt,
                strSplit(txt, ',', indx + 1)
            FROM d
            WHERE strSplit(txt, ',', indx + 1) IS NOT NULL
        ),ari AS (
            SELECT
                es.produccion_id,
                es.s3_monitoring_produccion_id,
                es.s3_monitoring_escena_id,
                es.scene_name,
                es.fecha,
                d.valor,
                b.s3_monitoring_escena_archivo_id,
                b.s3_key,
                b.s3_uri
            FROM es
            JOIN d
            join rules r
            JOIN s3_monitoring_escena_archivos b
                ON b.s3_monitoring_escena_id = es.s3_monitoring_escena_id
            WHERE d.valor IS NOT NULL
            AND b.s3_uri LIKE CONCAT('%%', d.valor) COLLATE utf8mb4_general_ci
            /*and es.fecha between if(datediff(r.date_to,p.fase2_completa_at,p.fecha_min,es.archivos_sync)<0,r.date_to,es.archivos_sync) and r.date_to
            and  es.fecha 
                between 
                    if(
                        datediff(coalesce(r.date_to,now()),coalesce(r.date_from,es.fase2_completa_at,es.ultima_sincronizacion))<0,
                        r.date_to,
                        coalesce(r.date_from,es.fase2_completa_at,es.ultima_sincronizacion)
                    ) 
                and 
                   coalesce(r.date_to,now())*/
        ), arf AS (
            SELECT
                es.produccion_id,
                es.s3_monitoring_produccion_id,
                es.s3_monitoring_escena_id,
                es.scene_name,
                d.valor,
                b.s3_monitoring_escena_archivo_id,
                CONCAT('previews/PROD_', es.produccion_id, '/', es.scene_name, '/', d.valor) AS s3_key,
                CONCAT('s3://sentinela-monitoring/previews/PROD_', es.produccion_id, '/', es.scene_name, '/', d.valor) AS s3_uri
            FROM es
            JOIN d
            LEFT JOIN s3_monitoring_escena_archivos b
                ON b.s3_monitoring_escena_id = es.s3_monitoring_escena_id
            AND b.s3_uri LIKE CONCAT('%%', d.valor) COLLATE utf8mb4_general_ci
            WHERE d.valor IS NOT NULL
            AND b.s3_monitoring_escena_archivo_id IS NULL
        )"""
    # ----------------------- Producciones -----------------------
    def _target_production_ids_mysql(self,
        active_only: bool = False,
        production_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None
    ) -> list[int]:
        sql=MySQLRepo.get_cte()
        cfg = get_runtime_config()
        params: list[Any] = [
            date_from,
            date_to,
            active_only,
            (",".join(cfg.s3_phase2_expected_files)),
            production_id
        ]
        sql += f"""
            select distinct produccion_id from es
        """
        rows = get_mysql().fetch_all(sql, tuple(params))
        return [int(row["produccion_id"]) for row in rows if row.get("produccion_id") is not None]
    def _target_production_idsyscene_mysql_toarchivos(self,
        active_only: bool = False,
        production_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None
    ) -> list[dict]:
        sql=MySQLRepo.get_cte()
        cfg = get_runtime_config()
        params: list[Any] = [
            date_from,
            date_to,
            active_only,
            (",".join(cfg.s3_phase2_expected_files)),
            production_id
        ]
        sql += f"""
            select distinct es.produccion_id,es.s3_monitoring_escena_id,es.scene_name,es.fecha
            from es
            join rules r
            join s3_monitoring_producciones pl on pl.produccion_id=es.produccion_id
            where  es.fecha 
                between 
                    if(
                        datediff(coalesce(r.date_to,now()),coalesce(r.date_from,es.fase2_completa_at,pl.fecha_plantacion))<0,
                        r.date_to,
                        coalesce(r.date_from,es.fase2_completa_at,pl.fecha_plantacion)
                    ) 
                and 
                   coalesce(r.date_to,now())
            order by  produccion_id,scene_name;
        """
        print(sql)
        rows = get_mysql().fetch_all(sql, tuple(params))
        print(rows)
        return [
            {
                "produccion_id": int(row["produccion_id"]),
                "s3_monitoring_escena_id": row.get("s3_monitoring_escena_id"),
                "scene_name": row.get("scene_name"),
                "fecha": row.get("fecha"),
            }
            for row in rows
            if row.get("produccion_id") is not None and row.get("scene_name") is not None
        ]
    def _target_production_idsyscene_mysql(self,
        active_only: bool = False,
        production_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None
    ) -> list[dict]:
        sql=MySQLRepo.get_cte()
        cfg = get_runtime_config()
        params: list[Any] = [
            date_from,
            date_to,
            active_only,
            (",".join(cfg.s3_phase2_expected_files)),
            production_id
        ]
        sql += f"""
            select distinct es.produccion_id,es.s3_monitoring_escena_id,es.scene_name,es.fecha 
            from es 
            join rules r
            where  es.fecha 
                between 
                    if(
                        datediff(coalesce(r.date_to,now()),coalesce(r.date_from,es.fase2_completa_at,es.ultima_sincronizacion))<0,
                        r.date_to,
                        coalesce(r.date_from,es.fase2_completa_at,es.ultima_sincronizacion)
                    ) 
                and 
                   coalesce(r.date_to,now())
            order by  produccion_id,scene_name
        """
        rows = get_mysql().fetch_all(sql, tuple(params))
        return [
            {
                "produccion_id": int(row["produccion_id"]),
                "s3_monitoring_escena_id": row.get("s3_monitoring_escena_id"),
                "scene_name": row.get("scene_name"),
                "fecha": row.get("fecha"),
            }
            for row in rows
            if row.get("produccion_id") is not None and row.get("scene_name") is not None
        ]
    def get_production(self, production_id: int) -> Optional[dict]:
        cfg = get_runtime_config()
        sql = f"SELECT * FROM {cfg.mysql_target_table} WHERE produccion_id = %s"
        return get_mysql().fetch_one(sql, (production_id,))

    def get_production_with_polygon(
        self,
        active_only: bool = False,
        production_id: Optional[int] = None,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        sql=MySQLRepo.get_cte()
        cfg = get_runtime_config()
        params: list[Any] = [
            None,
            None,
            active_only,
            None,
            production_id
        ]
        sql+=f"""
            select 
                pr.s3_monitoring_produccion_id, 
                pr.produccion_id, 
                pr.prefix, 
                pr.ultima_sincronizacion, 
                pr.monitoring, 
                pr.max_dias_monitoring, 
                pr.fecha_fin, 
                pr.fecha_creacion, 
                pr.fecha_actualizacion, 
                pr.fecha_plantacion, 
                pr.pbox, 
                pr.polygon_bbox, 
                pr.tile_bbox, 
                pr.tile_center_lat, 
                pr.tile_center_lon, 
                pr.tile_edge_meters, 
                pr.fase2_completa_at, 
                ap.poligono 
            from p 
            join {cfg.mysql_target_table} pr on pr.s3_monitoring_produccion_id=p.s3_monitoring_produccion_id
            JOIN asignaciones_zonas_producciones ap ON ap.produccion_id = pr.produccion_id
            where pr.tile_bbox is null
                or pr.tile_center_lat is null
                or pr.tile_center_lon is null
            order by p.produccion_id
        """
        #return get_mysql().fetch_one(sql, (production_id,))
        print(sql)
        return get_mysql().fetch_all(sql, tuple(params))
    '''def get_production_with_polygon(self, production_id: int) -> Optional[dict]:
        cfg = get_runtime_config()
        sql = f"""
            SELECT p.*, ap.poligono
            FROM {cfg.mysql_target_table} p
            LEFT JOIN asignaciones_zonas_producciones ap
                ON ap.produccion_id = p.produccion_id
            WHERE p.produccion_id = %s
            LIMIT 1
        """"""
        return get_mysql().fetch_one(sql, (production_id,))
    '''
    def list_productions_filtered(
        self,
        active_only: bool = False,
        production_id: Optional[int] = None,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        sql=MySQLRepo.get_cte()
        cfg = get_runtime_config()
        #where = []
        params: list[Any] = [
            None,
            None,
            active_only,
            None,
            production_id
        ]
        #if production_id is not None:
        #    where.append("p.produccion_id = %s")
        #    params.append(production_id)
        #if active_only:
        #   where.append("p.monitoring = 1")
        #    where.append(
        #        "DATEDIFF(COALESCE(%s, NOW()), p.fecha_plantacion) <= p.max_dias_monitoring"
        #    )
        #    params.append(date_to)
        #sql = (
        #    f"SELECT p.*, pr.cantidad AS area, azp.poligono, cc.nombre AS rancho "
        #    f"FROM {cfg.mysql_target_table} p "
        #    f"JOIN producciones pr ON pr.produccion_id = p.produccion_id "
        #    f"JOIN asignaciones_zonas_producciones azp ON azp.produccion_id = pr.produccion_id "
        #    f"JOIN centros_costos cc ON cc.centro_costo_id = pr.centro_costo_id "
        #)
        #if where:
        #    sql += " WHERE " + " AND ".join(where)
        #sql += " GROUP BY p.s3_monitoring_produccion_id"
        sql += (
            f"SELECT distinct pr.*, p.area, p.poligono, p.rancho "
            f"FROM p JOIN {cfg.mysql_target_table} pr on pr.produccion_id=p.produccion_id"
        )
        sql += " GROUP BY pr.s3_monitoring_produccion_id"
        return get_mysql().fetch_all(sql, tuple(params))
    

    def get_production_id_map(self, production_ids: list[int]) -> dict[int, int]:
        """produccion_id -> s3_monitoring_produccion_id"""
        if not production_ids:
            return {}
        cfg = get_runtime_config()
        placeholders = ",".join(["%s"] * len(production_ids))
        sql = (
            f"SELECT s3_monitoring_produccion_id, produccion_id "
            f"FROM {cfg.mysql_target_table} "
            f"WHERE produccion_id IN ({placeholders})"
        )
        rows = get_mysql().fetch_all(sql, tuple(production_ids))
        return {r["produccion_id"]: r["s3_monitoring_produccion_id"] for r in rows}

    def upsert_productions(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        cfg = get_runtime_config()
        production_ids = [row.get("produccion_id") for row in rows if row.get("produccion_id") is not None]
        existing = self.get_production_id_map(production_ids)
        def _normalize(value: Any) -> Any:
            if isinstance(value, (dict, list)):
                import json
                return json.dumps(value, ensure_ascii=False)
            return value
        clean_rows = [
            {key: _normalize(value) for key, value in row.items()}
            for row in rows
            if row.get("produccion_id") not in existing
        ]
        if not clean_rows:
            return 0
        sql = f"""
            INSERT INTO {cfg.mysql_target_table}
                (produccion_id, prefix, monitoring, max_dias_monitoring,
                 fecha_plantacion, pbox, polygon_bbox,
                 ultima_sincronizacion)
            VALUES (%(produccion_id)s, %(prefix)s, %(monitoring)s, %(max_dias_monitoring)s,
                    %(fecha_plantacion)s, %(pbox)s, %(polygon_bbox)s,
                    NULL)
        """
        result=get_mysql().execute_many(sql, clean_rows)
        return result

    def update_production_geometry(self, row: dict) -> int:
        cfg = get_runtime_config()
        sql = f"""
            UPDATE {cfg.mysql_target_table}
            SET pbox = %(pbox)s,
                polygon_bbox = %(polygon_bbox)s,
                poligono = %(poligono)s,
                tile_bbox = %(tile_bbox)s,
                tile_center_lat = %(tile_center_lat)s,
                tile_center_lon = %(tile_center_lon)s,
                tile_edge_meters = %(tile_edge_meters)s,
                fecha_actualizacion = NOW()
            WHERE produccion_id = %(produccion_id)s
        """
        return get_mysql().execute(sql, row)

    def update_production_scene_sync_date(
        self,
        production_id: int,
        fecha_actualizacion: str,
        sync_date_to: Optional[str] = None,
    ) -> int:
        cfg = get_runtime_config()
        sql = f"""
            UPDATE {cfg.mysql_target_table}
            SET fecha_actualizacion = %s,
                ultima_sincronizacion = COALESCE(%s, NOW())
            WHERE produccion_id = %s
              AND (
                    ultima_sincronizacion IS NULL
                    OR ultima_sincronizacion < COALESCE(%s, NOW())
                  )
        """
        return get_mysql().execute(sql, (fecha_actualizacion, sync_date_to, production_id, sync_date_to))

    def update_production_phase2_date(self, production_id: int, fecha_to: str) -> int:
        cfg = get_runtime_config()
        sql = f"""
            UPDATE {cfg.mysql_target_table}
            SET fase2_completa_at = %s,
                fecha_actualizacion = NOW()
            WHERE produccion_id = %s
              AND (fase2_completa_at IS NULL OR fase2_completa_at < %s)
        """
        return get_mysql().execute(sql, (fecha_to, production_id, fecha_to))

    # ----------------------- Escenas -----------------------
    def get_scenes_by_production(self, s3_prod_id: int) -> list[dict]:
        cfg = get_runtime_config()
        sql = (
            f"SELECT * FROM {cfg.mysql_scenes_table} "
            f"WHERE s3_monitoring_produccion_id = %s ORDER BY fecha"
        )
        return get_mysql().fetch_all(sql, (s3_prod_id,))

    def get_scene_id_map(self, s3_prod_id: int) -> dict[str, int]:
        """scene_name -> s3_monitoring_escena_id para una produccion."""
        cfg = get_runtime_config()
        sql = (
            f"SELECT s3_monitoring_escena_id, scene_name FROM {cfg.mysql_scenes_table} "
            f"WHERE s3_monitoring_produccion_id = %s"
        )
        rows = get_mysql().fetch_all(sql, (s3_prod_id,))
        return {r["scene_name"]: r["s3_monitoring_escena_id"] for r in rows}

    def get_scenes_filtered(self, s3_prod_id: int, date_from: Optional[str] = None,
                            date_to: Optional[str] = None) -> list[dict]:
        cfg = get_runtime_config()
        sql = (
            f"SELECT * FROM {cfg.mysql_scenes_table} "
            f"WHERE s3_monitoring_produccion_id = %s"
        )
        params: list[Any] = [s3_prod_id]
        if date_from is not None:
            sql += " AND fecha >= %s"
            params.append(date_from)
        if date_to is not None:
            sql += " AND fecha <= %s"
            params.append(date_to)
        sql += " ORDER BY fecha"
        return get_mysql().fetch_all(sql, tuple(params))

    def list_scenes_grouped(
        self,
        active_only: bool = False,
        production_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        sql=MySQLRepo.get_cte()
        cfg = get_runtime_config()
        #where = []
        params: list[Any] = [
            date_from,
            date_to,
            active_only,
            (",".join(cfg.s3_phase2_expected_files)),
            production_id
        ]
        #sql = (
        #    f"SELECT p.produccion_id, p.s3_monitoring_produccion_id, p.monitoring, "
        #    f"p.fecha_plantacion, p.max_dias_monitoring, p.fecha_fin, "
        #    f"p.prefix, p.pbox, p.polygon_bbox, "
        #    f"e.s3_monitoring_escena_id, e.scene_name, e.fecha, e.cloud_cover, e.status, "
        #    f"e.truth_tif_exists, e.render_tif_exists, e.params_exists, e.ia_exists, e.fase2_completa_at "
        #    f"FROM {cfg.mysql_target_table} p "
        #    f"JOIN {cfg.mysql_scenes_table} e "
        #    f"  ON e.s3_monitoring_produccion_id = p.s3_monitoring_produccion_id"
        #)
        #where = []
        #params: list[Any] = []
        #if active_only:
        #    where.append("p.monitoring = 1")
        #if production_id is not None:
        #    where.append("p.produccion_id = %s")
        #    params.append(production_id)
        #if date_from is not None:
        #    where.append("e.fecha >= %s")
        #    params.append(date_from)
        #if date_to is not None:
        #    where.append("e.fecha <= %s")
        #    params.append(date_to)
        #if where:
        #    sql += " WHERE " + " AND ".join(where)
        #sql += " ORDER BY p.produccion_id, e.fecha, e.scene_name"

        sql += f"""
        SELECT distinct
            pr.produccion_id,
            pr.s3_monitoring_produccion_id,
            pr.monitoring,
            pr.fecha_plantacion,
            pr.max_dias_monitoring,
            pr.fecha_fin,
            pr.prefix,
            pr.pbox,
            pr.polygon_bbox,
            e.s3_monitoring_escena_id,
            e.scene_name,
            e.fecha,
            e.cloud_cover,
            e.status,
            e.truth_tif_exists,
            e.render_tif_exists,
            e.params_exists,
            e.ia_exists,
            e.fase2_completa_at
        FROM es
        join rules r
        JOIN {cfg.mysql_target_table} pr
            ON pr.s3_monitoring_produccion_id = es.s3_monitoring_produccion_id
        JOIN {cfg.mysql_scenes_table} e
            ON e.s3_monitoring_produccion_id = pr.s3_monitoring_produccion_id
        where es.fecha between date(coalesce(r.date_from,pr.ultima_sincronizacion,pr.fecha_plantacion)) and date(coalesce(r.date_to,now()))
        ORDER BY pr.produccion_id, e.fecha, e.scene_name
        """
        print(sql)
        result=get_mysql().fetch_all(sql, tuple(params))
        return result
    def list_fechas_escenes_produccion(
        self,
        active_only: bool = False,
        production_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        sql=MySQLRepo.get_cte()
        cfg = get_runtime_config()
        #where = []
        params: list[Any] = [
            date_from,
            date_to,
            active_only,
            (",".join(cfg.s3_phase2_expected_files)),
            production_id
        ]

        sql += f"""
        select distinct p.produccion_id,date(coalesce(r.date_from,pr.ultima_sincronizacion,pr.fecha_plantacion)) fecha_inicio from p
        join rules r
        join {cfg.mysql_target_table} pr on pr.produccion_id=p.produccion_id
        ORDER BY pr.produccion_id
        """
        #print(sql)
        result=get_mysql().fetch_all(sql, tuple(params))
        return result

    def upsert_scenes(self, s3_prod_id: int, rows: list[dict]) -> int:
        if not rows:
            return 0
        cfg = get_runtime_config()
        payload = [{**r, "s3_monitoring_produccion_id": s3_prod_id} for r in rows]
        sql = f"""
            INSERT INTO {cfg.mysql_scenes_table}
                (s3_monitoring_produccion_id, scene_name, fecha,
                 scene_json_key, scene_json_uri, cloud_cover, status, ultima_sincronizacion)
            VALUES (%(s3_monitoring_produccion_id)s, %(scene_name)s, %(fecha)s,
                    %(scene_json_key)s, %(scene_json_uri)s, %(cloud_cover)s, %(status)s, NOW())
            ON DUPLICATE KEY UPDATE
                fecha = VALUES(fecha),
                scene_json_key = VALUES(scene_json_key),
                scene_json_uri = VALUES(scene_json_uri),
                cloud_cover = VALUES(cloud_cover),
                status = VALUES(status),
                ultima_sincronizacion = NOW(),
                fecha_actualizacion = NOW()
        """
        # _raw_scene no es columna: la quitamos antes de insertar
        clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in payload]
        return get_mysql().execute_many(sql, clean)

    def insert_scenes_only(self, s3_prod_id: int, rows: list[dict]) -> int:
        if not rows:
            return 0
        cfg = get_runtime_config()
        payload = [{**r, "s3_monitoring_produccion_id": s3_prod_id} for r in rows]
        sql = f"""
            INSERT INTO {cfg.mysql_scenes_table}
                (s3_monitoring_produccion_id, scene_name, fecha,
                 scene_json_key, scene_json_uri, cloud_cover, status, ultima_sincronizacion)
            VALUES (%(s3_monitoring_produccion_id)s, %(scene_name)s, %(fecha)s,
                    %(scene_json_key)s, %(scene_json_uri)s, %(cloud_cover)s, %(status)s, NOW())
        """
        clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in payload]
        return get_mysql().execute_many(sql, clean)

    def update_scene_file_flags(self, scene_id: int, flags: dict) -> int:
        cfg = get_runtime_config()
        sql = f"""
            UPDATE {cfg.mysql_scenes_table}
            SET truth_tif_exists = %(truth_tif_exists)s,
                render_tif_exists = %(render_tif_exists)s,
                params_exists = %(params_exists)s,
                fase2_completa_at = NOW(),
                fecha_actualizacion = NOW()
            WHERE s3_monitoring_escena_id = %(scene_id)s
        """
        return get_mysql().execute(sql, {**flags, "scene_id": scene_id})

    def update_scene_ia_flag(self, scene_id: int, riesgo_nivel: Optional[str],
                             fecha_analisis: Optional[str]) -> int:
        cfg = get_runtime_config()
        sql = f"""
            UPDATE {cfg.mysql_scenes_table}
            SET ia_exists = 1,
                latest_ia_riesgo_nivel = %(riesgo_nivel)s,
                latest_ia_fecha_analisis = %(fecha_analisis)s,
                fecha_actualizacion = NOW()
            WHERE s3_monitoring_escena_id = %(scene_id)s
        """
        return get_mysql().execute(
            sql, {"riesgo_nivel": riesgo_nivel, "fecha_analisis": fecha_analisis,
                  "scene_id": scene_id}
        )

    # ----------------------- Archivos -----------------------
    def get_files_by_scene(self, scene_id: int) -> list[dict]:
        cfg = get_runtime_config()
        sql = (
            f"SELECT * FROM {cfg.mysql_scene_files_table} "
            f"WHERE s3_monitoring_escena_id = %s"
        )
        return get_mysql().fetch_all(sql, (scene_id,))

    def get_files_filtered(self, scene_id: int, expected_keys: Optional[list[str]] = None) -> list[dict]:
        files = self.get_files_by_scene(scene_id)
        if not expected_keys:
            return files
        expected_set = set(expected_keys)
        return [row for row in files if row.get("s3_key") in expected_set]

    def list_files_grouped(
        self,
        active_only: bool = False,
        production_id: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        sql=MySQLRepo.get_cte()
        cfg = get_runtime_config()
        archivos = ",".join(cfg.s3_phase2_expected_files or [])
        '''sql = f"""
            WITH RECURSIVE
            ru AS (
                SELECT
                    %s AS date_from,
                    %s AS date_to,
                    %s AS active_only,
                    %s AS archivos,
                    %s AS produccion_id
            ),
            rules AS (
                SELECT
                    IF(date_from IS NOT NULL AND date_from <> '', CAST(date_from AS DATE), NULL) AS date_from,
                    IF(date_to IS NOT NULL AND date_to <> '', CAST(date_to AS DATE), NULL) AS date_to,
                    IF(active_only IS NOT NULL AND active_only <> '', active_only, 0) AS active_only,
                    IF(archivos IS NOT NULL AND archivos <> '', CAST(archivos AS CHAR(5000)), NULL) AS archivos,
                    IF(produccion_id IS NOT NULL AND produccion_id <> '', CAST(produccion_id AS UNSIGNED), NULL) AS produccion_id
                FROM ru
                LIMIT 1
            ),
            p AS (
                SELECT
                    COALESCE(DATE(p.fase2_completa_at), MIN(es.fecha), DATE(p.ultima_sincronizacion), r.date_from) AS date_from,
                    MIN(es.fecha) AS date_min,
                    p.fase2_completa_at AS phase2_sync_at,
                    p.produccion_id,
                    p.s3_monitoring_produccion_id,
                    pr.cantidad AS area,
                    azp.poligono,
                    cc.nombre AS rancho
                FROM {cfg.mysql_target_table} p
                JOIN rules r
                JOIN producciones pr
                    ON pr.produccion_id = p.produccion_id
                JOIN asignaciones_zonas_producciones azp
                    ON azp.produccion_id = pr.produccion_id
                JOIN centros_costos cc
                    ON cc.centro_costo_id = pr.centro_costo_id
                JOIN {cfg.mysql_scenes_table} es
                    ON es.s3_monitoring_produccion_id = p.s3_monitoring_produccion_id
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
            ),
            es AS (
                SELECT
                    p.produccion_id,
                    p.area,
                    p.poligono,
                    p.rancho,
                    p.phase2_sync_at,
                    es.s3_monitoring_produccion_id,
                    es.s3_monitoring_escena_id,
                    es.scene_name,
                    es.fecha,
                    GREATEST(
                        COALESCE(DATE(p.phase2_sync_at), r.date_from, p.date_from, p.date_min),
                        COALESCE(r.date_from, p.date_from, p.date_min)
                    ) AS archivos_sync
                FROM p
                JOIN rules r
                JOIN {cfg.mysql_scenes_table} es
                    ON es.s3_monitoring_produccion_id = p.s3_monitoring_produccion_id
                WHERE es.fecha BETWEEN COALESCE(r.date_from, p.date_min) AND COALESCE(r.date_to, NOW())
            ),
            d AS (
                SELECT
                    1 AS indx,
                    CAST(r.archivos AS CHAR(5000)) AS txt,
                    CAST(strSplit(r.archivos, ',', 1) AS CHAR(255)) AS valor
                FROM rules r

                UNION ALL

                SELECT
                    indx + 1,
                    txt,
                    strSplit(txt, ',', indx + 1)
                FROM d
                WHERE strSplit(txt, ',', indx + 1) IS NOT NULL
            ),
            ari AS (
                SELECT
                    es.produccion_id,
                    es.s3_monitoring_produccion_id,
                    es.s3_monitoring_escena_id,
                    es.scene_name,
                    d.valor,
                    b.s3_monitoring_escena_archivo_id,
                    b.s3_key,
                    b.s3_uri,
                    b.tipo,
                    b.extension,
                    b.size_bytes,
                    b.last_modified,
                    b.existe,
                    b.json_content
                FROM es
                JOIN d
                JOIN {cfg.mysql_scene_files_table} b
                    ON b.s3_monitoring_escena_id = es.s3_monitoring_escena_id
                WHERE d.valor IS NOT NULL
                  AND b.s3_uri LIKE CONCAT('%', d.valor) COLLATE utf8mb4_general_ci
                  AND es.fecha BETWEEN COALESCE(es.archivos_sync, r.date_from, p.date_from, p.date_min) AND COALESCE(r.date_to, NOW())
            )
            SELECT
                produccion_id,
                s3_monitoring_produccion_id,
                s3_monitoring_escena_id,
                scene_name,
                valor AS archivo,
                s3_monitoring_escena_archivo_id,
                s3_key,
                s3_uri,
                tipo,
                extension,
                size_bytes,
                last_modified,
                existe,
                json_content
            FROM ari
            ORDER BY produccion_id, scene_name, s3_key
        """
        '''
        sql+="""
            select
                ari.produccion_id,
                ari.s3_monitoring_produccion_id,
                ari.s3_monitoring_escena_id,
                ari.scene_name,
                ari.valor AS archivo,
                ar.s3_monitoring_escena_archivo_id,
                ar.s3_key,
                ar.tipo,
                ar.extension,
                ar.existe
            from ari
            join s3_monitoring_escena_archivos ar on ar.s3_monitoring_escena_archivo_id=ari.s3_monitoring_escena_archivo_id
            ORDER BY ari.produccion_id, ari.scene_name, ar.s3_key
        """
        rule_params = [date_from, date_to, 1 if active_only else 0, archivos, production_id]
        #print(rule_params)
        resultado=get_mysql().fetch_all(sql, tuple(rule_params))
        #print(resultado)
        return resultado

    def upsert_files(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        cfg = get_runtime_config()
        sql = f"""
            INSERT INTO {cfg.mysql_scene_files_table}
                (s3_monitoring_escena_id, tipo, s3_key, s3_key_hash, s3_uri,
                 extension, size_bytes, last_modified, existe, json_content)
            VALUES (%(s3_monitoring_escena_id)s, %(tipo)s, %(s3_key)s, %(s3_key_hash)s,
                    %(s3_uri)s, %(extension)s, %(size_bytes)s, %(last_modified)s,
                    %(existe)s, %(json_content)s)
            ON DUPLICATE KEY UPDATE
                tipo = VALUES(tipo),
                s3_uri = VALUES(s3_uri),
                extension = VALUES(extension),
                size_bytes = VALUES(size_bytes),
                last_modified = VALUES(last_modified),
                existe = VALUES(existe),
                json_content = VALUES(json_content),
                fecha_actualizacion = NOW()
        """
        return get_mysql().execute_many(sql, rows)

    # ----------------------- IA -----------------------
    def get_ia_by_scene(self, scene_id: int) -> Optional[dict]:
        cfg = get_runtime_config()
        sql = (
            f"SELECT * FROM {cfg.mysql_scene_ia_table} "
            f"WHERE s3_monitoring_escena_id = %s"
        )
        return get_mysql().fetch_one(sql, (scene_id,))

    def list_ia_pending(self, production_id: Optional[int] = None) -> list[dict]:
        cfg = get_runtime_config()
        sql = f"""
            SELECT
                a.s3_monitoring_escena_id,
                a.s3_key,
                a.s3_uri,
                a.tipo,
                a.extension,
                e.scene_name,
                p.produccion_id
            FROM {cfg.mysql_scene_files_table} a
            LEFT JOIN {cfg.mysql_scene_ia_table} r
                ON r.s3_monitoring_escena_id = a.s3_monitoring_escena_id
            JOIN {cfg.mysql_scenes_table} e
                ON e.s3_monitoring_escena_id = a.s3_monitoring_escena_id
            JOIN {cfg.mysql_target_table} p
                ON p.s3_monitoring_produccion_id = e.s3_monitoring_produccion_id
            WHERE a.tipo IN ('ia_json', 'ia')
              AND r.s3_monitoring_escena_ia_resumen_id IS NULL
        """
        params: list[Any] = []
        if production_id is not None:
            sql += " AND p.produccion_id = %s"
            params.append(production_id)
        sql += " ORDER BY p.produccion_id, e.scene_name"
        return get_mysql().fetch_all(sql, tuple(params))

    def upsert_ia(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        cfg = get_runtime_config()
        sql = f"""
            INSERT INTO {cfg.mysql_scene_ia_table}
                (s3_monitoring_escena_id, estado_clave, estado_general,
                 riesgo_nivel, riesgo_motivo, fecha_analisis, json_original)
            VALUES (%(s3_monitoring_escena_id)s, %(estado_clave)s, %(estado_general)s,
                    %(riesgo_nivel)s, %(riesgo_motivo)s, %(fecha_analisis)s, %(json_original)s)
            ON DUPLICATE KEY UPDATE
                estado_clave = VALUES(estado_clave),
                estado_general = VALUES(estado_general),
                riesgo_nivel = VALUES(riesgo_nivel),
                riesgo_motivo = VALUES(riesgo_motivo),
                fecha_analisis = VALUES(fecha_analisis),
                json_original = VALUES(json_original),
                fecha_actualizacion = NOW()
        """
        return get_mysql().execute_many(sql, rows)

    # ----------------------- Logs del daemon -----------------------
    def log_daemon_event(self, service_name: str, event_name: str, status: str,
                         message: str | None = None, payload_json: str | None = None) -> None:
        cfg = get_runtime_config()
        sql = f"""
            INSERT INTO {cfg.mysql_daemon_logs_table}
                (service_name, event_name, status, message, payload_json)
            VALUES (%s, %s, %s, %s, %s)
        """
        try:
            get_mysql().execute(sql, (service_name, event_name, status, message, payload_json))
        except Exception:  # noqa: BLE001
            # el logging del daemon nunca debe tumbar el flujo
            pass


_repo: Optional[MySQLRepo] = None


def get_repo() -> MySQLRepo:
    global _repo
    if _repo is None:
        _repo = MySQLRepo()
    return _repo
