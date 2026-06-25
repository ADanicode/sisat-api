from fastapi import APIRouter

router = APIRouter(prefix="/ia", tags=["ia"])


@router.get("/status")
async def status():
    """
    Placeholder: aqui se agregaran las funciones de IA (ej. transcripcion
    de audios de encuestas, analisis de sentimiento de respuestas abiertas,
    resumenes automaticos, etc.) cuando esten definidas.
    """
    return {"status": "ok", "modulo": "ia", "implementado": False}
