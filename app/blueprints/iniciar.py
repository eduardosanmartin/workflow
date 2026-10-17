"""Pantalla "Iniciar proceso" del staff (§10, pantalla 2; RF-09)."""

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from app import models as m
from app.services import inicio, recorridos
from app.services import workflow as wf
from app.services.postulantes import CAMPOS_EDITABLES, CARRERAS, campos_pendientes

bp = Blueprint("iniciar", __name__, url_prefix="/iniciar")


@bp.before_request
def _permiso():
    """Sin proceso activo, se informa a todo el staff. Con proceso activo, solo pueden entrar
    el Administrador, el Gestor y los responsables de "Iniciado" (§4)."""
    g.proceso = inicio.proceso_activo()
    if g.proceso is None:
        if request.method == "POST":
            flash(inicio.SIN_PROCESO_ACTIVO, "error")
        return render_template("iniciar/sin_proceso.html"), 409 if request.method == "POST" else 200
    if not inicio.puede_iniciar(g.usuario, g.proceso):
        abort(403)
    return None


@bp.after_request
def _sin_cache(respuesta):
    respuesta.headers["Cache-Control"] = "no-store"  # datos personales del postulante
    return respuesta


def _formulario(datos, errores=None, ruta=None, existe=False):
    estructura = recorridos.estructura_del_proceso(g.proceso.id)
    nombres = {f.id: f.nombre for f in estructura.fases}
    return render_template(
        "iniciar/formulario.html",
        datos=datos, errores=errores or {}, ruta=ruta or {}, existe=existe,
        pendientes=campos_pendientes(datos) if existe else [],
        carreras=inicio.opciones_de_carrera([datos.get(c) for c in CARRERAS]),
        consultas=[f for f in estructura.fases if f.tipo == wf.CONSULTA],
        flujos=inicio.opciones_de_flujo(estructura, nombres),
        mnp=estructura.mnp,
    ), 422 if errores else 200


def _existente(expediente):
    return render_template("iniciar/existente.html", **inicio.resumen(expediente))


@bp.get("/")
def rut():
    return render_template("iniciar/rut.html", rut="")


@bp.post("/buscar")
def buscar():
    ingresado = request.form.get("rut", "")
    try:
        rut = inicio.normalizar_rut(ingresado)
    except inicio.DatosInvalidosError as error:
        flash(error.mensaje, "error")
        return render_template("iniciar/rut.html", rut=ingresado), 422
    postulante = inicio.buscar_postulante(rut)
    if postulante is not None and not postulante.activo:
        flash("El postulante está desactivado. Contacte al Administrador.", "error")
        return render_template("iniciar/rut.html", rut=ingresado), 409
    expediente = inicio.expediente_en(g.proceso, postulante)
    if expediente is not None:
        return _existente(expediente)
    datos = inicio.datos_de(postulante) if postulante else {"rut": rut}
    return _formulario(datos, existe=postulante is not None)


@bp.post("/confirmar")
def confirmar():
    datos = {campo: request.form.get(campo, "") for campo in ("rut", *CAMPOS_EDITABLES)}
    existe = inicio.buscar_postulante(_rut_o_vacio(datos["rut"])) is not None
    ruta_form = request.form.to_dict()
    try:
        estructura = recorridos.estructura_del_proceso(g.proceso.id)
        ruta = inicio.ruta_desde_formulario(ruta_form, estructura)
        expediente = inicio.iniciar_por_staff(datos, ruta, g.usuario)
    except inicio.ExpedienteExistenteError as error:
        return _existente(error.expediente)
    except inicio.DatosInvalidosError as error:
        flash(error.mensaje, "error")
        return _formulario(datos, error.campos, ruta_form, existe)
    except wf.ReglaVioladaError as error:
        if error.codigo_http == 403:
            abort(403)
        flash(error.mensaje, "error")
        return _formulario(datos, {"_": error.mensaje}, ruta_form, existe)
    return redirect(url_for("iniciar.listo", expediente_id=expediente.id))


def _rut_o_vacio(rut):
    try:
        return inicio.normalizar_rut(rut)
    except inicio.DatosInvalidosError:
        return ""


@bp.get("/listo/<int:expediente_id>")
def listo(expediente_id):
    expediente = m.Expediente.get_or_none(
        (m.Expediente.id == expediente_id) & (m.Expediente.proceso == g.proceso.id)
    )
    if expediente is None:
        abort(404)
    return render_template("iniciar/listo.html", **inicio.resumen(expediente))
