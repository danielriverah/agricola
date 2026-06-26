import logging
from datetime import date, timedelta
from typing import List

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr
from boto3.dynamodb.types import TypeDeserializer

from app.core.config import DYNAMO_TABLE_PRODUCCIONES, AWS_REGION
from app.models.produccion_clima import ProduccionMonitoreo
from app.services.mysql_service import obtener_producciones_monitoreadas

logger = logging.getLogger(__name__)
_DESERIALIZER = TypeDeserializer()


def _parse_item(item: dict) -> ProduccionMonitoreo | None:
    try:
        produccion_id = int(item["produccion_id"])
        estatus = item.get("estatus", "")
        fecha_siembra = date.fromisoformat(item["fecha_siembra"])
        dias_max = int(item["dias_max_monitoreo"])

        return ProduccionMonitoreo(
            produccion_id=produccion_id,
            estatus=estatus,
            fecha_siembra=fecha_siembra,
            dias_max_monitoreo=dias_max,
        )
    except (KeyError, ValueError) as e:
        logger.warning("Item DynamoDB inválido, se omite: %s — %s", item, e)
        return None


def _deserialize_ddb_item(item: dict) -> dict:
    return {k: _DESERIALIZER.deserialize(v) for k, v in item.items()}


def _batch_get_producciones_dynamo(produccion_ids: list[int]) -> dict[int, dict]:
    if not produccion_ids:
        return {}

    client = boto3.client("dynamodb", region_name=AWS_REGION)
    result: dict[int, dict] = {}
    keys = [{"produccion_id": {"N": str(pid)}} for pid in produccion_ids]

    # BatchGetItem maneja hasta 100 claves por request.
    for i in range(0, len(keys), 100):
        chunk = keys[i : i + 100]
        request = {
            DYNAMO_TABLE_PRODUCCIONES: {
                "Keys": chunk,
                "ConsistentRead": False,
            }
        }
        while request:
            try:
                response = client.batch_get_item(RequestItems=request)
            except ClientError as exc:
                logger.warning(
                    "BatchGetItem falló, se usará scan como respaldo: %s",
                    exc.response.get("Error", {}).get("Message", str(exc)),
                )
                return {}

            for raw_item in response.get("Responses", {}).get(DYNAMO_TABLE_PRODUCCIONES, []):
                item = _deserialize_ddb_item(raw_item)
                try:
                    pid = int(item.get("produccion_id"))
                    result[pid] = item
                except Exception:
                    logger.warning("Item DynamoDB batch inválido, se omite: %s", item)
            unprocessed = response.get("UnprocessedKeys", {})
            request = unprocessed if unprocessed else {}
    return result


def _scan_producciones_dynamo_missing(produccion_ids: list[int]) -> dict[int, dict]:
    """
    Fallback cuando BatchGet no encuentra la key real de la tabla o el schema
    no coincide con la expectativa. Escanea solo por ids faltantes y estatus OPEN.
    """
    if not produccion_ids:
        return {}

    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    tabla = dynamodb.Table(DYNAMO_TABLE_PRODUCCIONES)
    result: dict[int, dict] = {}

    # DynamoDB limita el tamaño de los filtros IN; hacemos chunks pequeños.
    chunk_size = 50
    for i in range(0, len(produccion_ids), chunk_size):
        chunk = produccion_ids[i : i + chunk_size]
        if not chunk:
            continue
        kwargs: dict = {
            "FilterExpression": Attr("estatus").eq("OPEN") & Attr("produccion_id").is_in(chunk)
        }
        while True:
            response = tabla.scan(**kwargs)
            for raw_item in response.get("Items", []):
                try:
                    pid = int(raw_item.get("produccion_id"))
                    result[pid] = raw_item
                except Exception:
                    logger.warning("Item DynamoDB scan inválido, se omite: %s", raw_item)
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key

    return result


