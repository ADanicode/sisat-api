import os
import json
import base64
import firebase_admin
from firebase_admin import credentials, firestore

# --- INICIALIZACIÓN SEGURA DE FIREBASE ---
# Railway lee el JSON de Firebase desde una variable de entorno en Base64
# para evitar errores de saltos de línea. Compartido por las 3 APIs.
encoded_creds = os.environ.get("FIREBASE_B64")
if encoded_creds:
    decoded_creds = base64.b64decode(encoded_creds).decode("utf-8")
    cred_dict = json.loads(decoded_creds)
    cred = credentials.Certificate(cred_dict)
else:
    # Fallback para pruebas locales en tu PC
    cred = credentials.Certificate("firebase-adminsdk.json")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

db = firestore.client()
