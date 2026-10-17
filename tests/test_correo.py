"""Correos al postulante (RF-21): inicio, matrícula completada y reenvíos manual y por QR."""

import smtplib
from datetime import timedelta

import pytest
from flask import current_app

from app import models as m
from app.services import correo, ficha, inicio
from app.services import configuracion as cfg
from app.services import workflow as wf
from app.services.workflow import ReglaVioladaError
from tests.test_bandeja import T0, finalizar, iniciar, orden
from tests.test_inicio import admin, armar_proceso, auditorias, ctx, datos, fase  # noqa: F401


@pytest.fixture
def proceso(admin):
    return armar_proceso(admin)


@pytest.fixture
def smtp(monkeypatch):
    """SMTP configurado: captura los mensajes en vez de enviarlos."""
    enviados = []
    monkeypatch.setitem(current_app.config, "SMTP_HOST", "smtp.ejemplo.cl")
    monkeypatch.setattr(correo, "_enviar_smtp", lambda mensaje: enviados.append(mensaje))
    return enviados


def envios(expediente, tipo=None):
    consulta = m.EnvioCorreo.select().where(m.EnvioCorreo.expediente == expediente.id).order_by(m.EnvioCorreo.id)
    if tipo:
        consulta = consulta.where(m.EnvioCorreo.tipo == tipo)
    return list(consulta)


def cuerpos(mensaje):
    texto = mensaje.get_body(("plain",)).get_content()
    html = mensaje.get_body(("html",)).get_content()
    return texto, html


# ── Correo de inicio ──

