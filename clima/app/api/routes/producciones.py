from fastapi import APIRouter, HTTPException, Query
from datetime import date
from app.services.dynamo_service import (
    obtener_producciones_activas,
    obtener_produccion_dynamo,
    obtener_producciones_dynamo_crudas,
)
from app.services.mysql_service import (
    obtener_historial_monitoreo_producciones,
    obtener_resumen_monitoreo_paginado,
    obtener_ultimas_fechas_confirmadas,
    obtener_producciones_monitoreadas,
)
from app.utils.geo_utils import primer_punto_poligono

router = APIRouter(prefix="/producciones", tags=["producciones"])


@router.get("/activas")
def listar_producciones_activas():
    """
    Retorna todas las producciones con estatus OPEN que están dentro
    de su ventana de monitoreo (fecha_siembra + dias_max_monitoreo).
    """
    producciones = obtener_producciones_activas()
    ultimas_fechas = obtener_ultimas_fechas_confirmadas([p.produccion_id for p in producciones])
    resultado = []
    for p in producciones:
        poligono = p.poligono
        coords = primer_punto_poligono(poligono) if poligono else None
        ultima = ultimas_fechas.get(p.produccion_id)
        fecha_inicio_historico = ultima or p.fecha_siembra
        fecha_fin_historico = ultima
        motivo_inicio_historico = (
            "con_registros_usando_ultima_confirmada"
            if ultima
            else "sin_registros_usando_fecha_siembra"
        )
        dias_transcurridos_desde_siembra = (date.today() - p.fecha_siembra).days
        resultado.append({
            "produccion_id": p.produccion_id,
            "estatus": p.estatus,
            "fecha_siembra": p.fecha_siembra.isoformat(),
            "dias_max_monitoreo": p.dias_max_monitoreo,
            "dias_transcurridos_desde_siembra": dias_transcurridos_desde_siembra,
            "ultima_fecha_confirmada_mysql": ultima.isoformat() if ultima else None,
            "fecha_inicio_historico": fecha_inicio_historico.isoformat() if fecha_inicio_historico else None,
            "fecha_fin_historico": fecha_fin_historico.isoformat() if fecha_fin_historico else None,
            "motivo_inicio_historico": motivo_inicio_historico,
            "lat": coords[0] if coords else None,
            "lon": coords[1] if coords else None,
            "poligono": poligono,
            "tiene_poligono": poligono is not None,
        })
    return {"total": len(resultado), "producciones": resultado}


@router.get("/mysql")
def listar_producciones_mysql():
    """
    Devuelve el conjunto base de producciones monitoreadas desde MySQL,
    sin enriquecer con DynamoDB.
    """
    rows = obtener_producciones_monitoreadas()
    resultado = []
    for row in rows:
        resultado.append({
            "produccion_id": row.get("produccion_id"),
            "estatus": row.get("estatus"),
            "monitoring": row.get("monitoring"),
            "fecha": row.get("fecha").isoformat() if row.get("fecha") else None,
            "poligono": row.get("poligono"),
            "area_asig": row.get("area_asig"),
        })
    return {"total": len(resultado), "producciones": resultado}


@router.get("/mysql/{produccion_id}")
def obtener_produccion_mysql_view(produccion_id: int):
    """
    Devuelve una producción específica desde la base cruda de MySQL.
    """
    rows = obtener_producciones_monitoreadas()
    for row in rows:
        try:
            pid = int(row.get("produccion_id"))
        except Exception:
            continue
        if pid == produccion_id:
            return {
                "produccion_id": pid,
                "item": {
                    "produccion_id": row.get("produccion_id"),
                    "estatus": row.get("estatus"),
                    "monitoring": row.get("monitoring"),
                    "fecha": row.get("fecha").isoformat() if row.get("fecha") else None,
                    "poligono": row.get("poligono"),
                    "area_asig": row.get("area_asig"),
                },
            }
    raise HTTPException(status_code=404, detail=f"Producción {produccion_id} no encontrada en MySQL.")


@router.get("/dynamo")
def listar_producciones_dynamo():
    """
    Devuelve el conjunto crudo de producciones en DynamoDB asociadas a las
    producciones monitoreadas en MySQL, sin filtrar por ventana de días ni
    enriquecer con MySQL.
    """
    items = obtener_producciones_dynamo_crudas()
    return {"total": len(items), "producciones": items}


@router.get("/dynamo/{produccion_id}")
def obtener_produccion_dynamo_view(produccion_id: int):
    """
    Devuelve el registro crudo de DynamoDB para una producción específica.
    """
    item = obtener_produccion_dynamo(produccion_id)
    if not item:
        items = obtener_producciones_dynamo_crudas()
        for candidate in items:
            try:
                if int(candidate.get("produccion_id")) == produccion_id:
                    item = candidate
                    break
            except Exception:
                continue
    if not item:
        raise HTTPException(status_code=404, detail=f"Producción {produccion_id} no encontrada en DynamoDB.")
    return {"produccion_id": produccion_id, "item": item}


@router.get("/monitoreo")
def listar_producciones_monitoreo(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    limit_historial: int = Query(20, ge=1, le=200),
):
    """
    Devuelve todas las producciones monitoreadas con un resumen y su historial
    reciente de temperatura para construir gráficas en la vista.
    """
    total, resultado, _ = obtener_resumen_monitoreo_paginado(
        page=page,
        page_size=page_size,
        limit_historial=limit_historial,
    )
    total_pages = (total + page_size - 1) // page_size if total else 0

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "limit_historial": limit_historial,
        "producciones": resultado,
    }
