import os
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
import boto3
from botocore.client import Config as BotoConfig

from shared.firebase import db

router = APIRouter(prefix="/audio", tags=["audios"])

# --- CLIENTE CLOUDFLARE R2 (compatible con S3) ---
# Las credenciales viven SOLO en variables de entorno de Railway, nunca en
# el codigo ni en la app movil. Configurar en Railway:
#   R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME
r2_client = boto3.client(
    "s3",
    endpoint_url=os.environ.get("R2_ENDPOINT"),
    aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
    config=BotoConfig(signature_version="s3v4"),
    region_name="auto",
)
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "sisat-audios")


# --- SUBIDA DE AUDIO A CLOUDFLARE R2 ---
@router.post("/subir")
async def subir_audio(
    registro_id: str = Form(...),
    audio: UploadFile = File(...)
):
    """
    Recibe el audio grabado por el encuestador (desde la app, offline-first)
    y lo sube a Cloudflare R2. La app nunca toca las credenciales de R2.
    """
    if not r2_client.meta.endpoint_url or not os.environ.get("R2_ACCESS_KEY_ID"):
        raise HTTPException(
            status_code=500,
            detail="R2 no está configurado en el servidor (faltan variables de entorno)."
        )

    extension = os.path.splitext(audio.filename or "")[1] or ".m4a"
    object_key = f"audios/{registro_id}{extension}"

    try:
        contenido = await audio.read()
        r2_client.put_object(
            Bucket=R2_BUCKET_NAME,
            Key=object_key,
            Body=contenido,
            ContentType=audio.content_type or "audio/mp4",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error subiendo a R2: {e}")

    # Guarda la referencia del audio en el registro de Firestore correspondiente.
    try:
        db.collection("registros_campo").document(registro_id).update({
            "audio_key": object_key,
            "audio_sincronizado": True,
        })
    except Exception:
        # El audio ya quedó en R2 aunque el registro aun no exista en Firestore
        # (puede llegar despues, por ejemplo si la encuesta se sincroniza por separado).
        pass

    return {"status": "ok", "registro_id": registro_id, "object_key": object_key}