def test_sin_smtp_el_expediente_se_crea_y_el_envio_queda_con_error(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    [envio] = envios(expediente)
    assert envio.tipo == "INICIO" and envio.estado == "ERROR" and envio.detalle_error == "SMTP no configurado"
    assert envio.destinatario == "juan.perez@gmail.com" and envio.enviado_por_id is None
    registro = auditorias("ENVIAR_CORREO")[0]
    assert registro.actor == "SISTEMA" and registro.valor_nuevo["tipo"] == "INICIO"


def test_correo_de_inicio_con_resumen_y_enlace(admin, proceso, smtp):
    expediente = iniciar(admin, consultas=["BEC"])
    assert envios(expediente)[0].estado == "ENVIADO"
    [mensaje] = smtp
    assert mensaje["To"] == "juan.perez@gmail.com"
    assert mensaje["From"] == "Matrícula UNACH <matricula-noreply@unach.cl>"
    assert "M-0001" in mensaje["Subject"] and "Matrícula 2027" in mensaje["Subject"]
    enlace = f"http://localhost:5001/seguimiento/{expediente.token_seguimiento}"
    for cuerpo in cuerpos(mensaje):
        assert enlace in cuerpo and "M-0001" in cuerpo and "Juan" in cuerpo
        assert "Dirígete a: Becas" in cuerpo and "Enfermería" in cuerpo and "MNP" in cuerpo
        assert "personal" in cuerpo and "no respondas" in cuerpo.lower()
        assert "12345678" not in cuerpo and "Pérez" not in cuerpo  # sin RUT ni apellidos


def test_correo_de_inicio_por_qr(admin, proceso, smtp):
    expediente = inicio.iniciar_por_qr(datos(), [], ahora=T0)
    assert [e.tipo for e in envios(expediente)] == ["INICIO"]
    assert envios(expediente)[0].estado == "ENVIADO"


def test_falla_del_smtp_queda_registrada(admin, proceso, monkeypatch):
    monkeypatch.setitem(current_app.config, "SMTP_HOST", "smtp.ejemplo.cl")

    def falla(_mensaje):
        raise smtplib.SMTPAuthenticationError(535, b"Authentication failed")
    monkeypatch.setattr(correo, "_enviar_smtp", falla)
    expediente = iniciar(admin)
    [envio] = envios(expediente)
    assert envio.estado == "ERROR" and "535" in envio.detalle_error
    assert m.Expediente.select().count() == 1


# ── Correo de matrícula completada ──

def test_correo_de_matricula_completada_una_sola_vez(admin, proceso, smtp):
    expediente = iniciar(admin)
    finalizar(expediente, admin, sin_atender="on")  # MNP completado
    [envio] = envios(expediente, "MATRICULADO")
    assert envio.estado == "ENVIADO" and envio.enviado_por_id is None
    texto, html = cuerpos(smtp[-1])
    assert "Matriculado" in smtp[-1]["Subject"]
    for cuerpo in (texto, html):
        assert "Ya estás matriculado" in cuerpo and "✓ MNP" in cuerpo.replace("&#10003;", "✓")
        assert "Dirígete" not in cuerpo

    # Se reabre MNP y se vuelve a completar: no se reenvía automáticamente
    atencion = ficha.ver(expediente.postulante_id, admin)["expedientes"][0]["a_reabrir"]["id"]
    ficha.reabrir(expediente.id, {"motivo": "Faltó firmar", "atencion": str(atencion)}, admin)
    finalizar(expediente, admin, sin_atender="on")
    assert len(envios(expediente, "MATRICULADO")) == 1


def test_no_se_envia_mientras_no_quede_matriculado(admin, proceso, smtp):
    expediente = iniciar(admin, consultas=["BEC"])
    finalizar(expediente, admin, sin_atender="on", **orden(proceso, "FIN"))
    assert [e.tipo for e in envios(expediente)] == ["INICIO"]


def test_correo_de_matricula_por_cierre_manual(admin, proceso, smtp):
    expediente = iniciar(admin, consultas=["BEC"])
    entrada = f"{m.FlujoAlternativo.get().id}:{fase(proceso, 'CAJA').id}"
    finalizar(expediente, admin, accion="flujo", entrada=entrada, sin_atender="on")
    finalizar(expediente, admin, sin_atender="on", detalle_cierre="Boleta 123", confirmar_cierre="on")
    assert len(envios(expediente, "MATRICULADO")) == 1
    texto, _ = cuerpos(smtp[-1])
    assert "Boleta 123" not in texto  # el detalle del cierre es interno


# ── Reenvíos ──

def test_reenvio_manual_desde_la_ficha(admin, proceso, smtp):
    expediente = iniciar(admin)
    assert correo.reenviar(expediente.id, admin, ahora=T0) == "Enviamos el enlace a j***@gmail.com"
    envio = envios(expediente, "REENVIO")[0]
    assert envio.estado == "ENVIADO" and envio.enviado_por_id == admin.id
    registro = auditorias("ENVIAR_CORREO")[-1]
    assert registro.actor == "USUARIO" and registro.valor_nuevo["tipo"] == "REENVIO"


def test_reenvio_manual_informa_la_falla(admin, proceso):
    expediente = iniciar(admin)
    with pytest.raises(ReglaVioladaError, match="SMTP no configurado"):
        correo.reenviar(expediente.id, admin, ahora=T0)
    assert envios(expediente, "REENVIO")[0].estado == "ERROR"


def test_limite_de_tres_reenvios_por_hora_entre_manual_y_qr(admin, proceso, smtp):
    expediente = iniciar(admin)
    correo.reenviar(expediente.id, admin, ahora=T0)
    correo.reenviar_qr(expediente, ahora=T0 + timedelta(minutes=10))
    correo.reenviar(expediente.id, admin, ahora=T0 + timedelta(minutes=20))
    with pytest.raises(ReglaVioladaError) as error:
        correo.reenviar(expediente.id, admin, ahora=T0 + timedelta(minutes=30))
    assert error.value.codigo_http == 429 and "3 reenvíos por hora" in error.value.mensaje

    # El QR no revela el límite: no envía y deja auditado el intento
    correo.reenviar_qr(expediente, ahora=T0 + timedelta(minutes=40))
    assert len(envios(expediente, "REENVIO_QR")) == 1
    assert len(auditorias("REENVIO_QR_LIMITADO")) == 1

    correo.reenviar(expediente.id, admin, ahora=T0 + timedelta(minutes=61))  # salió de la ventana
    assert len(envios(expediente, "REENVIO")) == 3


def test_no_se_reenvia_con_el_proceso_cerrado(admin, proceso, smtp):
    expediente = iniciar(admin)
    cfg.cerrar_proceso(proceso.id, admin)
    with pytest.raises(ReglaVioladaError) as error:
        correo.reenviar(expediente.id, admin)
    assert error.value.codigo_http == 409


def test_reenvio_de_matriculado_lleva_el_resumen_final(admin, proceso, smtp):
    expediente = iniciar(admin)
    finalizar(expediente, admin, sin_atender="on")
    correo.reenviar(expediente.id, admin, ahora=T0)
    texto, _ = cuerpos(smtp[-1])
    assert "Ya estás matriculado" in texto


def test_historial_de_correos_en_la_ficha(admin, proceso, smtp):
    expediente = iniciar(admin)
    correo.reenviar(expediente.id, admin, ahora=T0 + timedelta(minutes=5))
    exp = ficha.ver(expediente.postulante_id, admin)["expedientes"][0]
    assert [(c["tipo"], c["estado"], c["enviado_por"]) for c in exp["correos"]] == [
        ("Reenvío manual", "Enviado", "Admin"), ("Inicio", "Enviado", "Sistema")]
    assert exp["puede_reenviar"]


def test_ficha_muestra_el_error_del_envio(admin, proceso):
    expediente = iniciar(admin)
    [fila] = ficha.ver(expediente.postulante_id, admin)["expedientes"][0]["correos"]
    assert fila["estado"] == "Error" and fila["detalle"] == "SMTP no configurado"
