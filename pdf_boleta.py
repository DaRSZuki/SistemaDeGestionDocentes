"""Generación de la boleta de calificaciones en PDF (reportlab)."""

import os
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image,
)

from db import promedio_curso, BOLETAS_DIR
from logo import logo_optimizado

NOTA_MINIMA = 60.0
ROJO = colors.HexColor("#c0261e")
DORADO = colors.HexColor("#d8a13a")


def _logo_path():
    return logo_optimizado()


def _fmt(nota):
    if nota is None:
        return ""
    if float(nota) == int(nota):
        return str(int(nota))
    return f"{float(nota):.2f}"


def generar_boleta_pdf(estudiante: dict, grado: dict, cursos: list, ruta_salida: str = None,
                       promedio_general=None, comportamiento=None, escala=None) -> str:
    """
    estudiante:      {nombres, apellidos, codigo, observaciones_comportamiento}
    grado:           {nombre, anio}
    cursos:          [{nombre, u1, u2, u3, u4}, ...]
    promedio_general: número (promedio de los promedios) o None
    comportamiento:  [{en, it, es, u1, u2, u3, u4}, ...]  (letras A-D)
    escala:          [(letra, rango, en, it, es), ...]
    Devuelve la ruta del PDF generado.
    """
    comportamiento = comportamiento or []
    escala = escala or []
    if ruta_salida is None:
        os.makedirs(BOLETAS_DIR, exist_ok=True)
        base = f"boleta_{estudiante['apellidos']}_{estudiante['nombres']}_{grado['anio']}".replace(" ", "_")
        ruta_salida = os.path.join(BOLETAS_DIR, base + ".pdf")

    styles = getSampleStyleSheet()
    h_titulo = ParagraphStyle("t", parent=styles["Title"], fontSize=15, spaceAfter=2)
    h_sub = ParagraphStyle("s", parent=styles["Normal"], fontSize=10, alignment=1, textColor=colors.grey)
    normal = styles["Normal"]
    label = ParagraphStyle("l", parent=styles["Normal"], fontSize=10, leading=15)

    doc = SimpleDocTemplate(
        ruta_salida, pagesize=letter,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
    )
    elems = []

    logo = _logo_path()
    encabezado_txt = [
        Paragraph("<b>LICEO ITALIANO TRILINGÜE</b>", h_titulo),
        Paragraph("Boleta de Calificaciones", h_sub),
        Paragraph(f"Ciclo escolar {grado['anio']}", h_sub),
    ]
    if logo:
        try:
            img = Image(logo, width=24 * mm, height=24 * mm)
            head = Table([[img, encabezado_txt]], colWidths=[28 * mm, None])
            head.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]))
            elems.append(head)
        except Exception:
            elems.extend(encabezado_txt)
    else:
        elems.extend(encabezado_txt)

    elems.append(Spacer(1, 8 * mm))

    datos = Table(
        [
            [Paragraph("<b>Estudiante:</b>", label),
             Paragraph(f"{estudiante['apellidos']}, {estudiante['nombres']}", label)],
            [Paragraph("<b>Grado:</b>", label), Paragraph(grado["nombre"], label)],
            [Paragraph("<b>Código:</b>", label), Paragraph(estudiante.get("codigo") or "—", label)],
            [Paragraph("<b>Fecha de emisión:</b>", label),
             Paragraph(datetime.now().strftime("%d/%m/%Y"), label)],
        ],
        colWidths=[42 * mm, None],
    )
    datos.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elems.append(datos)
    elems.append(Spacer(1, 6 * mm))

    # Tabla de cursos
    header = ["Curso", "Unidad 1", "Unidad 2", "Unidad 3", "Unidad 4", "Promedio"]
    data = [header]
    estilos_celda = []
    for i, c in enumerate(cursos, start=1):
        prom = promedio_curso(c.get("u1"), c.get("u2"), c.get("u3"), c.get("u4"))
        fila = [
            c["nombre"],
            _fmt(c.get("u1")), _fmt(c.get("u2")),
            _fmt(c.get("u3")), _fmt(c.get("u4")),
            _fmt(prom),
        ]
        data.append(fila)
        # Notas reprobatorias en rojo
        for col, val in ((1, c.get("u1")), (2, c.get("u2")), (3, c.get("u3")),
                         (4, c.get("u4")), (5, prom)):
            if val is not None and float(val) < NOTA_MINIMA:
                estilos_celda.append(("TEXTCOLOR", (col, i), (col, i), ROJO))
                estilos_celda.append(("FONTNAME", (col, i), (col, i), "Helvetica-Bold"))

    tabla = Table(data, colWidths=[52 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 24 * mm])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), DORADO),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#faf6ee")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ] + estilos_celda))
    elems.append(tabla)
    elems.append(Spacer(1, 3 * mm))

    # ---- Promedio general (promedio de los promedios de los cursos) ----
    if promedio_general is not None:
        rojo = float(promedio_general) < NOTA_MINIMA
        pg = Table(
            [["PROMEDIO GENERAL / GENERAL AVERAGE / MEDIA GENERALE", _fmt(promedio_general)]],
            colWidths=[None, 24 * mm],
        )
        pg.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#f0e6cf")),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
            ("ALIGN", (1, 0), (1, 0), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("TEXTCOLOR", (1, 0), (1, 0), ROJO if rojo else colors.black),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        elems.append(pg)
    elems.append(Spacer(1, 3 * mm))
    elems.append(Paragraph(
        f"<font size=8 color='#c0261e'>Nota en rojo = curso reprobado (menos de {int(NOTA_MINIMA)} pts). "
        f"Escala sobre 100 puntos.</font>", normal))
    elems.append(Spacer(1, 7 * mm))

    # ---- Sección fija: Hábitos de trabajo / Work Habits ----
    if comportamiento:
        elems.append(Paragraph(
            "<b>Work Habits / Abitudini lavorative / Hábitos de trabajo</b>", label))
        elems.append(Spacer(1, 2 * mm))
        hdr = ["Work Habits / Abitudini lavorative / Hábitos de trabajo", "I", "II", "III", "IV"]
        wdata = [hdr]
        for it in comportamiento:
            wdata.append([
                f"{it['en']} / {it['it']} / {it['es']}",
                it.get("u1") or "", it.get("u2") or "", it.get("u3") or "", it.get("u4") or "",
            ])
        wtab = Table(wdata, colWidths=[None, 14 * mm, 14 * mm, 14 * mm, 14 * mm])
        wtab.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), DORADO),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#faf6ee")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elems.append(wtab)
        elems.append(Spacer(1, 4 * mm))

    if escala:
        edata = [["Escala de calificación / Grading Scale / Tabella dei voti", ""]]
        for (letra, rango, en, it, es) in escala:
            edata.append([f"{letra}  {rango}", f"{en} / {it} / {es}"])
        etab = Table(edata, colWidths=[34 * mm, None])
        etab.setStyle(TableStyle([
            ("SPAN", (0, 0), (1, 0)),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0e6cf")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elems.append(etab)
        elems.append(Spacer(1, 7 * mm))

    elems.append(Paragraph("<b>Observaciones de comportamiento</b>", label))
    elems.append(Spacer(1, 2 * mm))
    obs = (estudiante.get("observaciones_comportamiento") or "Sin observaciones.").replace("\n", "<br/>")
    caja = Table([[Paragraph(obs, normal)]], colWidths=[None])
    caja.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 18),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    elems.append(caja)
    elems.append(Spacer(1, 18 * mm))

    firmas = Table(
        [["_______________________", "_______________________"],
         ["Docente", "Dirección"]],
        colWidths=[None, None],
    )
    firmas.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTSIZE", (0, 1), (-1, 1), 9),
        ("TEXTCOLOR", (0, 1), (-1, 1), colors.grey),
    ]))
    elems.append(firmas)

    doc.build(elems)
    return ruta_salida
