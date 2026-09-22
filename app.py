"""
Sistema de Gestión y Administración - Liceo Italiano Trilingüe
============================================================
- Control de asistencia del personal docente (marca de entrada / salida con hora del sistema).
- Horarios semanales configurables por día y por docente.
- Interfaz de administrador: usuarios, bajas lógicas, reportes de asistencia y puntualidad.
- Justificación de asistencias/inasistencias con observación (máx. 500 palabras).
- Control de notas: grados -> estudiantes -> cursos -> notas por unidad (4) + promedio.
- Boletas en PDF, notas < 60 en rojo.
- Accesos por grado a otros docentes ('admin' o 'ver').

Ejecución local, base de datos SQLite, empaquetable como .exe con PyInstaller.
"""

import io
import threading
import webbrowser
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash, send_file, abort, Response,
)
from werkzeug.security import generate_password_hash, check_password_hash

import db
from db import (
    get_db, init_db, get_secret_key, resource_path, registrar_auditoria,
    promedio_curso, promedio_general, contar_palabras, DIAS_SEMANA, MESES,
    ITEMS_COMPORTAMIENTO, ESCALA_COMPORTAMIENTO, CLAVES_COMPORTAMIENTO,
    LETRAS_COMPORTAMIENTO, cargar_config, ip_local, CONFIG_PATH,
)
from pdf_boleta import generar_boleta_pdf, NOTA_MINIMA
from pdf_reporte import generar_reporte_pdf
import importador

app = Flask(
    __name__,
    template_folder=resource_path("templates"),
    static_folder=resource_path("static"),
)
app.secret_key = get_secret_key()
app.config.update(
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,      # la cookie no es accesible desde JavaScript
    SESSION_COOKIE_SAMESITE="Lax",     # no se envía en peticiones de otros sitios
    SESSION_PERMANENT=False,           # cookie de sesión: se borra al cerrar el navegador
)

MAX_PALABRAS_OBS = 500
INACTIVIDAD_MAX = 5 * 60               # segundos de inactividad antes de cerrar sesión


# ==========================================================================
# Autenticación / autorización
# ==========================================================================
def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
    finally:
        conn.close()


@app.context_processor
def inject_user():
    u = current_user()
    ver_boletas = False
    if u:
        if u["rol"] == "admin" or u["puede_boletas"]:
            ver_boletas = True
        else:
            conn = get_db()
            try:
                ver_boletas = conn.execute(
                    "SELECT 1 FROM accesos_grado WHERE usuario_id = ? AND activo = 1 LIMIT 1",
                    (u["id"],),
                ).fetchone() is not None
            finally:
                conn.close()
    ahora = datetime.now()
    return {"usuario": u, "ahora": ahora, "ver_boletas": ver_boletas,
            "inactividad_seg": INACTIVIDAD_MAX,
            "dia_semana_es": DIAS_SEMANA[ahora.weekday()].lower()}


# Rutas que no requieren sesión activa ni renuevan el contador de inactividad.
_ENDPOINTS_LIBRES = {"login", "logout", "static", "sesion_ping", "logo_png"}


@app.before_request
def control_inactividad():
    """Cierra la sesión automáticamente tras INACTIVIDAD_MAX segundos sin actividad."""
    if request.endpoint in _ENDPOINTS_LIBRES:
        return
    if not session.get("uid"):
        return
    ahora = datetime.now().timestamp()
    ultima = session.get("ultima_actividad")
    if ultima is not None and (ahora - ultima) > INACTIVIDAD_MAX:
        session.clear()
        flash("Su sesión se cerró automáticamente por inactividad.", "warn")
        return redirect(url_for("login", motivo="inactividad"))
    session["ultima_actividad"] = ahora


@app.route("/sesion/ping", methods=["POST"])
def sesion_ping():
    """El navegador llama a esto cuando hay actividad del usuario, para no cerrar la sesión."""
    if not session.get("uid"):
        return ("", 401)
    ahora = datetime.now().timestamp()
    ultima = session.get("ultima_actividad")
    if ultima is not None and (ahora - ultima) > INACTIVIDAD_MAX:
        session.clear()
        return ("", 401)
    session["ultima_actividad"] = ahora
    return ("", 204)


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u or not u["activo"]:
            session.clear()
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u or u["rol"] != "admin" or not u["activo"]:
            abort(403)
        return f(*a, **kw)
    return wrapper


def boletas_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u or not u["activo"]:
            return redirect(url_for("login"))
        if u["rol"] != "admin" and not u["puede_boletas"]:
            # También se permite el acceso si tiene acceso asignado a algún grado
            conn = get_db()
            try:
                tiene = conn.execute(
                    "SELECT 1 FROM accesos_grado WHERE usuario_id = ? AND activo = 1 LIMIT 1",
                    (u["id"],),
                ).fetchone()
            finally:
                conn.close()
            if not tiene:
                abort(403)
        return f(*a, **kw)
    return wrapper


# ==========================================================================
# Rutas de sesión
# ==========================================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = get_db()
        try:
            u = conn.execute(
                "SELECT * FROM usuarios WHERE username = ?", (username,)
            ).fetchone()
            if u and u["activo"] and check_password_hash(u["password_hash"], password):
                session.clear()
                session["uid"] = u["id"]
                session["ultima_actividad"] = datetime.now().timestamp()
                session.permanent = False
                registrar_auditoria(conn, u["id"], "login", "Inicio de sesión")
                conn.commit()
                return redirect(url_for("index"))
            flash("Usuario o contraseña incorrectos, o cuenta dada de baja.", "error")
        finally:
            conn.close()
    else:
        motivo = request.args.get("motivo")
        if motivo == "inactividad":
            flash("Su sesión se cerró automáticamente por inactividad.", "warn")
        elif motivo == "pestana":
            flash("Su sesión se cerró al cerrar la pestaña del navegador.", "warn")
    return render_template("login.html")


@app.route("/logo.png")
def logo_png():
    from logo import logo_optimizado
    p = logo_optimizado()
    if not p:
        abort(404)
    return send_file(p, mimetype="image/png")


@app.route("/logout")
def logout():
    uid = session.get("uid")
    if uid:
        conn = get_db()
        try:
            registrar_auditoria(conn, uid, "logout",
                                f"Cierre de sesión ({request.args.get('motivo', 'manual')})")
            conn.commit()
        finally:
            conn.close()
    session.clear()
    motivo = request.args.get("motivo")
    if motivo in ("inactividad", "pestana"):
        return redirect(url_for("login", motivo=motivo))
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    u = current_user()
    if u["rol"] == "admin":
        return redirect(url_for("admin_home"))
    return redirect(url_for("panel"))


