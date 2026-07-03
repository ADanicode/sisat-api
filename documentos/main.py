import base64
import json
import io
import os
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
    docs = db.collection("registros_campo").where("cuestionario_id", "==", cuestionario_id).stream()

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


@app.get("/reporte/excel/{cuestionario_id}")
def reporte_excel(cuestionario_id: str) -> StreamingResponse:
    df = obtener_datos(cuestionario_id)

    if df.empty:
        raise HTTPException(status_code=404, detail="No se encontraron datos para el cuestionario indicado.")

    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        resumen_df = pd.DataFrame(
            {
                "Metrica": ["Total Encuestas", "Total Variables"],
                "Valor": [len(df), len(df.columns)],
            }
        )
        resumen_df.to_excel(writer, sheet_name="Resumen", index=False)
        df.to_excel(writer, sheet_name="Datos", index=False)

    output.seek(0)

    headers = {
        "Content-Disposition": f'attachment; filename="reporte_{cuestionario_id}.xlsx"'
    }
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


class PDFReporte(FPDF):
    def header(self) -> None:
        self.set_fill_color(0, 0, 128)
        self.rect(0, 0, self.w, 20, style="F")
        self.set_text_color(255, 215, 0)
        self.set_font("Helvetica", "B", 14)
        self.set_y(6)
        self.cell(0, 8, "REPORTE EJECUTIVO SISAT", align="C")
        self.ln(14)
        self.set_text_color(0, 0, 0)


@app.get("/reporte/pdf/{cuestionario_id}")
def reporte_pdf(cuestionario_id: str) -> StreamingResponse:
    df = obtener_datos(cuestionario_id)

    if df.empty:
        raise HTTPException(status_code=404, detail="No se encontraron datos para el cuestionario indicado.")

    columnas = [str(c) for c in df.columns]
    columna_objetivo = next(
        (c for c in columnas if "respuesta" in c.lower() or "q" in c.lower()),
        columnas[0] if columnas else None,
    )

    if not columna_objetivo:
        raise HTTPException(status_code=400, detail="No se encontraron columnas para agrupar en el reporte.")

    serie = df[columna_objetivo].astype(str).fillna("N/D")
    frecuencia = serie.value_counts(dropna=False).reset_index()
    frecuencia.columns = ["Respuesta", "Frecuencia"]

    total = int(frecuencia["Frecuencia"].sum())
    if total > 0:
        frecuencia["Porcentaje"] = (frecuencia["Frecuencia"] / total * 100).round(2)
    else:
        frecuencia["Porcentaje"] = 0.0

    pdf = PDFReporte(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, f"Cuestionario ID: {cuestionario_id}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Total Encuestas: {len(df)}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Total Variables: {len(df.columns)}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(
        0,
        8,
        f"Resumen de respuestas (columna: {columna_objetivo})",
        new_x="LMARGIN",
        new_y="NEXT",
    )

    pdf.set_font("Helvetica", size=10)
    with pdf.table(col_widths=(60, 20, 20), text_align="CENTER") as table:
        header_row = table.row()
        header_row.cell("Respuesta")
        header_row.cell("Frecuencia")
        header_row.cell("Porcentaje")

        for _, row in frecuencia.iterrows():
            data_row = table.row()
            data_row.cell(str(row["Respuesta"]))
            data_row.cell(str(int(row["Frecuencia"])))
            data_row.cell(f"{float(row['Porcentaje']):.2f}%")

    pdf_bytes = bytes(pdf.output())
    output = io.BytesIO(pdf_bytes)

    headers = {
        "Content-Disposition": f'attachment; filename="reporte_{cuestionario_id}.pdf"'
    }
    return StreamingResponse(output, media_type="application/pdf", headers=headers)
