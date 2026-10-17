"""Búsqueda por RUT y ficha del postulante (§10, pantalla 3; RF-15, RF-27, RF-28)."""

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from app import models as m
from app.services import correo, ficha, inicio
from app.services import workflow as wf

bp = Blueprint("ficha", __name__, url_prefix="/ficha")


@bp.after_request
def _sin_cache(respuesta):
    respuesta.headers["Cache-Control"] = "no-store"  # datos personales del postulante
    return respuesta


def _buscar(mensaje=None, rut="", codigo=200):
    return render_template("ficha/buscar.html", mensaje=mensaje, rut=rut), codigo


def _expediente(expediente_id):
    expediente = m.Expediente.get_or_none(m.Expediente.id == expediente_id)
    if expediente is None:
        abort(404)
    return expediente


def _a_la_ficha(expediente):
    return redirect(url_for("ficha.ver", postulante_id=expediente.postulante_id))


def _vista(expediente):
    """Datos del expediente para las pantallas de acción, desde la misma vista de la ficha."""
    vista = ficha.ver(expediente.postulante_id, g.usuario)
    return next(e for e in vista["expedientes"] if e["expediente"].id == expediente.id), vista


def _error(error, expediente):
    if error.codigo_http in (403, 404):
        abort(error.codigo_http)
    flash(error.mensaje, "error")
    return _a_la_ficha(expediente)


@bp.get("/")
def buscar():
    return _buscar()


@bp.post("/buscar")
def buscar_post():
    ingresado = request.form.get("rut", "")
    try:
        postulante = ficha.buscar(ingresado)
    except wf.ReglaVioladaError as error:
        return _buscar(error.mensaje, ingresado, error.codigo_http)
    if postulante is None:
        rut = inicio.normalizar_rut(ingresado)
        return _buscar(f"No hay un postulante con el RUT {rut}.", ingresado, 404)
    return redirect(url_for("ficha.ver", postulante_id=postulante.id))


@bp.get("/<int:postulante_id>")
def ver(postulante_id):
    try:
        vista = ficha.ver(postulante_id, g.usuario)
    except wf.ReglaVioladaError as error:
        abort(error.codigo_http if error.codigo_http in (403, 404) else 409)
    return render_template("ficha/ver.html", **vista)


# ── Reactivación (RF-28) ──

@bp.get("/expediente/<int:expediente_id>/reactivar")
def reactivar_form(expediente_id):
    expediente = _expediente(expediente_id)
    try:
        opciones = ficha.opciones_reactivar(expediente_id, g.usuario)
    except wf.ReglaVioladaError as error:
        return _error(error, expediente)
    exp, vista = _vista(expediente)
    return render_template("ficha/reactivar.html", exp=exp, postulante=vista["postulante"], **opciones)


@bp.post("/expediente/<int:expediente_id>/reactivar")
def reactivar(expediente_id):
    expediente = _expediente(expediente_id)
    try:
        flash(ficha.reactivar(expediente_id, request.form, g.usuario), "exito")
    except wf.ReglaVioladaError as error:
        return _error(error, expediente)
    return _a_la_ficha(expediente)


# ── Reenvío del enlace (RF-21, cualquier rol) ──

@bp.post("/expediente/<int:expediente_id>/reenviar")
def reenviar(expediente_id):
    expediente = _expediente(expediente_id)
    try:
        flash(correo.reenviar(expediente_id, g.usuario), "exito")
    except wf.ReglaVioladaError as error:
        return _error(error, expediente)
    return _a_la_ficha(expediente)


# ── Correcciones (RF-27, Gestor y Administrador) ──

def _exigir_gestion():
    if g.usuario.rol not in wf.ROLES_TODAS_LAS_FASES:
        abort(403)


def _corregir(expediente, form=None, codigo=200):
    exp, vista = _vista(expediente)
    if not exp["puede_corregir"]:
        flash("La ruta de este expediente no se puede corregir en su estado actual.", "error")
        return _a_la_ficha(expediente)
    if form is None:
        form = {f"orden_{fase_id}": str(n) for fase_id, n in exp["orden"].items()}
    return render_template("ficha/corregir.html", exp=exp, postulante=vista["postulante"], form=form), codigo


@bp.get("/expediente/<int:expediente_id>/corregir")
def corregir_form(expediente_id):
    _exigir_gestion()
    return _corregir(_expediente(expediente_id))


@bp.post("/expediente/<int:expediente_id>/corregir")
def corregir(expediente_id):
    _exigir_gestion()
    expediente = _expediente(expediente_id)
    try:
        flash(ficha.corregir_ruta(expediente_id, request.form, g.usuario), "exito")
    except wf.ReglaVioladaError as error:
        if error.codigo_http in (403, 404):
            abort(error.codigo_http)
        flash(error.mensaje, "error")
        return _corregir(expediente, request.form.to_dict(), error.codigo_http)
    return _a_la_ficha(expediente)


@bp.get("/expediente/<int:expediente_id>/reabrir")
def reabrir_form(expediente_id):
    _exigir_gestion()
    expediente = _expediente(expediente_id)
    exp, vista = _vista(expediente)
    if not exp["puede_reabrir"]:
        flash("No hay una atención que se pueda reabrir.", "error")
        return _a_la_ficha(expediente)
    return render_template("ficha/reabrir.html", exp=exp, postulante=vista["postulante"],
                           motivo=request.args.get("motivo", ""))


@bp.post("/expediente/<int:expediente_id>/reabrir")
def reabrir(expediente_id):
    _exigir_gestion()
    expediente = _expediente(expediente_id)
    try:
        flash(ficha.reabrir(expediente_id, request.form, g.usuario), "exito")
    except wf.ReglaVioladaError as error:
        return _error(error, expediente)
    return _a_la_ficha(expediente)

