"""
Acceso a datos y esquema de la base de datos (SQLite).

La base de datos y todos los archivos generados se guardan en la carpeta
``datos/`` ubicada junto al ejecutable (o junto a este archivo en desarrollo).
De esta forma el sistema es totalmente transportable: basta con copiar la
carpeta del programa junto con la carpeta ``datos/``.
"""

import os
import sys
import socket
import sqlite3
import secrets
import configparser
from datetime import datetime


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------
def base_dir() -> str:
    """Carpeta donde viven los datos persistentes (junto al .exe)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(rel: str) -> str:
    """Ruta a recursos empaquetados (templates / static) compatible con PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


DATA_DIR = os.path.join(base_dir(), "datos")
DB_PATH = os.path.join(DATA_DIR, "liceo.db")
BOLETAS_DIR = os.path.join(DATA_DIR, "boletas")
SECRET_PATH = os.path.join(DATA_DIR, "secret.key")
CONFIG_PATH = os.path.join(DATA_DIR, "configuracion.ini")

PLANTILLA_CONFIG = """\
; Configuracion del servidor - Sistema Liceo Italiano Trilingue
; Edite este archivo y vuelva a abrir el programa para aplicar los cambios.

[servidor]
; modo = local  -> solo se puede usar en ESTA computadora
; modo = red    -> otras computadoras de la red local del colegio pueden entrar
modo = red

; Puerto (dejar 5000 salvo que otro programa lo use)
puerto = 5000
"""


def cargar_config():
    """Devuelve (host, puerto, modo). Crea el archivo de configuración si no existe."""
    _ensure_dirs()
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            fh.write(PLANTILLA_CONFIG)
    cp = configparser.ConfigParser()
    try:
        cp.read(CONFIG_PATH, encoding="utf-8")
    except configparser.Error:
        pass
    modo = cp.get("servidor", "modo", fallback="red").strip().lower()
    try:
        puerto = cp.getint("servidor", "puerto", fallback=5000)
    except (ValueError, configparser.Error):
        puerto = 5000
    host = "0.0.0.0" if modo in ("red", "lan", "network") else "127.0.0.1"
    return host, puerto, modo


