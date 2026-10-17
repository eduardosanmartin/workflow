"""Vista pública de seguimiento con el enlace único del postulante (§10, pantalla 15; RF-20)."""

from flask import Blueprint, abort, make_response, render_template, request

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
    firma = seguimiento.firma(token)
    expediente = seguimiento.por_token(token)
    if expediente is None:
        abort(404)
    return render_template("seguimiento/ver.html", token=token, firma=firma, **seguimiento.vista(expediente))


@bp.get("/seguimiento/<token>/estado")
@publico
def estado(token):
    """Sondeo cada 10 s (RF-20). Si la firma coincide con la que trae el navegador en
    If-None-Match, responde 304 sin armar la vista (RNF-04)."""
    firma = seguimiento.firma(token)
    if firma is None:
        abort(404)
    if firma in request.if_none_match:
        respuesta = make_response("", 304)
    else:
        expediente = seguimiento.por_token(token)
        respuesta = make_response(render_template("seguimiento/_estado.html", **seguimiento.vista(expediente)))
    respuesta.set_etag(firma)
    return respuesta
