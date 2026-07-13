import base64
import logging
import os
import re

import anthropic
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ia", tags=["ia"])

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_INSTRUCTION = """Eres un extractor de encuestas. Analiza el documento o imagen y extrae SOLO el título y las preguntas.
Devuelve ÚNICAMENTE un JSON válido con esta estructura exacta, sin texto adicional ni bloques de código:
{
  "titulo": "nombre de la encuesta",
  "preguntas": [
    {
      "id": "p1",
      "orden": 1,
      "enunciado": "texto de la pregunta",
      "tipo": "CERRADA",
      "opciones": ["opción 1", "opción 2"]
    }
  ]
}
Reglas:
- tipo siempre "CERRADA" o "ABIERTA"
- Si la pregunta tiene opciones de respuesta, tipo="CERRADA" y llena opciones[]
- Si es respuesta libre/abierta, tipo="ABIERTA" y opciones=[]
- Numera los ids como p1, p2, p3..."""


class PreguntaSalida(BaseModel):
    id: str
    orden: int
    enunciado: str
    tipo: str
    opciones: list[str]


class EncuestaSalida(BaseModel):
    titulo: str
    preguntas: list[PreguntaSalida]


@router.get("/status")
async def status():
    return {"status": "ok", "modulo": "ia", "implementado": True}


@router.post("/scan-survey/")
async def scan_survey(file: UploadFile = File(...)):
    allowed_types = ["image/jpeg", "image/png", "application/pdf"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Solo se aceptan JPG, PNG o PDF.")

    try:
        file_bytes = await file.read()
        base64_data = base64.standard_b64encode(file_bytes).decode("utf-8")

        is_pdf = file.content_type == "application/pdf"

        if is_pdf:
            content_block = {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64_data,
                },
            }
        else:
            content_block = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": file.content_type,
                    "data": base64_data,
                },
            }

        create_kwargs = dict(
            model="claude-opus-4-8",
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        content_block,
                        {"type": "text", "text": SYSTEM_INSTRUCTION},
                    ],
                }
            ],
        )
        if is_pdf:
            create_kwargs["betas"] = ["pdfs-2024-09-25"]
            response = client.beta.messages.create(**create_kwargs)
        else:
            response = client.messages.create(**create_kwargs)

        raw = response.content[0].text.strip()
        # Extraer JSON aunque el modelo agregue texto extra
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            raise HTTPException(status_code=502, detail="Claude no devolvió JSON válido.")

        encuesta = EncuestaSalida.model_validate_json(match.group())
        return encuesta.model_dump(mode="json")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error procesando documento con Claude")
        raise HTTPException(status_code=500, detail=str(e))
