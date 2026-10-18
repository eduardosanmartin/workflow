"""Auditoría (§10, pantalla 12): consulta con filtros y exportación, en solo lectura (RF-24)."""

from urllib.parse import urlencode

from flask import Blueprint, Response, abort, g, render_template, request

from app.services import consulta_auditoria as ca

bp = Blueprint("auditoria", __name__, url_prefix="/auditoria")


@bp.before_request
def _exigir_rol():
    if not ca.puede_ver(g.get("usuario")):
        abort(403)


@bp.after_request
def _sin_cache(respuesta):
    respuesta.headers["Cache-Control"] = "no-store"  # incluye datos personales y direcciones IP
    return respuesta


def _consulta(**cambios):
    """Parámetros actuales de la URL, con cambios (ej.: otra página)."""
    valores = {k: v for k, v in request.args.items() if v and k != "pagina"}
    valores.update({k: v for k, v in cambios.items() if v})
    return urlencode(valores)


@bp.get("/")
def ver():
    filtros = ca.leer_filtros(request.args, g.usuario)
    return render_template("auditoria/ver.html", filtros=filtros, resultado=ca.buscar(filtros, g.usuario),
                           consulta=_consulta, ACTORES=ca.ACTORES, **ca.opciones(g.usuario))


@bp.get("/exportar")
def exportar():
    formato = request.args.get("formato")
    if formato not in ("csv", "xlsx"):
        abort(404)
    filtros = ca.leer_filtros(request.args, g.usuario)
    contenido, tipo, nombre = ca.exportar(filtros, formato, g.usuario)
    return Response(contenido, mimetype=tipo, headers={"Content-Disposition": f"attachment; filename={nombre}"})