def ip_local() -> str:
    """IP de esta computadora en la red local (para dársela a los demás equipos)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # no envía datos, solo elige la interfaz
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"

DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]

# --------------------------------------------------------------------------
# Sección FIJA de "Hábitos de trabajo / Work Habits" de la boleta.
# El orden y las etiquetas no cambian; lo único editable es la nota (A/B/C/D).
# Cada elemento: (clave, Inglés, Italiano, Español)
# --------------------------------------------------------------------------
ITEMS_COMPORTAMIENTO = [
    ("cleaning",       "Cleaning",             "Pulizia",                          "Limpieza"),
    ("participation",  "Class Participation",  "Partecipazione alla classe",       "Participación en clase"),
    ("responsibility", "Responsibility",       "Responsabilità",                   "Responsabilidad"),
    ("school_rules",   "Follow School Rules",  "Rispettare le regole della scuola","Cumplir las reglas del colegio"),
    ("homework",       "Homework",             "Compiti a casa",                   "Tareas"),
    ("behavior",       "Behavior",             "Comportamento",                    "Comportamiento"),
    ("instructions",   "Follow Instructions",  "Seguire le istruzioni",            "Sigue instrucciones"),
]
CLAVES_COMPORTAMIENTO = [c[0] for c in ITEMS_COMPORTAMIENTO]

# Escala de calificación de esa sección (también fija). (letra, rango, Inglés, Italiano, Español)
ESCALA_COMPORTAMIENTO = [
    ("A", "90–100", "Excellent",       "Eccellente",          "Excelente"),
    ("B", "80–89",  "Very Good",        "Molto bene",          "Muy Bien"),
    ("C", "70–79",  "Satisfactory",     "Bene",                "Satisfactorio"),
    ("D", "60–69",  "Need to improve",  "Bisogna migliorare",  "Necesita mejorar"),
]
LETRAS_COMPORTAMIENTO = {"A", "B", "C", "D"}


def _ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(BOLETAS_DIR, exist_ok=True)


def get_secret_key() -> bytes:
    _ensure_dirs()
    if not os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "w", encoding="utf-8") as fh:
            fh.write(secrets.token_hex(32))
    with open(SECRET_PATH, "r", encoding="utf-8") as fh:
        return fh.read().strip().encode("utf-8")


# --------------------------------------------------------------------------
# Conexión
# --------------------------------------------------------------------------
def get_db() -> sqlite3.Connection:
    _ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# --------------------------------------------------------------------------
# Esquema
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    nombres         TEXT NOT NULL,
    apellidos       TEXT NOT NULL,
    rol             TEXT NOT NULL DEFAULT 'docente',   -- 'admin' | 'docente'
    puede_boletas   INTEGER NOT NULL DEFAULT 0,
    activo          INTEGER NOT NULL DEFAULT 1,
    creado_en       TEXT NOT NULL,
    dado_baja_en    TEXT
);

CREATE TABLE IF NOT EXISTS horarios (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id      INTEGER NOT NULL REFERENCES usuarios(id),
    dia_semana      INTEGER NOT NULL,                  -- 0=Lunes ... 6=Domingo
    laborable       INTEGER NOT NULL DEFAULT 1,
    hora_entrada    TEXT,                              -- 'HH:MM'
    hora_salida     TEXT,                              -- 'HH:MM'
    tolerancia_min  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(usuario_id, dia_semana)
);

CREATE TABLE IF NOT EXISTS asistencias (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id        INTEGER NOT NULL REFERENCES usuarios(id),
    fecha             TEXT NOT NULL,                   -- 'YYYY-MM-DD'
    hora_entrada      TEXT,                            -- 'HH:MM:SS'
    hora_salida       TEXT,                            -- 'HH:MM:SS'
    minutos_atraso    INTEGER NOT NULL DEFAULT 0,
    estado            TEXT NOT NULL DEFAULT 'presente',-- presente|ausente|justificado|permiso
    observacion       TEXT,
    modificado_admin  INTEGER NOT NULL DEFAULT 0,
    modificado_por    INTEGER REFERENCES usuarios(id),
    modificado_en     TEXT,
    UNIQUE(usuario_id, fecha)
);

CREATE TABLE IF NOT EXISTS grados (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre      TEXT NOT NULL,
    anio        INTEGER NOT NULL,
    activo      INTEGER NOT NULL DEFAULT 1,
    creado_por  INTEGER REFERENCES usuarios(id),
    creado_en   TEXT NOT NULL,
    UNIQUE(nombre, anio)
);

CREATE TABLE IF NOT EXISTS estudiantes (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    grado_id                      INTEGER NOT NULL REFERENCES grados(id),
    nombres                       TEXT NOT NULL,
    apellidos                     TEXT NOT NULL,
    codigo                        TEXT,
    observaciones_comportamiento  TEXT,
    activo                        INTEGER NOT NULL DEFAULT 1,
    creado_en                     TEXT NOT NULL
);

-- Catálogo de cursos por grado. Todos los estudiantes del grado comparten estos cursos.
CREATE TABLE IF NOT EXISTS cursos_grado (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    grado_id    INTEGER NOT NULL REFERENCES grados(id),
    nombre      TEXT NOT NULL,
    orden       INTEGER NOT NULL DEFAULT 0,
    activo      INTEGER NOT NULL DEFAULT 1
);

-- Notas de un estudiante en un curso del grado (4 unidades). El promedio se calcula.
CREATE TABLE IF NOT EXISTS notas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    estudiante_id   INTEGER NOT NULL REFERENCES estudiantes(id),
    curso_grado_id  INTEGER NOT NULL REFERENCES cursos_grado(id),
    u1              REAL,
    u2              REAL,
    u3              REAL,
    u4              REAL,
    UNIQUE(estudiante_id, curso_grado_id)
);

-- Sección fija de hábitos de trabajo / comportamiento. Una fila por (estudiante, ítem).
-- u1..u4 guardan la letra ('A'|'B'|'C'|'D') o NULL.
CREATE TABLE IF NOT EXISTS comportamiento (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    estudiante_id   INTEGER NOT NULL REFERENCES estudiantes(id),
    item            TEXT NOT NULL,
    u1              TEXT,
    u2              TEXT,
    u3              TEXT,
    u4              TEXT,
    UNIQUE(estudiante_id, item)
);

CREATE TABLE IF NOT EXISTS accesos_grado (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id),
    grado_id    INTEGER NOT NULL REFERENCES grados(id),
    nivel       TEXT NOT NULL DEFAULT 'ver',           -- 'admin' | 'ver'
    activo      INTEGER NOT NULL DEFAULT 1,
    creado_en   TEXT NOT NULL,
    UNIQUE(usuario_id, grado_id)
);

CREATE TABLE IF NOT EXISTS auditoria (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id  INTEGER,
    accion      TEXT NOT NULL,
    detalle     TEXT,
    creado_en   TEXT NOT NULL
);
"""


