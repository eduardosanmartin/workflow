"""Carga manual (§10, pantalla 4), carga masiva (pantalla 5) y edición de postulantes (pantalla 6).

Ninguna de estas pantallas inicia procesos, crea expedientes ni envía correos (§5.2).
"""

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from app import models as m
from app.services import carga_masiva as cm
from app.services import inicio, registro
from app.services import workflow as wf
from app.services.inicio import DatosInvalidosError, datos_de, opciones_de_carrera
from app.services.postulantes import CAMPOS_EDITABLES, CARRERAS

bp = Blueprint("postulantes", __name__, url_prefix="/postulantes")


@bp.after_request
def _sin_cache(respuesta):
    respuesta.headers["Cache-Control"] = "no-store"  # datos personales de postulantes
    return respuesta


def _exigir(permitido):
    if not permitido:
        abort(403)


def _formulario_datos():
    return {campo: request.form.get(campo, "") for campo in ("rut", *CAMPOS_EDITABLES)}


# ── Carga manual (RF-07) ──

def _nuevo(datos=None, errores=None, existente=None, codigo=200):
    datos = datos or {}
    return render_template("postulantes/nuevo.html", datos=datos, errores=errores or {}, existente=existente,
                           carreras=opciones_de_carrera()), codigo


@bp.get("/nuevo")
def nuevo():
    _exigir(registro.puede_registrar(g.usuario))
    return _nuevo()


@bp.post("/nuevo")
def nuevo_post():
    _exigir(registro.puede_registrar(g.usuario))
    datos = _formulario_datos()
    try:
        postulante = registro.registrar(datos, g.usuario)
    except registro.PostulanteExistenteError as error:
        return _nuevo(datos, existente=error.postulante, codigo=409)
    except DatosInvalidosError as error:
        flash(error.mensaje, "error")
        return _nuevo(datos, error.campos, codigo=422)
    flash(f"Se registró a {postulante.nombres} {postulante.apellidos} ({postulante.rut}). "
          "Su proceso se inicia cuando llegue a matrícula.", "exito")
    return redirect(url_for("ficha.ver", postulante_id=postulante.id))


# ── Edición (Administrador y Gestor) ──

def _postulante(postulante_id):
    postulante = m.Postulante.get_or_none(m.Postulante.id == postulante_id)
    if postulante is None:
        abort(404)
    return postulante


def _editar(postulante, datos, errores=None, codigo=200):
    actuales = datos_de(postulante)
    return render_template("postulantes/editar.html", postulante=postulante, datos=datos, errores=errores or {},
                           carreras=opciones_de_carrera([actuales[c] for c in CARRERAS])), codigo


@bp.get("/<int:postulante_id>/editar")
def editar(postulante_id):
    _exigir(registro.puede_editar(g.usuario))
    postulante = _postulante(postulante_id)
    return _editar(postulante, datos_de(postulante))


@bp.post("/<int:postulante_id>/editar")
def editar_post(postulante_id):
    _exigir(registro.puede_editar(g.usuario))
    postulante = _postulante(postulante_id)
    datos = {**_formulario_datos(), "rut": postulante.rut}
    try:
        cambios = registro.editar(postulante_id, datos, g.usuario)
    except DatosInvalidosError as error:
        flash(error.mensaje, "error")
        return _editar(postulante, datos, error.campos, 422)
    flash("Datos actualizados." if cambios else "No hubo cambios.", "exito")
    return redirect(url_for("ficha.ver", postulante_id=postulante_id))


@bp.post("/<int:postulante_id>/estado")
def estado(postulante_id):
    _exigir(registro.puede_editar(g.usuario))
    postulante = _postulante(postulante_id)
    activo = request.form.get("activo") == "1"
    registro.cambiar_estado(postulante.id, activo, g.usuario)
    flash("Postulante activado." if activo else "Postulante desactivado: no podrá iniciar procesos.", "exito")
    return redirect(url_for("ficha.ver", postulante_id=postulante.id))


# ── Carga masiva (RF-08) ──

def _error_carga(error):
    if error.codigo_http in (403, 404):
        abort(error.codigo_http)
    flash(error.mensaje, "error")
    return redirect(url_for("postulantes.carga"))


@bp.get("/carga")
def carga():
    _exigir(registro.puede_editar(g.usuario))
    return render_template("postulantes/carga.html", proceso=inicio.proceso_activo())


@bp.post("/carga")
def carga_post():
    _exigir(registro.puede_editar(g.usuario))
    archivo = request.files.get("archivo")
    if archivo is None or not archivo.filename:
        flash("Elija un archivo .csv o .xlsx", "error")
        return render_template("postulantes/carga.html", proceso=inicio.proceso_activo()), 422
    try:
        token = cm.analizar_archivo(archivo.read(), archivo.filename, g.usuario)
    except wf.ReglaVioladaError as error:
        if error.codigo_http == 403:
            abort(403)
        flash(error.mensaje, "error")
        return render_template("postulantes/carga.html", proceso=inicio.proceso_activo()), error.codigo_http
    return redirect(url_for("postulantes.previa", token=token))


@bp.get("/carga/<token>")
def previa(token):
    _exigir(registro.puede_editar(g.usuario))
    try:
        analisis = cm.vista_previa(token, g.usuario)
    except wf.ReglaVioladaError as error:
        return _error_carga(error)
    return render_template("postulantes/previa.html", POLITICAS=cm.POLITICAS, ETIQUETAS=cm.ETIQUETAS,
                           mostrar=200, **analisis)


@bp.post("/carga/<token>/importar")
def importar(token):
    _exigir(registro.puede_editar(g.usuario))
    try:
        cm.importar(token, request.form.get("politica", ""), g.usuario)
    except wf.ReglaVioladaError as error:
        if error.codigo_http == 422:
            flash(error.mensaje, "error")
            return redirect(url_for("postulantes.previa", token=token))
        return _error_carga(error)
    return redirect(url_for("postulantes.resultado", token=token))


@bp.get("/carga/<token>/resultado")
def resultado(token):
    _exigir(registro.puede_editar(g.usuario))
    try:
        datos = cm.resultado(token, g.usuario)
    except wf.ReglaVioladaError as error:
        return _error_carga(error)
    return render_template("postulantes/resultado.html", POLITICAS=cm.POLITICAS, **datos)


@bp.get("/carga/<token>/errores.csv")
def errores_csv(token):
    _exigir(registro.puede_editar(g.usuario))
    try:
        contenido = cm.errores_csv(token, g.usuario)
    except wf.ReglaVioladaError as error:
        return _error_carga(error)
    return Response(contenido, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=errores_carga.csv"})


@bp.get("/carga/plantilla.<formato>")
def plantilla(formato):
    _exigir(registro.puede_editar(g.usuario))
    if formato == "csv":
        return Response(cm.plantilla_csv(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=plantilla_postulantes.csv"})
    if formato == "xlsx":
        return Response(cm.plantilla_xlsx(),
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": "attachment; filename=plantilla_postulantes.xlsx"})
    abort(404)
