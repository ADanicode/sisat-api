import base64
import json
import io
import os
from datetime import datetime
from typing import Any

import firebase_admin
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from firebase_admin import credentials, firestore
from fpdf import FPDF

app = FastAPI(title="Microservicio Reportes SISAT")


def _init_firebase() -> firestore.Client:
    if not firebase_admin._apps:
        encoded_creds = os.environ.get("FIREBASE_B64")
        if not encoded_creds:
            raise RuntimeError("FIREBASE_B64 no esta configurada.")

        decoded_creds = base64.b64decode(encoded_creds).decode("utf-8")
        cred_dict = json.loads(decoded_creds)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)

    return firestore.client()


db = _init_firebase()


def aplanar_diccionario(d: Any, parent_key: str = "", sep: str = "_") -> dict[str, Any]:
    items: dict[str, Any] = {}

    if isinstance(d, dict):
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else str(k)
            if isinstance(v, (dict, list)):
                items.update(aplanar_diccionario(v, new_key, sep=sep))
            else:
                items[new_key] = v
    elif isinstance(d, list):
        for i, v in enumerate(d):
            new_key = f"{parent_key}{sep}{i}" if parent_key else str(i)
            if isinstance(v, (dict, list)):
                items.update(aplanar_diccionario(v, new_key, sep=sep))
            else:
                items[new_key] = v
    else:
        items[parent_key] = d

    return items


def obtener_datos(cuestionario_id: str) -> pd.DataFrame:
    docs = db.collection("registros_campo").where(
        filter=firestore.FieldFilter("cuestionario_id", "==", cuestionario_id)
    ).stream()

    registros_aplanados: list[dict[str, Any]] = []
    for doc in docs:
        data = doc.to_dict() or {}
        plano = aplanar_diccionario(data)
        plano["documento_id"] = doc.id
        registros_aplanados.append(plano)

    if not registros_aplanados:
        return pd.DataFrame()

    df = pd.DataFrame(registros_aplanados)
    return df.fillna("N/D")


def obtener_nombre_cuestionario(cuestionario_id: str) -> str:
    try:
        doc = db.collection("cuestionarios_config").document(cuestionario_id).get()
        if doc.exists:
            data = doc.to_dict()
            return data.get("nombre") or data.get("titulo") or cuestionario_id
    except Exception:
        pass
    return cuestionario_id


def obtener_preguntas_config(cuestionario_id: str) -> dict[str, str]:
    """Retorna {id_pregunta: texto_pregunta} del cuestionario."""
    try:
        doc = db.collection("cuestionarios_config").document(cuestionario_id).get()
        if doc.exists:
            data = doc.to_dict()
            preguntas = data.get("preguntas", [])
            return {p.get("id", ""): p.get("texto") or p.get("enunciado", "") for p in preguntas if p.get("id")}
    except Exception:
        pass
    return {}


def columnas_respuestas(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith("respuestas_")]


def renombrar_columnas_respuestas(cols: list[str], preguntas: dict[str, str]) -> dict[str, str]:
    """Mapea columnas aplanadas como 'respuestas_p1' a texto legible."""
    mapping = {}
    for col in cols:
        preg_id = col.replace("respuestas_", "", 1)
        texto = preguntas.get(preg_id, preg_id)
        mapping[col] = texto
    return mapping


# ─── EXCEL ───────────────────────────────────────────────────────────

@app.get("/reporte/excel/{cuestionario_id}")
def reporte_excel(cuestionario_id: str) -> StreamingResponse:
    df = obtener_datos(cuestionario_id)
    if df.empty:
        raise HTTPException(status_code=404, detail="No se encontraron datos para el cuestionario indicado.")

    nombre_cuestionario = obtener_nombre_cuestionario(cuestionario_id)
    preguntas = obtener_preguntas_config(cuestionario_id)
    cols_resp = columnas_respuestas(df)
    mapping = renombrar_columnas_respuestas(cols_resp, preguntas)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        # Hoja 1: Resumen general
        resumen_data = {
            "Metrica": [
                "Cuestionario",
                "Total Encuestas",
                "Total Variables",
                "Fecha de Generacion",
            ],
            "Valor": [
                nombre_cuestionario,
                len(df),
                len(df.columns),
                datetime.now().strftime("%Y-%m-%d %H:%M"),
            ],
        }
        pd.DataFrame(resumen_data).to_excel(writer, sheet_name="Resumen", index=False)

        # Hoja 2: Datos crudos con nombres legibles
        df_legible = df.copy()
        df_legible.rename(columns=mapping, inplace=True)
        df_legible.to_excel(writer, sheet_name="Datos", index=False)

        # Hoja 3: Frecuencias por pregunta
        if cols_resp:
            freq_rows = []
            for col in cols_resp:
                texto = mapping.get(col, col)
                conteo = df[col].value_counts(dropna=False)
                total = conteo.sum()
                for valor, cuenta in conteo.items():
                    freq_rows.append({
                        "Pregunta": texto,
                        "Respuesta": str(valor),
                        "Frecuencia": int(cuenta),
                        "Porcentaje": round(cuenta / total * 100, 1) if total > 0 else 0,
                    })
            if freq_rows:
                pd.DataFrame(freq_rows).to_excel(writer, sheet_name="Frecuencias", index=False)

        # Hoja 4: Demograficos
        demo_cols = [c for c in df.columns if c in (
            "nombre_encuestado", "edad", "sexo", "ocupacion", "municipio", "seccion",
            "usuario_id", "documento_id"
        )]
        if demo_cols:
            df[demo_cols].to_excel(writer, sheet_name="Demograficos", index=False)

    output.seek(0)
    safe_name = nombre_cuestionario.replace('"', '').replace("'", "")[:50]
    headers = {
        "Content-Disposition": f'attachment; filename="Reporte_{safe_name}.xlsx"'
    }
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


