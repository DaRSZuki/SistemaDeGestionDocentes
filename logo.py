"""Resuelve y cachea una versión reducida del logo del colegio.

El usuario coloca `static/img/logo.png` (puede ser muy grande). Para no incrustar
7 MB en cada PDF ni servirlos en cada página, se genera una copia reducida en
`datos/logo_cache.png` y se reutiliza.
"""

import os

from db import resource_path, DATA_DIR


def logo_origen():
    for ext in ("png", "jpg", "jpeg"):
        p = os.path.join(resource_path("static"), "img", "logo." + ext)
        if os.path.exists(p):
            return p
    return None


def logo_optimizado(max_px: int = 512):
    """Ruta a una versión reducida del logo (cacheada). Si falla, el original. Si no hay, None."""
    src = logo_origen()
    if not src:
        return None
    try:
        if os.path.getsize(src) < 200 * 1024:
            return src  # ya es pequeño
        cache = os.path.join(DATA_DIR, "logo_cache.png")
        if (not os.path.exists(cache)) or os.path.getmtime(cache) < os.path.getmtime(src):
            from PIL import Image
            im = Image.open(src)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA")
            im.thumbnail((max_px, max_px))
            os.makedirs(DATA_DIR, exist_ok=True)
            im.save(cache, "PNG", optimize=True)
        return cache
    except Exception:
        return src
