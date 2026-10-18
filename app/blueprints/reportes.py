"""Reportería (§10, pantalla 11): índice, cada reporte con sus filtros y exportación (RF-18)."""

from flask import Blueprint, Response, abort, g, render_template, request

from app import models as m
from app.blueprints.admin import NOMBRE_ESTADO
from app.services import reportes as r
from app.services import tiempo

bp = Blueprint("reportes", __name__, url_prefix="/reportes")

DIAS_AVISO_ELIMINACION = 30  # RF-26: aviso previo para exportar lo necesario


@bp.before_request
def _exigir_rol():
    if not r.puede_ver(g.get("usuario")):
        abort(403)


@bp.after_request
def _sin_cache(respuesta):
    respuesta.headers["Cache-Control"] = "no-store"  # incluye datos personales de postulantes
    return respuesta


def _contexto(filtros):
    proceso = filtros.proceso
    hoy = m.ahora().astimezone(tiempo.ZONA).date()
    dias = (proceso.fecha_eliminacion - hoy).days
    return {
        "filtros": filtros, "procesos": r.procesos(), "REPORTES": r.REPORTES, "RUTAS": r.RUTAS,
        "NOMBRE_ESTADO": NOMBRE_ESTADO,
        "eliminacion": proceso.fecha_eliminacion.strftime("%d-%m-%Y"),
        "aviso_eliminacion": not proceso.datos_eliminados_en and 0 <= dias <= DIAS_AVISO_ELIMINACION,
        "dias_eliminacion": dias,
    }


def _filtros_o_vacio():
    filtros = r.leer_filtros(request.args)
    if filtros is None:
        return None, (render_template("reportes/sin_procesos.html"), 200)
    return filtros, None


@bp.get("/")
def indice():
    filtros, vacio = _filtros_o_vacio()
    if vacio:
        return vacio
    return render_template("reportes/indice.html", **_contexto(filtros))


@bp.get("/<slug>")
def ver(slug):
    if slug not in r.REPORTES:
        abort(404)
    filtros, vacio = _filtros_o_vacio()
    if vacio:
        return vacio
    resultado = r.generar(slug, filtros)
    consulta = request.query_string.decode()
    return render_template(
        "reportes/ver.html", resultado=resultado, reporte=resultado["reporte"], consulta=consulta,
        carreras=m.Carrera.select().order_by(m.Carrera.nombre),
        fases=m.Fase.select().where(m.Fase.proceso == filtros.proceso.id).order_by(m.Fase.orden),
        limite=r.LIMITE_PANTALLA, **_contexto(filtros),
    )


@bp.get("/<slug>/exportar")
def exportar(slug):
    formato = request.args.get("formato")
    if slug not in r.REPORTES or formato not in ("csv", "xlsx"):
        abort(404)
    filtros, vacio = _filtros_o_vacio()
    if vacio:
        abort(404)
    contenido, tipo, nombre = r.exportar(slug, filtros, formato, g.usuario)
    return Response(contenido, mimetype=tipo, headers={"Content-Disposition": f"attachment; filename={nombre}"})

