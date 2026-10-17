"""CAPTCHA del formulario QR con Cloudflare Turnstile (RF-10, protección contra abuso)."""

import time

import pytest

import seed_carreras
from app import create_app
from app import models as m
from app.config import TestConfig
from app.services import captcha
from tests.test_admin import texto, usuario
from tests.test_inicio import armar_proceso, datos


class ConCaptcha(TestConfig):
    TURNSTILE_SITE_KEY = "clave-sitio"
    TURNSTILE_SECRET_KEY = "clave-secreta"


@pytest.fixture
def consultas(monkeypatch):
    """Reemplaza la llamada a Cloudflare; aprueba solo el token "valido"."""
    enviadas = []

    def falsa(datos):
        enviadas.append(datos)
        return {"success": datos["response"] == "valido"}

    monkeypatch.setattr(captcha, "_consultar", falsa)
    return enviadas


def _app(config):
    aplicacion = create_app(config)
    with aplicacion.app_context():
        seed_carreras.sembrar()
        armar_proceso(usuario("admin@unach.cl"))
    return aplicacion


@pytest.fixture
def app(bd):
    return _app(ConCaptcha)


@pytest.fixture
def publico(app):
    return app.test_client()


def test_el_formulario_muestra_el_widget(publico):
    html = texto(publico.get("/qr/"))
    assert 'class="cf-turnstile" data-sitekey="clave-sitio"' in html
    assert "challenges.cloudflare.com/turnstile/v0/api.js" in html


def test_sin_captcha_no_consulta_el_rut(publico, consultas):
    respuesta = publico.post("/qr/rut", data={"rut": "12.345.678-5"})
    assert respuesta.status_code == 400 and "Confirma que no eres un robot" in texto(respuesta)
    assert 'name="nombres"' not in texto(respuesta)
    assert consultas == []  # sin token, ni siquiera se consulta a Cloudflare
    assert m.Auditoria.select().where(m.Auditoria.accion == "CONSULTA_RUT_QR").count() == 0


def test_captcha_rechazado(publico, consultas):
    respuesta = publico.post("/qr/rut", data={"rut": "12.345.678-5", "cf-turnstile-response": "malo"})
    assert respuesta.status_code == 400 and 'name="nombres"' not in texto(respuesta)
    assert consultas[0]["secret"] == "clave-secreta" and consultas[0]["remoteip"] == "127.0.0.1"


def test_captcha_aprobado_permite_iniciar(publico, consultas):
    respuesta = publico.post("/qr/rut", data={"rut": "12.345.678-5", "cf-turnstile-response": "valido"})
    assert respuesta.status_code == 200 and 'name="nombres"' in texto(respuesta)
    assert publico.post("/qr/iniciar", data=datos()).status_code == 302
    assert m.Expediente.select().count() == 1


def test_falla_de_red_no_deja_pasar(publico, monkeypatch):
    def caida(_datos):
        raise OSError("sin conexión")

    monkeypatch.setattr(captcha, "_consultar", caida)
    respuesta = publico.post("/qr/rut", data={"rut": "12.345.678-5", "cf-turnstile-response": "valido"})
    assert respuesta.status_code == 400 and 'name="nombres"' not in texto(respuesta)


def test_token_demasiado_largo_se_rechaza_sin_consultar(publico, consultas):
    publico.post("/qr/rut", data={"rut": "12.345.678-5", "cf-turnstile-response": "x" * 3000})
    assert consultas == []


# ── /qr/iniciar exige haber pasado por el paso del RUT ──

def test_iniciar_directo_sin_pasar_por_el_rut_se_rechaza(publico, consultas):
    respuesta = publico.post("/qr/iniciar", data=datos())
    assert respuesta.status_code == 400 and "vuelve a ingresar tu RUT" in texto(respuesta)
    assert m.Expediente.select().count() == 0


def test_iniciar_con_otro_rut_que_el_verificado_se_rechaza(publico, consultas):
    publico.post("/qr/rut", data={"rut": "11.111.111-1", "cf-turnstile-response": "valido"})
    assert publico.post("/qr/iniciar", data=datos()).status_code == 400  # datos() usa 12.345.678-5
    assert m.Expediente.select().count() == 0


def test_verificacion_vencida(publico, consultas):
    publico.post("/qr/rut", data={"rut": "12.345.678-5", "cf-turnstile-response": "valido"})
    with publico.session_transaction() as sesion:
        sesion["qr_rut"] = {**sesion["qr_rut"], "hasta": time.time() - 1}
    assert publico.post("/qr/iniciar", data=datos()).status_code == 400


def test_sin_claves_el_captcha_queda_desactivado(bd):
    publico = _app(TestConfig).test_client()
    assert "cf-turnstile" not in texto(publico.get("/qr/"))
    assert 'name="nombres"' in texto(publico.post("/qr/rut", data={"rut": "12.345.678-5"}))
