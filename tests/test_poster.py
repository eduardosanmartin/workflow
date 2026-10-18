"""Póster imprimible del QR "Inicia aquí" (§10, pantalla 16)."""

import xml.etree.ElementTree as ET

from tests.test_admin import entrar, texto, usuario
from tests.test_inicio_pantallas import admin, app  # noqa: F401


def test_poster_con_el_enlace_de_app_base_url(app, admin):
    app.config["APP_BASE_URL"] = "https://matricula.unach.cl"
    staff = entrar(app.test_client(), admin)
    assert 'href="/admin/poster-qr"' in texto(staff.get("/admin/procesos"))
    html = texto(staff.get("/admin/poster-qr"))
    assert "Inicia aquí" in html and "https://matricula.unach.cl/qr/" in html and "<svg" in html
    assert "localhost" not in html and "no usa https" not in html


def test_advierte_si_el_enlace_no_es_publico(app, admin):
    staff = entrar(app.test_client(), admin)
    app.config["APP_BASE_URL"] = "http://localhost:5001"
    assert "configure APP_BASE_URL" in texto(staff.get("/admin/poster-qr"))
    app.config["APP_BASE_URL"] = "http://matricula.unach.cl"
    assert "no usa https" in texto(staff.get("/admin/poster-qr"))


def test_descargar_el_qr_en_svg(app, admin):
    app.config["APP_BASE_URL"] = "https://matricula.unach.cl"
    respuesta = entrar(app.test_client(), admin).get("/admin/poster-qr/qr.svg")
    assert respuesta.mimetype == "image/svg+xml" and "attachment" in respuesta.headers["Content-Disposition"]
    raiz = ET.fromstring(respuesta.data)
    assert raiz.tag.endswith("svg") and "viewBox" in raiz.attrib and "width" not in raiz.attrib


def test_solo_el_administrador(app):
    gestor = entrar(app.test_client(), usuario("g@unach.cl", rol="GESTOR"))
    assert gestor.get("/admin/poster-qr").status_code == 403
