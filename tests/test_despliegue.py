"""Preparación del despliegue: IP real detrás de Caddy (ProxyFix) y correos en segundo plano."""

import threading

import pytest

from app import create_app
from app import models as m
from app.config import TestConfig
from app.services import correo
from tests.test_admin import usuario


class DetrasDeCaddy(TestConfig):
    PROXIES_CONFIABLES = 1


def _ip_del_ultimo_login(app, cabeceras):
    with app.app_context():
        usuario("ip@unach.cl")
    cliente = app.test_client()
    cliente.post("/login", data={"email": "ip@unach.cl", "password": "una-clave-incorrecta"},
                 headers=cabeceras, environ_base={"REMOTE_ADDR": "172.18.0.5"})
    return m.Auditoria.select().where(m.Auditoria.accion == "LOGIN_FALLIDO").order_by(m.Auditoria.id.desc()).get()


def test_detras_de_caddy_registra_la_ip_real(bd):
    app = create_app(DetrasDeCaddy)
    registro = _ip_del_ultimo_login(app, {"X-Forwarded-For": "200.1.2.3", "X-Forwarded-Proto": "https"})
    assert registro.ip == "200.1.2.3"


def test_sin_proxy_configurado_no_se_cree_la_cabecera(bd):
    app = create_app(TestConfig)
    registro = _ip_del_ultimo_login(app, {"X-Forwarded-For": "200.1.2.3"})
    assert registro.ip == "172.18.0.5"  # un cliente no puede falsificar su IP


@pytest.fixture
def app_en_segundo_plano(bd):
    class EnSegundoPlano(TestConfig):
        CORREO_EN_SEGUNDO_PLANO = True
    return create_app(EnSegundoPlano)


def test_correos_automaticos_en_otro_hilo(app_en_segundo_plano):
    listo, hilos = threading.Event(), []

    def envio_lento(valor):
        hilos.append((threading.current_thread().name, valor))
        listo.set()

    with app_en_segundo_plano.app_context():
        assert correo._en_segundo_plano(envio_lento, 7) is None  # no espera el resultado
    assert listo.wait(5)
    nombre, valor = hilos[0]
    assert nombre.startswith("correo") and nombre != threading.current_thread().name and valor == 7


def test_un_error_en_segundo_plano_queda_en_el_log_sin_romper_nada(app_en_segundo_plano, caplog):
    listo = threading.Event()

    def falla():
        listo.set()
        raise RuntimeError("SMTP caído")

    with app_en_segundo_plano.app_context():
        correo._en_segundo_plano(falla)
    assert listo.wait(5)
    correo._ejecutor_compartido().submit(lambda: None).result(5)
    for _ in range(50):
        if "Falló un envío de correo en segundo plano" in caplog.text:
            break
        threading.Event().wait(0.1)
    assert "Falló un envío de correo en segundo plano" in caplog.text


def test_en_pruebas_se_envia_de_inmediato(bd):
    app = create_app(TestConfig)
    with app.app_context():
        assert correo._en_segundo_plano(lambda: "hecho") == "hecho"
