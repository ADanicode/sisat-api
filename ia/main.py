import base64
import logging
import os
import re

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

try:
    import anthropic
    _client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
except Exception as e:
    _client = None
    logging.getLogger(__name__).error(f"anthropic no disponible: {e}")

app = FastAPI(title="Escáner Inteligente de Encuestas")
logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """Eres un extractor de encuestas. Analiza el documento o imagen y extrae SOLO el titulo y las preguntas.
Devuelve UNICAMENTE un JSON valido con esta estructura exacta, sin texto adicional ni bloques de codigo:
{
  "titulo": "nombre de la encuesta",
  "preguntas": [
    {
      "id": "p1",
      "orden": 1,
      "enunciado": "texto de la pregunta",
      "tipo": "CERRADA",
      "opciones": ["opcion 1", "opcion 2"],
      "rubros": [],
      "escala_max": 10
    }
  ]
}
Reglas:
- tipo puede ser "CERRADA", "ABIERTA" o "MATRIZ"
- Si la pregunta tiene opciones de respuesta, tipo="CERRADA" y llena opciones[]
- Si es respuesta libre/abierta, tipo="ABIERTA" y opciones=[]
- Si la pregunta es una TABLA o MATRIZ donde se evaluan varios rubros/conceptos/temas en una escala numerica (ej: "Califique del 0 al 10 los siguientes rubros"), tipo="MATRIZ", rubros=["rubro1","rubro2",...] y escala_max=el valor maximo de la escala (ej: 10). En este caso opciones=[]
- Si la pregunta es una TABLA donde se evaluan varios rubros/personas/conceptos con opciones NO numericas (ej: "Digame si conoce a las siguientes personas: Si/No"), tipo="MATRIZ", rubros=["persona1","persona2",...], opciones=["Si","No"] y escala_max=0
- IMPORTANTE: Una pregunta simple con opciones Si/No (sin tabla ni rubros) es tipo="CERRADA" con opciones=["Si","No"], NO es MATRIZ
- Para preguntas NO matriz, rubros=[] y escala_max=10
- Numera los ids como p1, p2, p3...
- No uses caracteres especiales como em-dash, usa guion normal
- IMPORTANTE: Asegurate de cerrar correctamente TODOS los corchetes y llaves del JSON. El JSON debe ser valido y completo."""


class PreguntaSalida(BaseModel):
    id: str
    orden: int
    enunciado: str
    tipo: str
    opciones: list[str] = []
    rubros: list[str] = []
    escala_max: int = 10


class EncuestaSalida(BaseModel):
    titulo: str
    preguntas: list[PreguntaSalida]


@app.get("/")
async def root():
    return {"status": "ok", "modulo": "ia"}


def _try_fix_truncated_json(raw: str) -> str | None:
    """Intenta reparar JSON truncado cerrando corchetes/llaves faltantes."""
    match = re.search(r'\{', raw)
    if not match:
        return None
    json_str = raw[match.start():]
    open_braces = json_str.count('{') - json_str.count('}')
    open_brackets = json_str.count('[') - json_str.count(']')
    if open_braces <= 0 and open_brackets <= 0:
        return None
    json_str = json_str.rstrip().rstrip(',')
    json_str += ']' * open_brackets + '}' * open_braces
    return json_str


@app.post("/scan-survey/")
async def scan_survey(file: UploadFile = File(...)):
    if _client is None:
        raise HTTPException(status_code=503, detail="anthropic no instalado en este servidor")

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

        response = _client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=16384,
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

        raw = response.content[0].text.strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)

        if match:
            try:
                encuesta = EncuestaSalida.model_validate_json(match.group())
                return encuesta.model_dump(mode="json")
            except Exception:
                pass

        # Si el JSON está truncado, intentar reparar
        fixed = _try_fix_truncated_json(raw)
        if fixed:
            try:
                encuesta = EncuestaSalida.model_validate_json(fixed)
                logger.warning("JSON truncado reparado exitosamente")
                return encuesta.model_dump(mode="json")
            except Exception:
                pass

        raise HTTPException(status_code=502, detail="Claude no devolvió JSON válido.")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error procesando documento con Claude")
        raise HTTPException(status_code=500, detail=str(e))
