from fastapi import FastAPI

from shared.firebase import db  # noqa: F401  (inicializa Firebase al importarse)
from documentos.router import router as documentos_router
from ia.router import router as ia_router
from audios.router import router as audios_router

app = FastAPI(title="SISAT Backend", description="Motor de Exportación, IA y Gestión de Audios")

app.include_router(documentos_router)
app.include_router(ia_router)
app.include_router(audios_router)


@app.get("/")
async def root():
    return {"status": "ok", "servicio": "sisat-api"}
