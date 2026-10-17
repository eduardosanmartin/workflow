"""CAPTCHA del formulario QR con Cloudflare Turnstile (RF-10, protección contra abuso).

Si faltan `TURNSTILE_SITE_KEY` o `TURNSTILE_SECRET_KEY`, el CAPTCHA queda desactivado
(desarrollo). Ante una falla de red con Cloudflare, se rechaza: no se deja pasar sin verificar.
"""

import json
import logging
import urllib.parse
import urllib.request

from flask import current_app

URL_VERIFICACION = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
CAMPO = "cf-turnstile-response"
LARGO_MAXIMO_TOKEN = 2048  # máximo documentado por Cloudflare
MENSAJE = "Confirma que no eres un robot e inténtalo nuevamente."

log = logging.getLogger(__name__)


def clave_sitio() -> str | None:
    """Clave pública para el widget, o None si el CAPTCHA está desactivado."""
    config = current_app.config
    if config.get("TURNSTILE_SITE_KEY") and config.get("TURNSTILE_SECRET_KEY"):
        return config["TURNSTILE_SITE_KEY"]
    return None


def _consultar(datos: dict) -> dict:
    cuerpo = urllib.parse.urlencode(datos).encode()
    with urllib.request.urlopen(URL_VERIFICACION, cuerpo, timeout=5) as respuesta:  # noqa: S310 (URL fija https)
        return json.load(respuesta)


def verificar(token: str | None, ip: str | None) -> bool:
    if clave_sitio() is None:
        return True
    if not token or len(token) > LARGO_MAXIMO_TOKEN:
        return False
    datos = {"secret": current_app.config["TURNSTILE_SECRET_KEY"], "response": token}
    if ip:
        datos["remoteip"] = ip
    try:
        resultado = _consultar(datos)
    except (OSError, ValueError) as error:
        log.warning("No se pudo verificar el CAPTCHA con Cloudflare: %s", error)
        return False
    return resultado.get("success") is True
