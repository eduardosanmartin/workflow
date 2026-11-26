"""Pantalla del hall de espera: llamados a módulo y postulantes en atención.

Es pública para que el televisor del hall no necesite una sesión del staff: solo muestra
números de atención, fases y módulos, sin datos personales.
"""

from flask import Blueprint, jsonify, render_template

from app.security import publico
from app.services import inicio, llamados

bp = Blueprint("pantalla", __name__, url_prefix="/pantalla")


@bp.after_request
def _cabeceras(respuesta):
    respuesta.headers["Cache-Control"] = "no-store"
    respuesta.headers["X-Robots-Tag"] = "noindex"
    return respuesta


@bp.get("/")
@publico
def ver():
    return render_template("pantalla/ver.html", **llamados.pantalla(inicio.proceso_activo()))


@bp.get("/estado")
@publico
def estado():
    """Sondeo de la pantalla cada pocos segundos."""
    return jsonify(llamados.pantalla(inicio.proceso_activo()))