# ─── PDF ─────────────────────────────────────────────────────────────

class PDFReporte(FPDF):
    def __init__(self, titulo: str = "REPORTE EJECUTIVO SISAT", **kwargs):
        super().__init__(**kwargs)
        self._titulo = titulo

    def header(self) -> None:
        self.set_fill_color(0, 0, 128)
        self.rect(0, 0, self.w, 20, style="F")
        self.set_text_color(255, 215, 0)
        self.set_font("Helvetica", "B", 14)
        self.set_y(6)
        self.cell(0, 8, self._titulo, align="C")
        self.ln(14)
        self.set_text_color(0, 0, 0)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Pagina {self.page_no()}/{{nb}} - Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}", align="C")


@app.get("/reporte/pdf/{cuestionario_id}")
def reporte_pdf(cuestionario_id: str) -> StreamingResponse:
    df = obtener_datos(cuestionario_id)
    if df.empty:
        raise HTTPException(status_code=404, detail="No se encontraron datos para el cuestionario indicado.")

    nombre_cuestionario = obtener_nombre_cuestionario(cuestionario_id)
    preguntas = obtener_preguntas_config(cuestionario_id)
    cols_resp = columnas_respuestas(df)
    mapping = renombrar_columnas_respuestas(cols_resp, preguntas)

    pdf = PDFReporte(titulo="REPORTE EJECUTIVO SISAT", orientation="P", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # --- Info general ---
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, f"Cuestionario: {nombre_cuestionario}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"Total Encuestas: {len(df)}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Total Variables: {len(df.columns)}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # --- Demograficos ---
    if "sexo" in df.columns:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, "Distribucion por Sexo", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        conteo_sexo = df["sexo"].value_counts()
        total_sexo = conteo_sexo.sum()
        with pdf.table(col_widths=(50, 20, 20), text_align="CENTER") as table:
            header = table.row()
            header.cell("Sexo")
            header.cell("Total")
            header.cell("Porcentaje")
            for sexo, n in conteo_sexo.items():
                row = table.row()
                row.cell(str(sexo))
                row.cell(str(int(n)))
                row.cell(f"{n / total_sexo * 100:.1f}%")
        pdf.ln(4)

    if "edad" in df.columns:
        edades = pd.to_numeric(df["edad"], errors="coerce").dropna()
        if not edades.empty:
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(0, 8, "Edades", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(0, 6, f"Promedio: {edades.mean():.1f}  |  Min: {int(edades.min())}  |  Max: {int(edades.max())}", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)

    # --- Frecuencias de todas las preguntas ---
    if cols_resp:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 10, "Resumen de Respuestas por Pregunta", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        for col in cols_resp:
            texto = mapping.get(col, col)
            serie = df[col].astype(str).fillna("N/D")
            frecuencia = serie.value_counts(dropna=False).reset_index()
            frecuencia.columns = ["Respuesta", "Frecuencia"]
            total = int(frecuencia["Frecuencia"].sum())

            # Truncar texto largo de pregunta
            texto_display = texto if len(texto) <= 80 else texto[:77] + "..."

            pdf.set_font("Helvetica", "B", 10)
            pdf.multi_cell(0, 6, texto_display, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9)

            with pdf.table(col_widths=(60, 15, 15), text_align="CENTER") as table:
                header = table.row()
                header.cell("Respuesta")
                header.cell("Freq")
                header.cell("%")
                for _, r in frecuencia.iterrows():
                    data_row = table.row()
                    resp_text = str(r["Respuesta"])
                    if len(resp_text) > 40:
                        resp_text = resp_text[:37] + "..."
                    data_row.cell(resp_text)
                    data_row.cell(str(int(r["Frecuencia"])))
                    pct = r["Frecuencia"] / total * 100 if total > 0 else 0
                    data_row.cell(f"{pct:.1f}%")

            pdf.ln(3)

    pdf_bytes = bytes(pdf.output())
    output = io.BytesIO(pdf_bytes)

    safe_name = nombre_cuestionario.replace('"', '').replace("'", "")[:50]
    headers = {
        "Content-Disposition": f'attachment; filename="Reporte_{safe_name}.pdf"'
    }
    return StreamingResponse(output, media_type="application/pdf", headers=headers)