@app.route("/cambiar-clave", methods=["GET", "POST"])
@login_required
def cambiar_clave():
    if request.method == "POST":
        u = current_user()
        actual = request.form.get("actual", "")
        nueva = request.form.get("nueva", "")
        if not check_password_hash(u["password_hash"], actual):
            flash("La contraseña actual no es correcta.", "error")
        elif len(nueva) < 4:
            flash("La nueva contraseña debe tener al menos 4 caracteres.", "error")
        else:
            conn = get_db()
            try:
                conn.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?",
                             (generate_password_hash(nueva), u["id"]))
                registrar_auditoria(conn, u["id"], "cambio_clave", "El usuario cambió su contraseña")
                conn.commit()
            finally:
                conn.close()
            flash("Contraseña actualizada.", "ok")
            return redirect(url_for("index"))
    return render_template("cambiar_clave.html")


# ==========================================================================
# Panel del docente - Asistencia
# ==========================================================================
def _horario_del_dia(conn, usuario_id, dia_semana):
    return conn.execute(
        "SELECT * FROM horarios WHERE usuario_id = ? AND dia_semana = ?",
        (usuario_id, dia_semana),
    ).fetchone()


def _calc_atraso(now, horario):
    if not horario or not horario["laborable"] or not horario["hora_entrada"]:
        return 0
    prog = datetime.strptime(horario["hora_entrada"], "%H:%M").replace(
        year=now.year, month=now.month, day=now.day
    )
    tol = horario["tolerancia_min"] or 0
    diff = (now - prog).total_seconds() / 60.0
    return int(round(diff)) if diff > tol else 0


@app.route("/panel")
@login_required
def panel():
    u = current_user()
    conn = get_db()
    try:
        hoy = date.today().isoformat()
        asis = conn.execute(
            "SELECT * FROM asistencias WHERE usuario_id = ? AND fecha = ?", (u["id"], hoy)
        ).fetchone()
        horario_hoy = _horario_del_dia(conn, u["id"], date.today().weekday())
        horarios = conn.execute(
            "SELECT * FROM horarios WHERE usuario_id = ? ORDER BY dia_semana", (u["id"],)
        ).fetchall()
        # Resumen de atrasos del mes en curso
        ym = date.today().strftime("%Y-%m")
        mes = conn.execute(
            """SELECT COALESCE(SUM(minutos_atraso),0) AS min,
                      SUM(CASE WHEN minutos_atraso > 0 THEN 1 ELSE 0 END) AS dias
               FROM asistencias WHERE usuario_id = ? AND substr(fecha,1,7) = ?""",
            (u["id"], ym),
        ).fetchone()
    finally:
        conn.close()
    return render_template(
        "panel.html", asis=asis, horario_hoy=horario_hoy, horarios=horarios,
        dias=DIAS_SEMANA, hoy_idx=date.today().weekday(), mes_resumen=mes,
    )


