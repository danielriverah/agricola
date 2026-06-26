import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException
from app.jobs.manager import get_job_manager
from app.services.dynamo_service import obtener_producciones_activas
from app.services.clima_sync_service import sincronizar_produccion

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/all")
def sync_all():
    """
    Ejecuta el ciclo completo de monitoreo climático para todas las
    producciones activas. Equivalente al run diario programado.
    """
    producciones = obtener_producciones_activas()
    jm = get_job_manager()
    job, active = jm.try_create_heavy(kind="sync_all", total=len(producciones))
    if not job:
        return {
            "job_id": active.id if active else None,
            "status": active.status.value if active else "running",
            "message": "Ya hay un job pesado activo. Consulta ese job_id.",
        }

    def _run(j):
        inicio = datetime.utcnow()
        if not producciones:
            j.progress_note = "Sin producciones activas para monitorear"
            j.results = []
            return

        resultados = []
        ok_count = 0
        error_count = 0

        for index, prod in enumerate(producciones, start=1):
            if j.cancel_requested():
                j.progress_note = f"Cancelado en {index - 1}/{len(producciones)}"
                break
            j.progress_note = f"{index}/{len(producciones)}: sincronizando producción {prod.produccion_id}"
            resultado = sincronizar_produccion(prod)
            resultados.append(resultado)
            j.results = resultados.copy()
            j.processed = index
            if resultado.get("ok"):
                ok_count += 1
            else:
                error_count += 1

        duracion = round((datetime.utcnow() - inicio).total_seconds(), 1)
        j.progress_note = f"Terminado. OK={ok_count} errores={error_count} duración={duracion}s"
        j.results = [{
            "ok": error_count == 0,
            "procesadas": len(resultados),
            "ok_count": ok_count,
            "error_count": error_count,
            "duracion_seg": duracion,
            "resultados": resultados,
        }]
        logger.info("sync/all OK=%d errores=%d duración=%.1fs", ok_count, error_count, duracion)

    jm.run_async(job, _run)
    return {"job_id": job.id}


@router.post("/{produccion_id}")
def sync_one(produccion_id: int):
    """
    Ejecuta el ciclo de monitoreo climático para una sola producción.
    Útil para re-procesar o testear individualmente.
    """
    producciones = obtener_producciones_activas()
    prod = next((p for p in producciones if p.produccion_id == produccion_id), None)

    if not prod:
        raise HTTPException(
            status_code=404,
            detail=f"Producción {produccion_id} no encontrada en DynamoDB o no está activa.",
        )

    jm = get_job_manager()
    job, active = jm.try_create_heavy(kind=f"sync_one:{produccion_id}", total=1)
    if not job:
        return {
            "job_id": active.id if active else None,
            "status": active.status.value if active else "running",
            "message": "Ya hay un job pesado activo. Consulta ese job_id.",
        }

    def _run(j):
        if j.cancel_requested():
            j.progress_note = f"Cancelado antes de iniciar producción {produccion_id}"
            return
        j.progress_note = f"1/1: sincronizando producción {produccion_id}"
        resultado = sincronizar_produccion(prod)
        j.results = [resultado]
        j.processed = 1
        j.progress_note = f"1/1: producción {produccion_id} terminada"

    jm.run_async(job, _run)
    return {"job_id": job.id}
