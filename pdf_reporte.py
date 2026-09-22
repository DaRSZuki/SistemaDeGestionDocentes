"""PDF del reporte de asistencia y puntualidad por docente y año."""

import os
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image

from db import MESES, DATA_DIR
from logo import logo_optimizado

DORADO = colors.HexColor("#d8a13a")


def _logo_path():
    return logo_optimizado()


def generar_reporte_pdf(docente: dict, anio: int, filas: list, totales: dict) -> str:
    """
    filas: lista de dicts con claves:
        mes, presentes, ausentes, justificados, dias_atraso, minutos_atraso, puntualidad
    totales: dict con las mismas claves agregadas del año.
    """
    ruta = os.path.join(
        DATA_DIR,
        f"reporte_{docente['apellidos']}_{docente['nombres']}_{anio}.pdf".replace(" ", "_"),
    )
    styles = getSampleStyleSheet()
    titulo = ParagraphStyle("t", parent=styles["Title"], fontSize=14)
    sub = ParagraphStyle("s", parent=styles["Normal"], alignment=1, textColor=colors.grey)

    doc = SimpleDocTemplate(ruta, pagesize=landscape(letter),
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm)
    elems = []
    logo = _logo_path()
    if logo:
        try:
            elems.append(Image(logo, width=20 * mm, height=20 * mm))
        except Exception:
            pass
    elems.append(Paragraph("LICEO ITALIANO TRILINGÜE", titulo))
    elems.append(Paragraph("Reporte de Asistencia y Puntualidad", sub))
    elems.append(Spacer(1, 5 * mm))
    elems.append(Paragraph(
        f"<b>Docente:</b> {docente['apellidos']}, {docente['nombres']} &nbsp;&nbsp; "
        f"<b>Usuario:</b> {docente['username']} &nbsp;&nbsp; <b>Año:</b> {anio} &nbsp;&nbsp; "
        f"<b>Generado:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles["Normal"]))
    elems.append(Spacer(1, 5 * mm))

    header = ["Mes", "Días presente", "Ausencias", "Justificadas",
              "Días con atraso", "Minutos de atraso", "% Puntualidad"]
    data = [header]
    for f in filas:
        data.append([
            f["mes"], f["presentes"], f["ausentes"], f["justificados"],
            f["dias_atraso"], f["minutos_atraso"], f"{f['puntualidad']:.0f}%",
        ])
    data.append([
        "TOTAL AÑO", totales["presentes"], totales["ausentes"], totales["justificados"],
        totales["dias_atraso"], totales["minutos_atraso"], f"{totales['puntualidad']:.0f}%",
    ])

    tabla = Table(data, colWidths=[38 * mm, 32 * mm, 28 * mm, 28 * mm, 34 * mm, 36 * mm, 30 * mm])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), DORADO),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f0e6cf")),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#faf6ee")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elems.append(tabla)
    elems.append(Spacer(1, 6 * mm))
    elems.append(Paragraph(
        "<font size=8 color='grey'>% Puntualidad = días que ingresó a la hora programada "
        "(o dentro de la tolerancia) sobre el total de días con asistencia registrada.</font>",
        styles["Normal"]))
    doc.build(elems)
    return ruta
