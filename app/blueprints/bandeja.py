"""Bandeja de trabajo por fase y pantalla "Finalizar y derivar" (§10: 7 y 7b; RF-17)."""

from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from app.security import destino_seguro
from app.services import bandeja, inicio, llamados
from app.services import workflow as wf

bp = Blueprint("bandeja", __name__, url_prefix="/bandeja")


@bp.before_request
def _permiso():
    g.proceso = inicio.proceso_activo()
    if g.proceso is None:
        if request.method == "POST":
            flash(inicio.SIN_PROCESO_ACTIVO, "error")
        return render_template("bandeja/sin_proceso.html"), 409 if request.method == "POST" else 200
    g.fases = bandeja.fases_del_usuario(g.usuario, g.proceso)
    if not g.fases:
        abort(403)
    return None


@bp.after_request
def _sin_cache(respuesta):
    respuesta.headers["Cache-Control"] = "no-store"  # datos personales del postulante
    return respuesta


def _filtros() -> dict:
    """Filtros válidos de la URL; los inválidos se ignoran."""
    filtros = {}
    fase = request.args.get("fase", "")
    if fase.isdigit() and int(fase) in {f.id for f in g.fases}:
        filtros["fase_id"] = int(fase)
    try:
        filtros["fecha"] = date.fromisoformat(request.args.get("fecha", ""))
    except ValueError:
        pass
    carrera = request.args.get("carrera", "").strip()
    if carrera:
        filtros["carrera"] = carrera[:20]
    return filtros


def _volver():
    """Vuelve a la bandeja con sus filtros, o a la ficha desde la que se actuó."""
    destino = destino_seguro(request.values.get("volver"))
    return destino if destino.startswith((url_for("bandeja.ver"), url_for("ficha.buscar"))) else url_for("bandeja.ver")


def _abortar_si_corresponde(error):
    if error.codigo_http in (403, 404):
        abort(error.codigo_http)


@bp.get("/")
def ver():
    filtros = _filtros()
    listas = bandeja.listar(g.usuario, g.proceso, **filtros)
    volver = url_for("bandeja.ver", **{k: v for k, v in request.args.items() if k != "parcial"})
    contexto = {"listas": listas, "varias_fases": len(g.fases) > 1 and "fase_id" not in filtros, "volver": volver}
    if request.args.get("parcial") == "1":
        return render_template("bandeja/_listas.html", **contexto)
    return render_template(
        "bandeja/ver.html", filtros=filtros, fases=g.fases, carreras=inicio.opciones_de_carrera(),
        **contexto,
    )


@bp.post("/<int:expediente_id>/atender")
def atender(expediente_id):
    try:
        flash(bandeja.atender(expediente_id, request.form.get("version"), g.usuario), "exito")
    except wf.ReglaVioladaError as error:
        _abortar_si_corresponde(error)
        flash(error.mensaje, "error")
    return redirect(_volver())


@bp.post("/<int:expediente_id>/llamar")
def llamar(expediente_id):
    """Llama al postulante a la pantalla del hall, o lo vuelve a llamar."""
    try:
        flash(llamados.llamar(expediente_id, g.usuario), "exito")
    except wf.ReglaVioladaError as error:
        _abortar_si_corresponde(error)
        flash(error.mensaje, "error")
    return redirect(_volver())


@bp.post("/<int:expediente_id>/cancelar-llamado")
def cancelar_llamado(expediente_id):
    try:
        flash(llamados.cancelar(expediente_id, g.usuario), "exito")
    except wf.ReglaVioladaError as error:
        _abortar_si_corresponde(error)
        flash(error.mensaje, "error")
    return redirect(_volver())


@bp.post("/modulo")
def modulo():
    """El usuario indica en qué módulo atiende: es lo que anuncia la pantalla del hall."""
    try:
        valor = llamados.guardar_modulo(g.usuario, request.form.get("modulo"))
        flash(f"Su módulo es el {valor}" if valor else "Se quitó su módulo", "exito")
    except wf.ReglaVioladaError as error:
        flash(error.mensaje, "error")
    return redirect(_volver())


def _finalizar(expediente_id, form=None, codigo=200):
    datos = bandeja.detalle(expediente_id, g.usuario)
    if form is None:  # propone la ruta que ya estaba planificada (RF-14)
        form = {f"orden_{fase_id}": str(n) for fase_id, n in datos["orden"].items()}
        form["accion"] = "seguir"
    return render_template("bandeja/finalizar.html", form=form, volver=_volver(), **datos), codigo


@bp.get("/<int:expediente_id>/finalizar")
def finalizar_form(expediente_id):
    try:
        return _finalizar(expediente_id)
    except wf.ReglaVioladaError as error:
        _abortar_si_corresponde(error)
        flash(error.mensaje, "error")
        return redirect(_volver())


@bp.post("/<int:expediente_id>/finalizar")
def finalizar(expediente_id):
    try:
        mensaje = bandeja.finalizar(expediente_id, request.form, g.usuario)
    except wf.ReglaVioladaError as error:
        _abortar_si_corresponde(error)
        flash(error.mensaje, "error")
        try:
            return _finalizar(expediente_id, request.form.to_dict(), error.codigo_http)
        except wf.ReglaVioladaError:  # ya no tiene atención en curso: vuelve a la bandeja
            return redirect(_volver())
    flash(mensaje, "exito")
    return redirect(_volver())


@bp.get("/<int:expediente_id>/abandono")
def abandono_form(expediente_id):
    try:
        datos = bandeja.detalle(expediente_id, g.usuario)
    except wf.ReglaVioladaError as error:
        _abortar_si_corresponde(error)
        flash(error.mensaje, "error")
        return redirect(_volver())
    return render_template("bandeja/abandono.html", volver=_volver(), **datos)


@bp.post("/<int:expediente_id>/abandono")
def abandono(expediente_id):
    try:
        flash(bandeja.marcar_abandono(expediente_id, request.form.get("version"), g.usuario), "exito")
    except wf.ReglaVioladaError as error:
        _abortar_si_corresponde(error)
        flash(error.mensaje, "error")
    return redirect(_volver())
