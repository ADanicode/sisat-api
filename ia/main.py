import base64
import json
import logging
import os

from fastapi import FastAPI, UploadFile, File, HTTPException
from openai import OpenAI
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Cargar variables locales
load_dotenv()

app = FastAPI(title="Escáner Inteligente de Encuestas")
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise RuntimeError("La variable de entorno GROQ_API_KEY no está configurada.")

client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")


class PreguntaSalida(BaseModel):
    id: str
    orden: int
    enunciado: str
    tipo: str
    opciones: list[str] = Field(default_factory=list)


class EncuestaSalida(BaseModel):
    titulo: str
    preguntas: list[PreguntaSalida] = Field(default_factory=list)

SYSTEM_INSTRUCTION = """
Eres un sistema experto en extracción de datos de documentos estructurados.
Tu tarea es analizar la imagen o PDF de una encuesta en papel y extraer su contenido estrictamente en formato JSON.

Reglas de extracción:
1. Identifica el título principal de la encuesta.
2. Extrae cada pregunta. Asigna un ID único ("p1", "p2", etc.) y un número de orden secuencial.
3. Determina el tipo de pregunta:
   - "CERRADA": Si tiene opciones múltiples de selección.
   - "ABIERTA": Si tiene líneas en blanco o espacio para que el usuario escriba texto libre.
4. Si es "CERRADA", extrae todas las opciones disponibles en un arreglo de strings. Si es "ABIERTA", el arreglo de opciones debe estar vacío [].
5. Corrige errores ortográficos menores generados por el escaneo, pero mantén la intención original de la pregunta.

El formato de salida debe ser ESTRICTAMENTE el siguiente esquema JSON:
{
  "titulo": "Título de la encuesta",
  "preguntas": [
    {
      "id": "p1",
      "orden": 1,
      "enunciado": "¿Cuál es su edad?",
      "tipo": "CERRADA",
      "opciones": ["18-25", "26-35", "36+"]
    }
  ]
}
"""

@app.post("/scan-survey/")
async def scan_survey(file: UploadFile = File(...)):
    allowed_types = ["image/jpeg", "image/png", "application/pdf"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Tipo de archivo no soportado. Sube un JPG, PNG o PDF.")

    try:
        file_bytes = await file.read()
        base64_image = base64.b64encode(file_bytes).decode("utf-8")

        messages = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analiza este documento y devuelve el JSON correspondiente."},
                    {"type": "image_url", "image_url": {"url": f"data:{file.content_type};base64,{base64_image}"}},
                ],
            },
        ]

        response = client.chat.completions.create(
            model="llama-3.1-70b-versatile",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0,
        )

        response_content = response.choices[0].message.content if response.choices else None
        if not response_content:
            raise HTTPException(status_code=502, detail="Groq no devolvió contenido.")

        encuesta = EncuestaSalida.model_validate_json(response_content)
        return encuesta.model_dump(mode="json")

    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="El modelo no devolvió un JSON válido.")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error procesando documento con Groq")
        raise HTTPException(status_code=500, detail=f"Error procesando en Groq: {str(e)}")
