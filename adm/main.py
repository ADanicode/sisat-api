import base64
import json
import os
from typing import Any

import firebase_admin
from fastapi import Body, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from firebase_admin import auth, credentials, firestore
from firebase_admin.exceptions import FirebaseError
from pydantic import BaseModel, EmailStr, Field

app = FastAPI(title="SISET ACL y Estado de Cuestionarios")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_firebase_credentials() -> credentials.Base:
    encoded_creds = os.environ.get("FIREBASE_B64")
    if encoded_creds:
        try:
            decoded_creds = base64.b64decode(encoded_creds).decode("utf-8")
            cred_dict = json.loads(decoded_creds)
            return credentials.Certificate(cred_dict)
        except Exception as exc:
            raise RuntimeError(f"No se pudieron leer las credenciales de Firebase: {exc}") from exc

    return credentials.ApplicationDefault()


def _init_firebase() -> firestore.Client:
    if not firebase_admin._apps:
        firebase_admin.initialize_app(_load_firebase_credentials())
    return firestore.client()


db = _init_firebase()


class UsuarioCreate(BaseModel):
    nombre: str = Field(min_length=1)
    email: EmailStr
    password: str = Field(min_length=6)
    rol_principal: str = Field(min_length=1)
    permisos: list[str] = Field(default_factory=list)
    asignacion_territorial: dict[str, list[str]] = Field(
        default_factory=lambda: {"distritos": [], "municipios": [], "secciones": []}
    )


class UsuarioEstadoUpdate(BaseModel):
    activo: bool


class EncuestaEstadoUpdate(BaseModel):
    activo: bool


@app.post("/usuarios/", status_code=status.HTTP_201_CREATED)
def crear_usuario(payload: UsuarioCreate) -> dict[str, Any]:
    usuario_id = payload.email.split("@")[0]

    try:
        usuario_auth = auth.create_user(
            email=str(payload.email),
            password=payload.password,
            display_name=payload.nombre,
            disabled=False,
        )
    except auth.EmailAlreadyExistsError as exc:
        raise HTTPException(status_code=400, detail="El correo ya existe en Firebase Auth.") from exc
    except FirebaseError as exc:
        raise HTTPException(status_code=502, detail=f"Error creando usuario en Firebase Auth: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error inesperado creando usuario en Auth: {exc}") from exc

    documento = {
        "usuario_id": usuario_id,
        "uid": usuario_auth.uid,
        "nombre": payload.nombre,
        "email": str(payload.email),
        "rol_principal": payload.rol_principal,
        "permisos": payload.permisos,
        "asignacion_territorial": payload.asignacion_territorial,
        "estado_activo": True,
    }

    try:
        db.collection("usuarios").document(usuario_id).set(documento)
    except Exception as exc:
        try:
            auth.delete_user(usuario_auth.uid)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Error creando documento en Firestore: {exc}") from exc

    return {
        "mensaje": "Usuario creado correctamente",
        "usuario_id": usuario_id,
        "uid": usuario_auth.uid,
        "usuario": documento,
    }


@app.put("/usuarios/{usuario_id}/estado")
def actualizar_estado_usuario(usuario_id: str, activo: bool = Body(...)) -> dict[str, Any]:
    try:
        doc_ref = db.collection("usuarios").document(usuario_id)
        snapshot = doc_ref.get()
        if not snapshot.exists:
            raise HTTPException(status_code=404, detail="Usuario no encontrado.")

        doc_ref.update({"estado_activo": activo})

        uid = snapshot.to_dict().get("uid")
        if uid:
            auth.update_user(uid, disabled=not activo)

        return {
            "mensaje": "Estado de usuario actualizado correctamente",
            "usuario_id": usuario_id,
            "estado_activo": activo,
        }
    except HTTPException:
        raise
    except FirebaseError as exc:
        raise HTTPException(status_code=502, detail=f"Error actualizando estado en Firebase: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error actualizando estado del usuario: {exc}") from exc


@app.put("/encuestas/{cuestionario_id}/estado")
def actualizar_estado_encuesta(cuestionario_id: str, activo: bool = Body(...)) -> dict[str, Any]:
    try:
        doc_ref = db.collection("cuestionarios_config").document(cuestionario_id)
        snapshot = doc_ref.get()
        if not snapshot.exists:
            raise HTTPException(status_code=404, detail="Cuestionario no encontrado.")

        doc_ref.update({"activo": activo})

        return {
            "mensaje": "Estado de encuesta actualizado correctamente",
            "cuestionario_id": cuestionario_id,
            "activo": activo,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error actualizando estado de encuesta: {exc}") from exc


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "ok", "servicio": "adm-acl-cuestionarios"}