@app.route("/asistencia/entrada", methods=["POST"])
@login_required
def marcar_entrada():
    u = current_user()
    now = datetime.now()
    hoy = now.date().isoformat()
    conn = get_db()
    try:
        asis = conn.execute(
            "SELECT * FROM asistencias WHERE usuario_id = ? AND fecha = ?", (u["id"], hoy)
        ).fetchone()
        if asis and asis["hora_entrada"]:
            flash("Ya registró su hora de entrada hoy.", "error")
            return redirect(url_for("panel"))
        horario = _horario_del_dia(conn, u["id"], now.weekday())
        atraso = _calc_atraso(now, horario)
        hora = now.strftime("%H:%M:%S")
        if asis:
            conn.execute(
                "UPDATE asistencias SET hora_entrada = ?, minutos_atraso = ?, estado = 'presente' WHERE id = ?",
                (hora, atraso, asis["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO asistencias (usuario_id, fecha, hora_entrada, minutos_atraso, estado)
                   VALUES (?, ?, ?, ?, 'presente')""",
                (u["id"], hoy, hora, atraso),
            )
        registrar_auditoria(conn, u["id"], "marca_entrada", f"{hoy} {hora} atraso={atraso}min")
        conn.commit()
    finally:
        conn.close()
    if atraso > 0:
        flash(f"Entrada registrada a las {hora}. Atraso: {atraso} minuto(s).", "warn")
    else:
        flash(f"Entrada registrada a las {hora}. ¡Puntual!", "ok")
    return redirect(url_for("panel"))


@app.route("/asistencia/salida", methods=["POST"])
@login_required
def marcar_salida():
    u = current_user()
    now = datetime.now()
    hoy = now.date().isoformat()
    conn = get_db()
    try:
        asis = conn.execute(
            "SELECT * FROM asistencias WHERE usuario_id = ? AND fecha = ?", (u["id"], hoy)
        ).fetchone()
        if not asis or not asis["hora_entrada"]:
            flash("Primero debe registrar su hora de entrada.", "error")
            return redirect(url_for("panel"))
        if asis["hora_salida"]:
            flash("Ya registró su hora de salida hoy.", "error")
            return redirect(url_for("panel"))
        hora = now.strftime("%H:%M:%S")
        conn.execute("UPDATE asistencias SET hora_salida = ? WHERE id = ?", (hora, asis["id"]))
        registrar_auditoria(conn, u["id"], "marca_salida", f"{hoy} {hora}")
        conn.commit()
    finally:
        conn.close()
    flash(f"Salida registrada a las {hora}.", "ok")
    return redirect(url_for("panel"))


@app.route("/mis-asistencias")
@login_required
def mis_asistencias():
    u = current_user()
    anio = int(request.args.get("anio", date.today().year))
    conn = get_db()
    try:
        registros = conn.execute(
            """SELECT * FROM asistencias
               WHERE usuario_id = ? AND substr(fecha,1,4) = ?
               ORDER BY fecha DESC""",
            (u["id"], str(anio)),
        ).fetchall()
        anios = [r["a"] for r in conn.execute(
            "SELECT DISTINCT substr(fecha,1,4) AS a FROM asistencias WHERE usuario_id = ? ORDER BY a DESC",
            (u["id"],),
        ).fetchall()]
    finally:
        conn.close()
    filas, totales = _reporte_mensual(registros)
    if str(anio) not in anios:
        anios.append(str(anio))
    return render_template("mis_asistencias.html", registros=registros, filas=filas,
                           totales=totales, anio=anio, anios=sorted(set(anios), reverse=True))


# ==========================================================================
# Reporte mensual (reutilizado por docente y admin)
# ==========================================================================
def _reporte_mensual(registros):
    base = {m: dict(mes=MESES[m - 1], presentes=0, ausentes=0, justificados=0,
                    dias_atraso=0, minutos_atraso=0, a_tiempo=0) for m in range(1, 13)}
    for r in registros:
        m = int(r["fecha"][5:7])
        b = base[m]
        estado = r["estado"]
        if estado == "presente":
            b["presentes"] += 1
            if r["minutos_atraso"] and r["minutos_atraso"] > 0:
                b["dias_atraso"] += 1
                b["minutos_atraso"] += r["minutos_atraso"]
            else:
                b["a_tiempo"] += 1
        elif estado == "ausente":
            b["ausentes"] += 1
        elif estado in ("justificado", "permiso"):
            b["justificados"] += 1
            if r["minutos_atraso"] and r["minutos_atraso"] > 0:
                b["dias_atraso"] += 1
                b["minutos_atraso"] += r["minutos_atraso"]
    filas = []
    tot = dict(presentes=0, ausentes=0, justificados=0, dias_atraso=0,
               minutos_atraso=0, a_tiempo=0)
    for m in range(1, 13):
        b = base[m]
        con_registro = b["presentes"] + b["justificados"]
        b["puntualidad"] = (100.0 * b["a_tiempo"] / con_registro) if con_registro else 100.0
        filas.append(b)
        for k in tot:
            tot[k] += b[k]
    tot_con = tot["presentes"] + tot["justificados"]
    tot["puntualidad"] = (100.0 * tot["a_tiempo"] / tot_con) if tot_con else 100.0
    tot["mes"] = "TOTAL AÑO"
    return filas, tot


# ==========================================================================
# Interfaz de administrador - Usuarios
# ==========================================================================
@app.route("/admin")
@admin_required
def admin_home():
    conn = get_db()
    try:
        u_act = conn.execute("SELECT COUNT(*) n FROM usuarios WHERE activo = 1 AND rol='docente'").fetchone()["n"]
        u_baja = conn.execute("SELECT COUNT(*) n FROM usuarios WHERE activo = 0").fetchone()["n"]
        grd = conn.execute("SELECT COUNT(*) n FROM grados WHERE activo = 1").fetchone()["n"]
        est = conn.execute("SELECT COUNT(*) n FROM estudiantes WHERE activo = 1").fetchone()["n"]
        hoy = date.today().isoformat()
        marcas = conn.execute(
            """SELECT a.*, us.nombres, us.apellidos FROM asistencias a
               JOIN usuarios us ON us.id = a.usuario_id
               WHERE a.fecha = ? ORDER BY a.hora_entrada""", (hoy,)
        ).fetchall()
    finally:
        conn.close()
    return render_template("admin_home.html", u_act=u_act, u_baja=u_baja,
                           grd=grd, est=est, marcas=marcas, hoy=hoy)


@app.route("/admin/usuarios")
@admin_required
def admin_usuarios():
    incluir_baja = request.args.get("bajas") == "1"
    conn = get_db()
    try:
        q = "SELECT * FROM usuarios"
        if not incluir_baja:
            q += " WHERE activo = 1"
        q += " ORDER BY activo DESC, rol, apellidos"
        usuarios = conn.execute(q).fetchall()
    finally:
        conn.close()
    return render_template("admin_usuarios.html", usuarios=usuarios, incluir_baja=incluir_baja)


@app.route("/admin/usuarios/crear", methods=["POST"])
@admin_required
def admin_crear_usuario():
    f = request.form
    username = f.get("username", "").strip().lower()
    password = f.get("password", "")
    nombres = f.get("nombres", "").strip()
    apellidos = f.get("apellidos", "").strip()
    rol = "admin" if f.get("rol") == "admin" else "docente"
    puede_boletas = 1 if f.get("puede_boletas") else 0
    if not (username and password and nombres and apellidos):
        flash("Todos los campos son obligatorios.", "error")
        return redirect(url_for("admin_usuarios"))
    conn = get_db()
    try:
        if conn.execute("SELECT 1 FROM usuarios WHERE username = ?", (username,)).fetchone():
            flash("Ese nombre de usuario ya existe.", "error")
            return redirect(url_for("admin_usuarios"))
        cur = conn.execute(
            """INSERT INTO usuarios (username, password_hash, nombres, apellidos, rol,
                                     puede_boletas, activo, creado_en)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (username, generate_password_hash(password), nombres, apellidos, rol,
             puede_boletas, datetime.now().isoformat(timespec="seconds")),
        )
        nuevo_id = cur.lastrowid
        # Horario base Lunes-Viernes 07:30 - 14:30 (editable luego)
        for d in range(7):
            laborable = 1 if d < 5 else 0
            conn.execute(
                """INSERT INTO horarios (usuario_id, dia_semana, laborable, hora_entrada,
                                         hora_salida, tolerancia_min)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (nuevo_id, d, laborable, "07:30" if laborable else None,
                 "14:30" if laborable else None, 5),
            )
        registrar_auditoria(conn, session["uid"], "crear_usuario", f"{username} ({rol})")
        conn.commit()
    finally:
        conn.close()
    flash(f"Usuario «{username}» creado.", "ok")
    return redirect(url_for("admin_usuarios"))


@app.route("/admin/usuarios/<int:uid>/editar", methods=["POST"])
@admin_required
def admin_editar_usuario(uid):
    f = request.form
    conn = get_db()
    try:
        u = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
        if not u:
            abort(404)
        nombres = f.get("nombres", u["nombres"]).strip()
        apellidos = f.get("apellidos", u["apellidos"]).strip()
        puede_boletas = 1 if f.get("puede_boletas") else 0
        rol = "admin" if f.get("rol") == "admin" else "docente"
        conn.execute(
            "UPDATE usuarios SET nombres=?, apellidos=?, puede_boletas=?, rol=? WHERE id=?",
            (nombres, apellidos, puede_boletas, rol, uid),
        )
        nueva = f.get("password", "").strip()
        if nueva:
            conn.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?",
                         (generate_password_hash(nueva), uid))
        registrar_auditoria(conn, session["uid"], "editar_usuario",
                            f"{u['username']} boletas={puede_boletas} reset_pwd={bool(nueva)}")
        conn.commit()
    finally:
        conn.close()
    flash("Usuario actualizado.", "ok")
    return redirect(url_for("admin_usuarios", bajas=request.args.get("bajas")))


@app.route("/admin/usuarios/<int:uid>/baja", methods=["POST"])
@admin_required
def admin_baja_usuario(uid):
    if uid == session["uid"]:
        flash("No puede darse de baja a sí mismo.", "error")
        return redirect(url_for("admin_usuarios"))
    conn = get_db()
    try:
        u = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
        if not u:
            abort(404)
        conn.execute(
            "UPDATE usuarios SET activo = 0, dado_baja_en = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), uid),
        )
        conn.execute("UPDATE accesos_grado SET activo = 0 WHERE usuario_id = ?", (uid,))
        registrar_auditoria(conn, session["uid"], "baja_usuario",
                            f"Baja lógica de {u['username']} (datos conservados)")
        conn.commit()
    finally:
        conn.close()
    flash("Usuario dado de baja (borrado lógico). Sus datos se conservan.", "ok")
    return redirect(url_for("admin_usuarios", bajas="1"))


@app.route("/admin/usuarios/<int:uid>/reactivar", methods=["POST"])
@admin_required
def admin_reactivar_usuario(uid):
    conn = get_db()
    try:
        conn.execute("UPDATE usuarios SET activo = 1, dado_baja_en = NULL WHERE id = ?", (uid,))
        registrar_auditoria(conn, session["uid"], "reactivar_usuario", str(uid))
        conn.commit()
    finally:
        conn.close()
    flash("Usuario reactivado.", "ok")
    return redirect(url_for("admin_usuarios", bajas="1"))


# ==========================================================================
# Horarios semanales por docente
# ==========================================================================
@app.route("/admin/usuarios/<int:uid>/horarios", methods=["GET", "POST"])
@admin_required
def admin_horarios(uid):
    conn = get_db()
    try:
        u = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
        if not u:
            abort(404)
        if request.method == "POST":
            for d in range(7):
                laborable = 1 if request.form.get(f"lab_{d}") else 0
                he = request.form.get(f"ent_{d}", "").strip() or None
                hs = request.form.get(f"sal_{d}", "").strip() or None
                try:
                    tol = int(request.form.get(f"tol_{d}", "0") or 0)
                except ValueError:
                    tol = 0
                if not laborable:
                    he = hs = None
                existe = conn.execute(
                    "SELECT id FROM horarios WHERE usuario_id = ? AND dia_semana = ?", (uid, d)
                ).fetchone()
                if existe:
                    conn.execute(
                        """UPDATE horarios SET laborable=?, hora_entrada=?, hora_salida=?,
                                               tolerancia_min=? WHERE id=?""",
                        (laborable, he, hs, tol, existe["id"]),
                    )
                else:
                    conn.execute(
                        """INSERT INTO horarios (usuario_id, dia_semana, laborable, hora_entrada,
                                                 hora_salida, tolerancia_min) VALUES (?,?,?,?,?,?)""",
                        (uid, d, laborable, he, hs, tol),
                    )
            registrar_auditoria(conn, session["uid"], "editar_horarios", u["username"])
            conn.commit()
            flash("Horario semanal actualizado.", "ok")
            return redirect(url_for("admin_horarios", uid=uid))
        horarios = {h["dia_semana"]: h for h in conn.execute(
            "SELECT * FROM horarios WHERE usuario_id = ? ORDER BY dia_semana", (uid,)
        ).fetchall()}
    finally:
        conn.close()
    return render_template("admin_horarios.html", u=u, horarios=horarios, dias=DIAS_SEMANA)


# ==========================================================================
# Asistencia - visualización y justificación (admin)
# ==========================================================================
@app.route("/admin/asistencia", methods=["GET"])
@admin_required
def admin_asistencia():
    conn = get_db()
    try:
        docentes = conn.execute(
            "SELECT id, nombres, apellidos, username FROM usuarios ORDER BY activo DESC, apellidos"
        ).fetchall()
        uid = request.args.get("uid", type=int)
        desde = request.args.get("desde") or date.today().replace(day=1).isoformat()
        hasta = request.args.get("hasta") or date.today().isoformat()
        registros = []
        if uid:
            registros = conn.execute(
                """SELECT a.*, m.nombres AS mod_nombres, m.apellidos AS mod_apellidos
                   FROM asistencias a
                   LEFT JOIN usuarios m ON m.id = a.modificado_por
                   WHERE a.usuario_id = ? AND a.fecha BETWEEN ? AND ?
                   ORDER BY a.fecha DESC""",
                (uid, desde, hasta),
            ).fetchall()
    finally:
        conn.close()
    return render_template("admin_asistencia.html", docentes=docentes, registros=registros,
                           uid=uid, desde=desde, hasta=hasta,
                           max_palabras=MAX_PALABRAS_OBS)


@app.route("/admin/asistencia/guardar", methods=["POST"])
@admin_required
def admin_asistencia_guardar():
    f = request.form
    uid = f.get("usuario_id", type=int)
    fecha = f.get("fecha", "").strip()
    estado = f.get("estado", "presente")
    he = f.get("hora_entrada", "").strip() or None
    hs = f.get("hora_salida", "").strip() or None
    observacion = f.get("observacion", "").strip()
    try:
        minutos = int(f.get("minutos_atraso", "0") or 0)
    except ValueError:
        minutos = 0

    if estado not in ("presente", "ausente", "justificado", "permiso"):
        estado = "presente"
    if contar_palabras(observacion) > MAX_PALABRAS_OBS:
        flash(f"La observación supera las {MAX_PALABRAS_OBS} palabras.", "error")
        return redirect(url_for("admin_asistencia", uid=uid))
    if not (uid and fecha):
        flash("Falta el docente o la fecha.", "error")
        return redirect(url_for("admin_asistencia", uid=uid))
    if estado in ("ausente", "justificado"):
        he = hs = None
        if estado == "ausente":
            minutos = 0

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM asistencias WHERE usuario_id = ? AND fecha = ?", (uid, fecha)
        ).fetchone()
        ahora = datetime.now().isoformat(timespec="seconds")
        if row:
            conn.execute(
                """UPDATE asistencias SET hora_entrada=?, hora_salida=?, minutos_atraso=?,
                       estado=?, observacion=?, modificado_admin=1, modificado_por=?, modificado_en=?
                   WHERE id=?""",
                (he, hs, minutos, estado, observacion, session["uid"], ahora, row["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO asistencias (usuario_id, fecha, hora_entrada, hora_salida,
                       minutos_atraso, estado, observacion, modificado_admin, modificado_por, modificado_en)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (uid, fecha, he, hs, minutos, estado, observacion, session["uid"], ahora),
            )
        registrar_auditoria(conn, session["uid"], "modificar_asistencia",
                            f"docente={uid} fecha={fecha} estado={estado} obs={contar_palabras(observacion)}pal")
        conn.commit()
    finally:
        conn.close()
    flash("Registro de asistencia guardado.", "ok")
    return redirect(url_for("admin_asistencia", uid=uid,
                            desde=f.get("desde"), hasta=f.get("hasta")))


# ==========================================================================
# Reportes de asistencia y puntualidad
# ==========================================================================
def _datos_reporte(conn, uid, anio):
    docente = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
    registros = conn.execute(
        "SELECT * FROM asistencias WHERE usuario_id = ? AND substr(fecha,1,4) = ? ORDER BY fecha",
        (uid, str(anio)),
    ).fetchall()
    filas, totales = _reporte_mensual(registros)
    return docente, filas, totales


@app.route("/admin/reportes")
@admin_required
def admin_reportes():
    conn = get_db()
    try:
        docentes = conn.execute(
            "SELECT id, nombres, apellidos, username FROM usuarios WHERE rol='docente' ORDER BY apellidos"
        ).fetchall()
        uid = request.args.get("uid", type=int)
        anio = request.args.get("anio", default=date.today().year, type=int)
        docente = filas = totales = None
        if uid:
            docente, filas, totales = _datos_reporte(conn, uid, anio)
    finally:
        conn.close()
    return render_template("admin_reportes.html", docentes=docentes, uid=uid, anio=anio,
                           docente=docente, filas=filas, totales=totales, meses=MESES)


@app.route("/admin/reportes/pdf")
@admin_required
def admin_reportes_pdf():
    uid = request.args.get("uid", type=int)
    anio = request.args.get("anio", default=date.today().year, type=int)
    if not uid:
        abort(400)
    conn = get_db()
    try:
        docente, filas, totales = _datos_reporte(conn, uid, anio)
        if not docente:
            abort(404)
        ruta = generar_reporte_pdf(dict(docente), anio, filas, totales)
    finally:
        conn.close()
    return send_file(ruta, as_attachment=True)


@app.route("/admin/reportes/csv")
@admin_required
def admin_reportes_csv():
    uid = request.args.get("uid", type=int)
    anio = request.args.get("anio", default=date.today().year, type=int)
    if not uid:
        abort(400)
    conn = get_db()
    try:
        docente, filas, totales = _datos_reporte(conn, uid, anio)
    finally:
        conn.close()
    if not docente:
        abort(404)
    out = io.StringIO()
    out.write("Mes;Dias presente;Ausencias;Justificadas;Dias con atraso;Minutos de atraso;% Puntualidad\n")
    for f in filas:
        out.write(f"{f['mes']};{f['presentes']};{f['ausentes']};{f['justificados']};"
                  f"{f['dias_atraso']};{f['minutos_atraso']};{f['puntualidad']:.0f}\n")
    out.write(f"TOTAL AÑO;{totales['presentes']};{totales['ausentes']};{totales['justificados']};"
              f"{totales['dias_atraso']};{totales['minutos_atraso']};{totales['puntualidad']:.0f}\n")
    return Response(
        out.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=reporte_{docente['username']}_{anio}.csv"},
    )


# ==========================================================================
# Control de notas / boletas
# ==========================================================================
def _acceso_grado(conn, usuario, grado_id):
    """Devuelve 'admin', 'ver' o None según el acceso del usuario al grado."""
    if usuario["rol"] == "admin":
        return "admin"
    row = conn.execute(
        "SELECT nivel FROM accesos_grado WHERE usuario_id = ? AND grado_id = ? AND activo = 1",
        (usuario["id"], grado_id),
    ).fetchone()
    return row["nivel"] if row else None


@app.route("/boletas")
@boletas_required
def boletas_grados():
    u = current_user()
    conn = get_db()
    try:
        if u["rol"] == "admin":
            grados = conn.execute(
                "SELECT * FROM grados WHERE activo = 1 ORDER BY anio DESC, nombre"
            ).fetchall()
        else:
            grados = conn.execute(
                """SELECT g.*, ag.nivel FROM grados g
                   JOIN accesos_grado ag ON ag.grado_id = g.id
                   WHERE ag.usuario_id = ? AND ag.activo = 1 AND g.activo = 1
                   ORDER BY g.anio DESC, g.nombre""",
                (u["id"],),
            ).fetchall()
        conteos = {r["grado_id"]: r["n"] for r in conn.execute(
            "SELECT grado_id, COUNT(*) n FROM estudiantes WHERE activo = 1 GROUP BY grado_id"
        ).fetchall()}
    finally:
        conn.close()
    return render_template("boletas_grados.html", grados=grados, conteos=conteos,
                           es_admin=(u["rol"] == "admin"), anio_actual=date.today().year)


@app.route("/boletas/grado/crear", methods=["POST"])
@boletas_required
def boletas_crear_grado():
    u = current_user()
    nombre = request.form.get("nombre", "").strip()
    try:
        anio = int(request.form.get("anio") or date.today().year)
    except ValueError:
        anio = date.today().year
    if not nombre:
        flash("Indique el nombre del grado.", "error")
        return redirect(url_for("boletas_grados"))
    conn = get_db()
    try:
        if conn.execute("SELECT 1 FROM grados WHERE nombre = ? AND anio = ?", (nombre, anio)).fetchone():
            flash("Ya existe ese grado para ese año.", "error")
            return redirect(url_for("boletas_grados"))
        cur = conn.execute(
            "INSERT INTO grados (nombre, anio, activo, creado_por, creado_en) VALUES (?, ?, 1, ?, ?)",
            (nombre, anio, u["id"], datetime.now().isoformat(timespec="seconds")),
        )
        gid = cur.lastrowid
        if u["rol"] != "admin":
            conn.execute(
                "INSERT INTO accesos_grado (usuario_id, grado_id, nivel, activo, creado_en) VALUES (?, ?, 'admin', 1, ?)",
                (u["id"], gid, datetime.now().isoformat(timespec="seconds")),
            )
        registrar_auditoria(conn, u["id"], "crear_grado", f"{nombre} {anio}")
        conn.commit()
    finally:
        conn.close()
    flash("Grado creado.", "ok")
    return redirect(url_for("boletas_grado", gid=gid))


# --------------------------------------------------------------------------
# Importación masiva de estudiantes y notas (Excel / CSV)
# --------------------------------------------------------------------------
@app.route("/boletas/plantilla.csv")
@boletas_required
def boletas_plantilla_csv():
    return Response(
        importador.plantilla_csv(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=plantilla_notas.csv"},
    )


@app.route("/boletas/plantilla.xlsx")
@boletas_required
def boletas_plantilla_xlsx():
    try:
        data = importador.plantilla_xlsx()
    except ImportError:
        flash("El formato Excel no está disponible (falta 'openpyxl'). Use la plantilla CSV.", "error")
        return redirect(url_for("boletas_importar"))
    return Response(
        data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=plantilla_notas.xlsx"},
    )


@app.route("/boletas/importar", methods=["GET", "POST"])
@boletas_required
def boletas_importar():
    u = current_user()
    gid = request.args.get("gid", type=int)
    grado = None
    conn = get_db()
    try:
        if gid:
            grado = conn.execute("SELECT * FROM grados WHERE id = ?", (gid,)).fetchone()
            if not grado:
                abort(404)
            if _acceso_grado(conn, u, gid) != "admin":
                abort(403)

        resumen = None
        errores_formato = None
        if request.method == "POST":
            archivo = request.files.get("archivo")
            dry_run = bool(request.form.get("solo_validar"))
            if not archivo or not archivo.filename:
                flash("Seleccione un archivo .xlsx o .csv.", "error")
                return redirect(request.url)
            contenido = archivo.read()
            if len(contenido) > 6 * 1024 * 1024:
                flash("El archivo es demasiado grande (máx. 6 MB).", "error")
                return redirect(request.url)
            try:
                filas, errores_formato = importador.parse_archivo(
                    archivo.filename, contenido,
                    forzar_grado=(grado["nombre"] if grado else None),
                    forzar_anio=(grado["anio"] if grado else None),
                )
            except ValueError as e:
                flash(str(e), "error")
                return redirect(request.url)

            resumen = importador.aplicar_import(conn, u, filas, dry_run=dry_run)
            resumen["errores"] = errores_formato + resumen.get("errores", [])
            if not dry_run:
                registrar_auditoria(
                    conn, u["id"], "importar_notas",
                    f"grado={gid or 'varios'} filas={len(filas)} "
                    f"est_nuevos={resumen['estudiantes_creados']} cursos_nuevos={resumen['cursos_creados']}",
                )
                conn.commit()
                flash("Importación aplicada correctamente.", "ok")
            else:
                flash("Validación completada. Nada se ha guardado todavía.", "warn")
    finally:
        conn.close()
    return render_template("boletas_importar.html", grado=grado, resumen=resumen)


@app.route("/boletas/grado/<int:gid>")
@boletas_required
def boletas_grado(gid):
    u = current_user()
    conn = get_db()
    try:
        grado = conn.execute("SELECT * FROM grados WHERE id = ?", (gid,)).fetchone()
        if not grado:
            abort(404)
        nivel = _acceso_grado(conn, u, gid)
        if not nivel:
            abort(403)
        db.backfill_notas(conn, gid)
        conn.commit()
        cursos_grado = conn.execute(
            "SELECT * FROM cursos_grado WHERE grado_id = ? AND activo = 1 ORDER BY orden, nombre",
            (gid,),
        ).fetchall()
        estudiantes = conn.execute(
            "SELECT * FROM estudiantes WHERE grado_id = ? AND activo = 1 ORDER BY apellidos, nombres",
            (gid,),
        ).fetchall()
        # promedios generales por estudiante (solo cursos activos del grado)
        proms = {e["id"]: promedio_general(conn, e["id"]) for e in estudiantes}
        con_nota = [v for v in proms.values() if v is not None]
        promedio_grado = round(sum(con_nota) / len(con_nota), 2) if con_nota else None

        orden = request.args.get("orden", "nombre")
        if orden == "promedio":
            estudiantes = sorted(
                estudiantes,
                key=lambda e: (proms[e["id"]] is None, -(proms[e["id"]] or 0)),
            )
        accesos = conn.execute(
            """SELECT ag.*, us.nombres, us.apellidos, us.username FROM accesos_grado ag
               JOIN usuarios us ON us.id = ag.usuario_id
               WHERE ag.grado_id = ? AND ag.activo = 1""", (gid,)
        ).fetchall()
        docentes = conn.execute(
            "SELECT id, nombres, apellidos, username FROM usuarios WHERE activo=1 AND rol='docente' ORDER BY apellidos"
        ).fetchall() if u["rol"] == "admin" else []
    finally:
        conn.close()
    return render_template("boletas_grado.html", grado=grado, estudiantes=estudiantes,
                           cursos_grado=cursos_grado, proms=proms, nivel=nivel,
                           promedio_grado=promedio_grado, orden=orden,
                           es_admin=(u["rol"] == "admin"), accesos=accesos,
                           docentes=docentes, nota_min=NOTA_MINIMA)


# --------------------------------------------------------------------------
# Cursos del grado (catálogo compartido por todos los estudiantes del grado)
# --------------------------------------------------------------------------
@app.route("/boletas/grado/<int:gid>/curso/crear", methods=["POST"])
@boletas_required
def boletas_grado_curso_crear(gid):
    u = current_user()
    conn = get_db()
    try:
        if _acceso_grado(conn, u, gid) != "admin":
            abort(403)
        nombres = [n.strip() for n in request.form.get("nombre", "").replace(";", "\n").splitlines()]
        nombres = [n for n in nombres if n]
        if not nombres:
            flash("Indique el nombre del curso.", "error")
            return redirect(url_for("boletas_grado", gid=gid))
        maxorden = conn.execute(
            "SELECT COALESCE(MAX(orden), 0) m FROM cursos_grado WHERE grado_id = ?", (gid,)
        ).fetchone()["m"]
        creados = 0
        for nombre in nombres:
            existe = conn.execute(
                "SELECT id, activo FROM cursos_grado WHERE grado_id = ? AND lower(nombre) = lower(?)",
                (gid, nombre),
            ).fetchone()
            if existe:
                if not existe["activo"]:
                    conn.execute("UPDATE cursos_grado SET activo = 1 WHERE id = ?", (existe["id"],))
                    creados += 1
                continue
            maxorden += 1
            conn.execute(
                "INSERT INTO cursos_grado (grado_id, nombre, orden, activo) VALUES (?, ?, ?, 1)",
                (gid, nombre, maxorden),
            )
            creados += 1
        # Asignar los cursos nuevos a todos los estudiantes del grado
        db.backfill_notas(conn, gid)
        registrar_auditoria(conn, u["id"], "crear_curso_grado", f"grado={gid} +{creados}: {', '.join(nombres)}")
        conn.commit()
    finally:
        conn.close()
    flash(f"{creados} curso(s) agregado(s) al grado y asignado(s) a los estudiantes.", "ok")
    return redirect(url_for("boletas_grado", gid=gid))


@app.route("/boletas/grado/curso/<int:cgid>/editar", methods=["POST"])
@boletas_required
def boletas_grado_curso_editar(cgid):
    u = current_user()
    accion = request.form.get("accion", "renombrar")
    nuevo = request.form.get("nombre", "").strip()
    conn = get_db()
    try:
        cg = conn.execute("SELECT * FROM cursos_grado WHERE id = ?", (cgid,)).fetchone()
        if not cg:
            abort(404)
        gid = cg["grado_id"]
        if _acceso_grado(conn, u, gid) != "admin":
            abort(403)
        if accion == "eliminar":
            conn.execute("UPDATE cursos_grado SET activo = 0 WHERE id = ?", (cgid,))
            registrar_auditoria(conn, u["id"], "eliminar_curso_grado", f"grado={gid} curso={cg['nombre']}")
            flash("Curso retirado del grado. Las notas registradas se conservan.", "ok")
        elif nuevo:
            conn.execute("UPDATE cursos_grado SET nombre = ? WHERE id = ?", (nuevo, cgid))
            registrar_auditoria(conn, u["id"], "renombrar_curso_grado", f"grado={gid} {cg['nombre']} -> {nuevo}")
            flash("Curso renombrado.", "ok")
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("boletas_grado", gid=gid))


@app.route("/boletas/grado/<int:gid>/estudiante/crear", methods=["POST"])
@boletas_required
def boletas_crear_estudiante(gid):
    u = current_user()
    conn = get_db()
    try:
        if _acceso_grado(conn, u, gid) != "admin":
            abort(403)
        nombres = request.form.get("nombres", "").strip()
        apellidos = request.form.get("apellidos", "").strip()
        codigo = request.form.get("codigo", "").strip()
        if not (nombres and apellidos):
            flash("Nombres y apellidos son obligatorios.", "error")
            return redirect(url_for("boletas_grado", gid=gid))
        conn.execute(
            """INSERT INTO estudiantes (grado_id, nombres, apellidos, codigo, activo, creado_en)
               VALUES (?, ?, ?, ?, 1, ?)""",
            (gid, nombres, apellidos, codigo, datetime.now().isoformat(timespec="seconds")),
        )
        # Asignar automáticamente los cursos del grado al nuevo estudiante
        db.backfill_notas(conn, gid)
        registrar_auditoria(conn, u["id"], "crear_estudiante", f"{apellidos}, {nombres} grado={gid}")
        conn.commit()
        n_cur = conn.execute(
            "SELECT COUNT(*) n FROM cursos_grado WHERE grado_id = ? AND activo = 1", (gid,)
        ).fetchone()["n"]
    finally:
        conn.close()
    flash(f"Estudiante agregado con {n_cur} curso(s) del grado.", "ok")
    return redirect(url_for("boletas_grado", gid=gid))


@app.route("/boletas/grado/<int:gid>/acceso", methods=["POST"])
@admin_required
def boletas_asignar_acceso(gid):
    f = request.form
    uid = f.get("usuario_id", type=int)
    nivel = "admin" if f.get("nivel") == "admin" else "ver"
    accion = f.get("accion", "asignar")
    conn = get_db()
    try:
        if not conn.execute("SELECT 1 FROM grados WHERE id = ?", (gid,)).fetchone():
            abort(404)
        if accion == "quitar":
            conn.execute("UPDATE accesos_grado SET activo = 0 WHERE grado_id = ? AND usuario_id = ?",
                         (gid, uid))
            flash("Acceso retirado.", "ok")
        else:
            existe = conn.execute(
                "SELECT id FROM accesos_grado WHERE grado_id = ? AND usuario_id = ?", (gid, uid)
            ).fetchone()
            if existe:
                conn.execute("UPDATE accesos_grado SET nivel = ?, activo = 1 WHERE id = ?",
                             (nivel, existe["id"]))
            else:
                conn.execute(
                    "INSERT INTO accesos_grado (usuario_id, grado_id, nivel, activo, creado_en) VALUES (?, ?, ?, 1, ?)",
                    (uid, gid, nivel, datetime.now().isoformat(timespec="seconds")),
                )
            flash(f"Acceso «{nivel}» asignado.", "ok")
        registrar_auditoria(conn, session["uid"], "acceso_grado", f"grado={gid} usuario={uid} {accion} {nivel}")
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("boletas_grado", gid=gid))


@app.route("/boletas/estudiante/<int:eid>", methods=["GET"])
@boletas_required
def boletas_estudiante(eid):
    u = current_user()
    conn = get_db()
    try:
        est = conn.execute("SELECT * FROM estudiantes WHERE id = ?", (eid,)).fetchone()
        if not est:
            abort(404)
        nivel = _acceso_grado(conn, u, est["grado_id"])
        if not nivel:
            abort(403)
        grado = conn.execute("SELECT * FROM grados WHERE id = ?", (est["grado_id"],)).fetchone()
        db.backfill_notas(conn, est["grado_id"])
        conn.commit()
        cursos = conn.execute(
            """SELECT cg.id AS curso_grado_id, cg.nombre,
                      n.id AS nota_id, n.u1, n.u2, n.u3, n.u4
               FROM cursos_grado cg
               LEFT JOIN notas n ON n.curso_grado_id = cg.id AND n.estudiante_id = ?
               WHERE cg.grado_id = ? AND cg.activo = 1
               ORDER BY cg.orden, cg.nombre""",
            (eid, est["grado_id"]),
        ).fetchall()
        cursos_prom = [(c, promedio_curso(c["u1"], c["u2"], c["u3"], c["u4"])) for c in cursos]
        prom_general = promedio_general(conn, eid)
        cmp_rows = {r["item"]: r for r in conn.execute(
            "SELECT * FROM comportamiento WHERE estudiante_id = ?", (eid,)
        ).fetchall()}
        comportamiento = [
            (clave, en, it, es, cmp_rows.get(clave))
            for (clave, en, it, es) in ITEMS_COMPORTAMIENTO
        ]
    finally:
        conn.close()
    return render_template("boletas_estudiante.html", est=est, grado=grado,
                           cursos_prom=cursos_prom, prom_general=prom_general,
                           comportamiento=comportamiento, escala=ESCALA_COMPORTAMIENTO,
                           nivel=nivel, nota_min=NOTA_MINIMA,
                           max_palabras=MAX_PALABRAS_OBS)


def _parse_nota(valor):
    valor = (valor or "").strip().replace(",", ".")
    if valor == "":
        return None
    try:
        n = float(valor)
    except ValueError:
        return None
    return max(0.0, min(100.0, n))


@app.route("/boletas/estudiante/<int:eid>/guardar", methods=["POST"])
@boletas_required
def boletas_guardar(eid):
    u = current_user()
    f = request.form
    conn = get_db()
    try:
        est = conn.execute("SELECT * FROM estudiantes WHERE id = ?", (eid,)).fetchone()
        if not est:
            abort(404)
        if _acceso_grado(conn, u, est["grado_id"]) != "admin":
            abort(403)

        obs = f.get("observaciones_comportamiento", "").strip()
        if contar_palabras(obs) > MAX_PALABRAS_OBS:
            flash(f"Las observaciones superan las {MAX_PALABRAS_OBS} palabras.", "error")
            return redirect(url_for("boletas_estudiante", eid=eid))

        conn.execute(
            "UPDATE estudiantes SET nombres=?, apellidos=?, codigo=?, observaciones_comportamiento=? WHERE id=?",
            (f.get("nombres", est["nombres"]).strip(),
             f.get("apellidos", est["apellidos"]).strip(),
             f.get("codigo", "").strip(),
             obs, eid),
        )
        cursos = conn.execute(
            "SELECT id FROM cursos_grado WHERE grado_id = ? AND activo = 1", (est["grado_id"],)
        ).fetchall()
        for cg in cursos:
            cgid = cg["id"]
            fila = conn.execute(
                "SELECT id FROM notas WHERE estudiante_id = ? AND curso_grado_id = ?", (eid, cgid)
            ).fetchone()
            valores = (
                _parse_nota(f.get(f"u1_{cgid}")),
                _parse_nota(f.get(f"u2_{cgid}")),
                _parse_nota(f.get(f"u3_{cgid}")),
                _parse_nota(f.get(f"u4_{cgid}")),
            )
            if fila:
                conn.execute("UPDATE notas SET u1=?, u2=?, u3=?, u4=? WHERE id=?",
                             (*valores, fila["id"]))
            else:
                conn.execute(
                    "INSERT INTO notas (estudiante_id, curso_grado_id, u1, u2, u3, u4) VALUES (?, ?, ?, ?, ?, ?)",
                    (eid, cgid, *valores),
                )

        # Sección fija de hábitos de trabajo / comportamiento (solo la letra A-D)
        def _parse_letra(v):
            v = (v or "").strip().upper()
            return v if v in LETRAS_COMPORTAMIENTO else None

        for clave in CLAVES_COMPORTAMIENTO:
            letras = (
                _parse_letra(f.get(f"cmp_{clave}_u1")),
                _parse_letra(f.get(f"cmp_{clave}_u2")),
                _parse_letra(f.get(f"cmp_{clave}_u3")),
                _parse_letra(f.get(f"cmp_{clave}_u4")),
            )
            fila = conn.execute(
                "SELECT id FROM comportamiento WHERE estudiante_id = ? AND item = ?", (eid, clave)
            ).fetchone()
            if fila:
                conn.execute("UPDATE comportamiento SET u1=?, u2=?, u3=?, u4=? WHERE id=?",
                             (*letras, fila["id"]))
            else:
                conn.execute(
                    "INSERT INTO comportamiento (estudiante_id, item, u1, u2, u3, u4) VALUES (?, ?, ?, ?, ?, ?)",
                    (eid, clave, *letras),
                )
        registrar_auditoria(conn, u["id"], "guardar_notas", f"est={eid}")
        conn.commit()
    finally:
        conn.close()
    flash("Cambios guardados.", "ok")
    return redirect(url_for("boletas_estudiante", eid=eid))


@app.route("/boletas/estudiante/<int:eid>/pdf")
@boletas_required
def boletas_pdf(eid):
    u = current_user()
    conn = get_db()
    try:
        est = conn.execute("SELECT * FROM estudiantes WHERE id = ?", (eid,)).fetchone()
        if not est:
            abort(404)
        if not _acceso_grado(conn, u, est["grado_id"]):
            abort(403)
        grado = conn.execute("SELECT * FROM grados WHERE id = ?", (est["grado_id"],)).fetchone()
        db.backfill_notas(conn, est["grado_id"])
        conn.commit()
        cursos = conn.execute(
            """SELECT cg.nombre, n.u1, n.u2, n.u3, n.u4
               FROM cursos_grado cg
               LEFT JOIN notas n ON n.curso_grado_id = cg.id AND n.estudiante_id = ?
               WHERE cg.grado_id = ? AND cg.activo = 1
               ORDER BY cg.orden, cg.nombre""",
            (eid, est["grado_id"]),
        ).fetchall()
        cmp_rows = {r["item"]: r for r in conn.execute(
            "SELECT * FROM comportamiento WHERE estudiante_id = ?", (eid,)
        ).fetchall()}
        comportamiento = []
        for (clave, en, it, es) in ITEMS_COMPORTAMIENTO:
            r = cmp_rows.get(clave)
            comportamiento.append({
                "en": en, "it": it, "es": es,
                "u1": r["u1"] if r else None, "u2": r["u2"] if r else None,
                "u3": r["u3"] if r else None, "u4": r["u4"] if r else None,
            })
        ruta = generar_boleta_pdf(dict(est), dict(grado), [dict(c) for c in cursos],
                                  promedio_general=promedio_general(conn, eid),
                                  comportamiento=comportamiento,
                                  escala=ESCALA_COMPORTAMIENTO)
        registrar_auditoria(conn, u["id"], "generar_boleta_pdf", f"est={eid}")
        conn.commit()
    finally:
        conn.close()
    return send_file(ruta, as_attachment=True)


@app.route("/boletas/estudiante/<int:eid>/eliminar", methods=["POST"])
@boletas_required
def boletas_eliminar_estudiante(eid):
    u = current_user()
    conn = get_db()
    try:
        est = conn.execute("SELECT * FROM estudiantes WHERE id = ?", (eid,)).fetchone()
        if not est:
            abort(404)
        if _acceso_grado(conn, u, est["grado_id"]) != "admin":
            abort(403)
        conn.execute("UPDATE estudiantes SET activo = 0 WHERE id = ?", (eid,))
        registrar_auditoria(conn, u["id"], "baja_estudiante", str(eid))
        conn.commit()
        gid = est["grado_id"]
    finally:
        conn.close()
    flash("Estudiante dado de baja (borrado lógico).", "ok")
    return redirect(url_for("boletas_grado", gid=gid))


# ==========================================================================
# Auditoría (solo admin)
# ==========================================================================
@app.route("/admin/auditoria")
@admin_required
def admin_auditoria():
    conn = get_db()
    try:
        registros = conn.execute(
            """SELECT a.*, u.username FROM auditoria a
               LEFT JOIN usuarios u ON u.id = a.usuario_id
               ORDER BY a.id DESC LIMIT 500"""
        ).fetchall()
    finally:
        conn.close()
    return render_template("admin_auditoria.html", registros=registros)


@app.errorhandler(403)
def e403(_):
    return render_template("error.html", codigo=403,
                           mensaje="No tiene permisos para acceder a esta sección."), 403


@app.errorhandler(404)
def e404(_):
    return render_template("error.html", codigo=404, mensaje="Página no encontrada."), 404


# ==========================================================================
# Arranque
# ==========================================================================
def abrir_navegador(puerto):
    webbrowser.open(f"http://127.0.0.1:{puerto}/")


def main():
    init_db()
    host, puerto, modo = cargar_config()
    ip = ip_local()
    def out(txt):
        try:
            print(txt)
        except UnicodeEncodeError:
            print(txt.encode("ascii", "replace").decode())

    out("=" * 64)
    out(" Sistema Liceo Italiano Trilingue")
    out("-" * 64)
    out(f" En esta computadora:       http://127.0.0.1:{puerto}/")
    if host == "0.0.0.0":
        out(f" Desde otras computadoras:  http://{ip}:{puerto}/   (red del colegio)")
    else:
        out(" Modo LOCAL: solo esta computadora puede usar el sistema.")
        out(f" Para habilitar la red, edite: {CONFIG_PATH}  (modo = red)")
    out("-" * 64)
    out(" Usuario administrador inicial:  admin  /  admin123")
    out(" (cambie la contrasena luego de ingresar)")
    out(" NO cierre esta ventana mientras el sistema este en uso.")
    out("=" * 64)
    threading.Timer(1.2, lambda: abrir_navegador(puerto)).start()
    app.run(host=host, port=puerto, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