def _scan_produccion_dynamo_por_id(produccion_id: int) -> dict | None:
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    tabla = dynamodb.Table(DYNAMO_TABLE_PRODUCCIONES)
    response = tabla.scan(
        FilterExpression=Attr("produccion_id").eq(produccion_id),
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None


def obtener_producciones_activas() -> List[ProduccionMonitoreo]:
    """
    Retorna producciones primero desde MySQL con monitoring = 1 y luego
    completa fecha_siembra/dias_max_monitoreo desde DynamoDB.
    """
    mysql_rows = obtener_producciones_monitoreadas()
    if not mysql_rows:
        logger.info("Producciones activas encontradas en MySQL: 0")
        return []

    produccion_ids = []
    mysql_by_id: dict[int, dict] = {}
    for row in mysql_rows:
        try:
            pid = int(row.get("produccion_id"))
        except Exception:
            continue
        produccion_ids.append(pid)
        mysql_by_id[pid] = row

    dynamo_items = _batch_get_producciones_dynamo(produccion_ids)
    missing_ids = [pid for pid in produccion_ids if pid not in dynamo_items]
    if missing_ids:
        fallback_items = _scan_producciones_dynamo_missing(missing_ids)
        dynamo_items.update(fallback_items)
    hoy = date.today()
    producciones: List[ProduccionMonitoreo] = []

    for pid in produccion_ids:
        item = dynamo_items.get(pid)
        if not item:
            logger.debug("Producción %s está en MySQL pero no existe en DynamoDB; se omite.", pid)
            continue

        prod = _parse_item(item)
        if prod is None:
            continue
        if str(prod.estatus or "").strip().upper() != "OPEN":
            logger.debug("Producción %s no está OPEN en DynamoDB; se omite.", pid)
            continue
        mysql_row = mysql_by_id.get(pid, {})
        prod.poligono = mysql_row.get("poligono")

        fecha_cierre = prod.fecha_siembra + timedelta(days=prod.dias_max_monitoreo)
        if hoy <= fecha_cierre:
            producciones.append(prod)
        else:
            logger.debug(
                "Producción %s excedió días de monitoreo (%s), se omite.",
                prod.produccion_id, fecha_cierre,
            )

    logger.info(
        "Producciones activas encontradas: mysql=%d dynamo=%d activas=%d",
        len(mysql_rows),
        len(dynamo_items),
        len(producciones),
    )
    return producciones


def actualizar_ultima_fecha_consultada(produccion_id: int, fecha: date) -> None:
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    tabla = dynamodb.Table(DYNAMO_TABLE_PRODUCCIONES)
    tabla.update_item(
        Key={"produccion_id": produccion_id},
        UpdateExpression="SET ultima_fecha_consultada = :f",
        ExpressionAttributeValues={":f": fecha.isoformat()},
    )


def obtener_produccion_dynamo(produccion_id: int) -> dict | None:
    """
    Retorna el registro crudo de DynamoDB para una producción específica.
    """
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    tabla = dynamodb.Table(DYNAMO_TABLE_PRODUCCIONES)
    try:
        response = tabla.get_item(Key={"produccion_id": produccion_id})
        item = response.get("Item")
        if item:
            return item
    except ClientError as exc:
        logger.warning(
            "GetItem falló para produccion_id=%s, se usará scan: %s",
            produccion_id,
            exc.response.get("Error", {}).get("Message", str(exc)),
        )
    return _scan_produccion_dynamo_por_id(produccion_id)


def obtener_producciones_dynamo_crudas() -> list[dict]:
    """
    Retorna los registros crudos de DynamoDB para las producciones monitoreadas
    en MySQL, sin enriquecer ni filtrar por fecha/días máximos.
    """
    mysql_rows = obtener_producciones_monitoreadas()
    if not mysql_rows:
        return []

    produccion_ids: list[int] = []
    for row in mysql_rows:
        try:
            produccion_ids.append(int(row.get("produccion_id")))
        except Exception:
            continue

    dynamo_items = _batch_get_producciones_dynamo(produccion_ids)
    missing_ids = [pid for pid in produccion_ids if pid not in dynamo_items]
    if missing_ids:
        dynamo_items.update(_scan_producciones_dynamo_missing(missing_ids))

    resultado: list[dict] = []
    for pid in produccion_ids:
        item = dynamo_items.get(pid)
        if item:
            resultado.append(item)
    return resultado
