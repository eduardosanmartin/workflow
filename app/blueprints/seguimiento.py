"""Vista pública de seguimiento con el enlace único del postulante (§10, pantalla 15; RF-20)."""

from flask import Blueprint, abort, render_template

from app.security import publico
from app.services import seguimiento

bp = Blueprint("seguimiento", __name__)


@bp.after_request
def _cabeceras(respuesta):
    respuesta.headers["Cache-Control"] = "no-store"
    respuesta.headers["Referrer-Policy"] = "no-referrer"  # el token no debe salir en el Referer
    respuesta.headers["X-Robots-Tag"] = "noindex"
    return respuesta


@bp.get("/seguimiento/<token>")
@publico
def ver(token):
    # Sin límite por IP: muchos postulantes comparten la red del campus y el token (256 bits) no se adivina
    expediente = seguimiento.por_token(token)
    if expediente is None:
        abort(404)
    return render_template("seguimiento/ver.html", **seguimiento.vista(expediente))