def init_db() -> None:
    """Crea las tablas si no existen y el administrador por defecto."""
    from werkzeug.security import generate_password_hash

    _ensure_dirs()
    conn = get_db()
    try:
        conn.executescript(SCHEMA)
        _migrar_cursos_por_grado(conn)
        backfill_notas(conn)
        row = conn.execute("SELECT COUNT(*) AS n FROM usuarios WHERE rol = 'admin'").fetchone()
        if row["n"] == 0:
            conn.execute(
                """INSERT INTO usuarios (username, password_hash, nombres, apellidos,
                                         rol, puede_boletas, activo, creado_en)
                   VALUES (?, ?, ?, ?, 'admin', 1, 1, ?)""",
                (
                    "admin",
                    generate_password_hash("admin123"),
                    "Administrador",
                    "General",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _migrar_cursos_por_grado(conn: sqlite3.Connection) -> None:
    """Migra el modelo antiguo (cursos por estudiante) al nuevo (cursos por grado + notas)."""
    tablas = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "cursos" not in tablas:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(cursos)")}
    if "estudiante_id" not in cols:
        return  # ya está en el modelo nuevo

    viejos = conn.execute(
        """SELECT c.estudiante_id, c.nombre, c.u1, c.u2, c.u3, c.u4, e.grado_id
           FROM cursos c JOIN estudiantes e ON e.id = c.estudiante_id"""
    ).fetchall()
    cache = {}
    for v in viejos:
        key = (v["grado_id"], (v["nombre"] or "").strip().lower())
        cgid = cache.get(key)
        if cgid is None:
            cur = conn.execute(
                "INSERT INTO cursos_grado (grado_id, nombre, orden, activo) VALUES (?, ?, 0, 1)",
                (v["grado_id"], (v["nombre"] or "").strip()),
            )
            cgid = cur.lastrowid
            cache[key] = cgid
        conn.execute(
            """INSERT OR IGNORE INTO notas (estudiante_id, curso_grado_id, u1, u2, u3, u4)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (v["estudiante_id"], cgid, v["u1"], v["u2"], v["u3"], v["u4"]),
        )
    # Se conserva la tabla anterior por trazabilidad de datos.
    conn.execute("ALTER TABLE cursos RENAME TO cursos_legacy")


def backfill_notas(conn: sqlite3.Connection, grado_id=None) -> None:
    """Garantiza filas base: una en 'notas' por (estudiante activo, curso activo de su
    grado) y una en 'comportamiento' por (estudiante activo, ítem fijo)."""
    q = "SELECT id, grado_id FROM estudiantes WHERE activo = 1"
    params = ()
    if grado_id is not None:
        q += " AND grado_id = ?"
        params = (grado_id,)
    for e in conn.execute(q, params).fetchall():
        for cg in conn.execute(
            "SELECT id FROM cursos_grado WHERE grado_id = ? AND activo = 1", (e["grado_id"],)
        ).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO notas (estudiante_id, curso_grado_id) VALUES (?, ?)",
                (e["id"], cg["id"]),
            )
        for clave in CLAVES_COMPORTAMIENTO:
            conn.execute(
                "INSERT OR IGNORE INTO comportamiento (estudiante_id, item) VALUES (?, ?)",
                (e["id"], clave),
            )


def promedio_general(conn: sqlite3.Connection, estudiante_id):
    """Promedio de los promedios de todos los cursos activos del estudiante."""
    filas = conn.execute(
        """SELECT n.u1, n.u2, n.u3, n.u4 FROM notas n
           JOIN cursos_grado cg ON cg.id = n.curso_grado_id AND cg.activo = 1
           WHERE n.estudiante_id = ?""",
        (estudiante_id,),
    ).fetchall()
    vals = [promedio_curso(f["u1"], f["u2"], f["u3"], f["u4"]) for f in filas]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def registrar_auditoria(conn: sqlite3.Connection, usuario_id, accion: str, detalle: str = "") -> None:
    conn.execute(
        "INSERT INTO auditoria (usuario_id, accion, detalle, creado_en) VALUES (?, ?, ?, ?)",
        (usuario_id, accion, detalle, datetime.now().isoformat(timespec="seconds")),
    )


# --------------------------------------------------------------------------
# Utilidades de negocio
# --------------------------------------------------------------------------
def promedio_curso(u1, u2, u3, u4):
    notas = [n for n in (u1, u2, u3, u4) if n is not None]
    if not notas:
        return None
    return round(sum(notas) / len(notas), 2)


def contar_palabras(texto: str) -> int:
    return len((texto or "").split())
