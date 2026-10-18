"""Póster imprimible del QR "Inicia aquí" (§10, pantalla 16; RF-10).

El enlace sale siempre de APP_BASE_URL: al cambiar el dominio basta con editar el .env y
reimprimir el póster.
"""

import io
import re

import qrcode
import qrcode.image.svg
from flask import current_app, url_for


def enlace() -> str:
    return current_app.config["APP_BASE_URL"] + url_for("qr.rut")


def svg(texto: str) -> str:
    """QR en SVG (nítido a cualquier tamaño de impresión), con corrección de errores alta para
    que se lea aunque el papel se arrugue o se manche."""
    codigo = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, border=2,
                           image_factory=qrcode.image.svg.SvgPathImage)
    codigo.add_data(texto)
    codigo.make(fit=True)
    salida = io.BytesIO()
    codigo.make_image().save(salida)
    contenido = salida.getvalue().decode("utf-8")
    contenido = re.sub(r"<\?xml[^>]*\?>\s*", "", contenido)
    # Sin ancho ni alto fijos: el tamaño lo da la hoja de estilos
    return re.sub(r'\s(width|height)="[^"]*"', "", contenido, count=2)


def advertencias(url: str) -> list:
    avisos = []
    if "localhost" in url or "127.0.0.1" in url:
        avisos.append("El enlace apunta a este computador (localhost): configure APP_BASE_URL con el dominio "
                      "público antes de imprimir.")
    elif not url.startswith("https://"):
        avisos.append("El enlace no usa https: configure APP_BASE_URL con la dirección segura antes de imprimir.")
    return avisos
