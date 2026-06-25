import os
import json
import tempfile
from fastapi import FastAPI, UploadFile, File, HTTPException
import google.generativeai as genai
from pydantic import BaseModel
from dotenv import load_dotenv

# Cargar variables locales
load_dotenv()

app = FastAPI(title="Escáner Inteligente de Encuestas")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("La variable de entorno GEMINI_API_KEY no está configurada.")

genai.configure(api_key=GEMINI_API_KEY)

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

model = genai.GenerativeModel(
    model_name="gemini-flash-lite-latest", # <-- Cambiamos al modelo ligero y rápido
    system_instruction=SYSTEM_INSTRUCTION,
    generation_config={"response_mime_type": "application/json"}
)
@app.post("/scan-survey/")
async def scan_survey(file: UploadFile = File(...)):
    allowed_types = ["image/jpeg", "image/png", "application/pdf"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Tipo de archivo no soportado. Sube un JPG, PNG o PDF.")

    temp_file_path = None
    uploaded_gemini_file = None

    try:
        # 1. Guardar el archivo en una ruta temporal del servidor local
        ext = ".pdf" if file.content_type == "application/pdf" else ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as temp_file:
            temp_file.write(await file.read())
            temp_file_path = temp_file.name

        # 2. Subir el archivo físicamente a la File API de Gemini
        uploaded_gemini_file = genai.upload_file(path=temp_file_path, mime_type=file.content_type)

        # 3. Consumir la API usando la referencia del archivo subido
        prompt_parts = [
            uploaded_gemini_file,
            "Analiza este documento y devuelve el JSON correspondiente."
        ]
        
        response = model.generate_content(prompt_parts)
        
        # 4. Retornar la data
        return json.loads(response.text)

    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="El modelo no devolvió un JSON válido.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error procesando en Gemini: {str(e)}")
    
    finally:
        # 5. LIMPIEZA ESTRICTA (Crucial para no colapsar el backend ni la capa gratuita)
        
        # Eliminar el archivo de los servidores de Google
        if uploaded_gemini_file:
            try:
                genai.delete_file(uploaded_gemini_file.name)
            except Exception as cleanup_error:
                print(f"Advertencia: No se pudo borrar el archivo de Gemini: {cleanup_error}")
                
        # Eliminar el archivo temporal del disco de tu servidor
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)