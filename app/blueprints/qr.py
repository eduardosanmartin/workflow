"""Formulario público "Inicia aquí" del QR (§10, pantalla 14; RF-10)."""

import time

from flask import Blueprint, redirect, render_template, request, session, url_for

from app.extensions import limiter
from app.security import publico
from app.services import captcha, inicio
from app.services import workflow as wf
from app.services.postulantes import CAMPOS_EDITABLES, CARRERAS

bp = Blueprint("qr", __name__, url_prefix="/qr")

# RF-10: límite por IP. Holgado, porque muchos postulantes pueden compartir la IP del campus
_LIMITE = "30 per minute"
# Tras pasar el CAPTCHA en el paso del RUT, la sesión recuerda ese RUT para el paso siguiente
_VIGENCIA_RUT = 30 * 60
_VOLVER_AL_RUT = "Por seguridad, vuelve a ingresar tu RUT."


@bp.after_request
def _sin_cache(respuesta):
    respuesta.headers["Cache-Control"] = "no-store"
    respuesta.headers["Referrer-Policy"] = "no-referrer"
    return respuesta


def _cerrado():
    return render_template("qr/cerrado.html"), 409 if request.method == "POST" else 200


def _rut(mensaje=None, rut="", codigo=200):
    return render_template("qr/rut.html", mensaje=mensaje, rut=rut, captcha=captcha.clave_sitio()), codigo


def _formulario(consulta, datos=None, errores=None, marcadas=(), codigo=200):
    codigos = [consulta["vista"].get(campo) for campo in CARRERAS]
    codigos = [c for c in codigos if c]
    return render_template(
        "qr/formulario.html", consulta=consulta, datos=datos or {}, errores=errores or {},
        marcadas=[str(c) for c in marcadas],
        carreras=inicio.opciones_de_carrera(),
        nombres_carrera=dict(zip(codigos, inicio.nombres_de_carrera(codigos))),
    ), codigo


def _recordar_rut(rut):
    session["qr_rut"] = {"rut": rut, "hasta": time.time() + _VIGENCIA_RUT}


def _rut_verificado(rut_ingresado) -> bool:
    recordado = session.get("qr_rut") or {}
    try:
        rut = inicio.normalizar_rut(rut_ingresado)
    except inicio.DatosInvalidosError:
        return False
    return recordado.get("rut") == rut and recordado.get("hasta", 0) > time.time()


@bp.get("/")
@publico
def rut():
    if inicio.proceso_activo() is None:
        return _cerrado()
    return _rut()


@bp.post("/rut")
@publico
@limiter.limit(_LIMITE)
def consultar():
    ingresado = request.form.get("rut", "")
    if inicio.proceso_activo() is None:
        return _cerrado()
    if not captcha.verificar(request.form.get(captcha.CAMPO), request.remote_addr):
        return _rut(captcha.MENSAJE, ingresado, 400)
    try:
        consulta = inicio.consultar_qr(ingresado)
    except inicio.DatosInvalidosError as error:
        return _rut(error.mensaje, ingresado, 422)
    except wf.ReglaVioladaError as error:
        if inicio.proceso_activo() is None:
            return _cerrado()
        return _rut(error.mensaje, ingresado, error.codigo_http)
    if consulta["expediente"] is not None:
        inicio.registrar_reenvio_qr(consulta["expediente"])
        return render_template("qr/enlace_enviado.html", correo=consulta["correo_enmascarado"])
    _recordar_rut(consulta["rut"])
    return _formulario(consulta)


@bp.post("/iniciar")
@publico
@limiter.limit(_LIMITE)
def iniciar():
    datos = {campo: request.form.get(campo, "") for campo in ("rut", *CAMPOS_EDITABLES)}
    marcadas = request.form.getlist("consultas")
    if inicio.proceso_activo() is None:
        return _cerrado()
    if not _rut_verificado(datos["rut"]):
        return _rut(_VOLVER_AL_RUT, codigo=400)
    try:
        expediente = inicio.iniciar_por_qr(datos, marcadas)
    except inicio.ExpedienteExistenteError as error:
        inicio.registrar_reenvio_qr(error.expediente)
        correo = inicio.enmascarar_correo_de(error.expediente)
        return render_template("qr/enlace_enviado.html", correo=correo)
    except inicio.DatosInvalidosError as error:
        if "rut" in error.campos:
            return _rut(error.campos["rut"], datos["rut"], 422)
        consulta = inicio.consultar_qr(datos["rut"])
        return _formulario(consulta, datos, error.campos, marcadas, 422)
    except wf.ReglaVioladaError as error:
        if inicio.proceso_activo() is None:
            return _cerrado()
        return _rut(error.mensaje, datos["rut"], error.codigo_http)
    session.pop("qr_rut", None)
    return redirect(url_for("seguimiento.ver", token=expediente.token_seguimiento))
