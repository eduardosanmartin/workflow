"""Pantalla de reportería (§10, pantalla 11): permisos, filtros, cada reporte y descargas."""

import io
from datetime import timedelta

import openpyxl
import pytest

from app import models as m
from app.services import bandeja
from app.services import reportes as r
from tests.test_admin import entrar, texto, usuario
from tests.test_bandeja import T0, finalizar, iniciar, version
from tests.test_inicio import armar_proceso, auditorias
from tests.test_inicio_pantallas import admin, app  # noqa: F401


@pytest.fixture
def staff(app, admin):
    return entrar(app.test_client(), admin)


@pytest.fixture
def proceso(app, admin):
    """M-0001 directa y matriculado; M-0002 en espera en Becas."""
    with app.app_context():
        proceso = armar_proceso(admin)
        e1 = iniciar(admin, 11111111, ahora=T0)
        bandeja.atender(e1.id, version(e1), admin, ahora=T0 + timedelta(minutes=2))
        finalizar(e1, admin, ahora=T0 + timedelta(minutes=10))
        iniciar(admin, 22222222, ["BEC"], ahora=T0 + timedelta(minutes=1))
    return proceso


def test_menu_y_permisos(app, staff, proceso):
    assert 'href="/reportes/"' in texto(staff.get("/"))
    gestor = entrar(app.test_client(), usuario("g@unach.cl", rol="GESTOR"))
    assert gestor.get("/reportes/").status_code == 200
    fase = entrar(app.test_client(), usuario("f@unach.cl", rol="USUARIO_FASE"))
    assert 'href="/reportes/"' not in texto(fase.get("/"))
    assert fase.get("/reportes/").status_code == 403
    assert fase.get("/reportes/estado").status_code == 403
    assert fase.get("/reportes/estado/exportar?formato=csv").status_code == 403


def test_sin_procesos(staff):
    respuesta = staff.get("/reportes/")
    assert respuesta.status_code == 200 and "Aún no hay procesos" in texto(respuesta)


def test_indice_con_fecha_de_eliminacion(staff, proceso):
    respuesta = staff.get("/reportes/")
    html = texto(respuesta)
    assert respuesta.headers["Cache-Control"] == "no-store"
    assert proceso.fecha_eliminacion.strftime("%d-%m-%Y") in html
    for reporte in r.REPORTES.values():
        assert f"/reportes/{reporte.slug}?proceso={proceso.id}" in html


def test_aviso_previo_a_la_eliminacion(staff, proceso):
    hoy = m.ahora().date()
    m.Proceso.update(fecha_inicio=hoy - timedelta(days=90), fecha_fin=hoy - timedelta(days=30),
                     fecha_eliminacion=hoy + timedelta(days=10)).where(m.Proceso.id == proceso.id).execute()
    assert "Exporte los reportes que necesite conservar" in texto(staff.get("/reportes/"))
    m.Proceso.update(fecha_eliminacion=hoy + timedelta(days=60)).where(m.Proceso.id == proceso.id).execute()
    assert "Exporte los reportes" not in texto(staff.get("/reportes/"))


def test_cada_reporte_se_muestra(staff, proceso):
    for slug, reporte in r.REPORTES.items():
        respuesta = staff.get(f"/reportes/{slug}")
        assert respuesta.status_code == 200, slug
        assert reporte.titulo in texto(respuesta)
    assert staff.get("/reportes/no-existe").status_code == 404


def test_estado_con_filtros_y_enlace_a_la_ficha(staff, proceso):
    html = texto(staff.get(f"/reportes/estado?proceso={proceso.id}&ruta=DIRECTA"))
    assert "M-0001" in html and "M-0002" not in html and "Quitar filtros" in html
    postulante = m.Expediente.get(m.Expediente.numero_atencion == 1).postulante_id
    assert f'href="/ficha/{postulante}"' in html
    html = texto(staff.get("/reportes/estado?desde=2027-02-01&hasta=2027-01-01"))
    assert "posterior" in html


def test_espera_se_actualiza_sola(staff, proceso):
    html = texto(staff.get("/reportes/espera"))
    assert 'http-equiv="refresh"' in html and "M-0002" in html


def test_descargas(staff, proceso):
    respuesta = staff.get(f"/reportes/estado/exportar?formato=xlsx&proceso={proceso.id}&ruta=DIRECTA")
    assert respuesta.status_code == 200 and "attachment" in respuesta.headers["Content-Disposition"]
    libro = openpyxl.load_workbook(io.BytesIO(respuesta.data))
    assert [f[0] for f in libro["Expedientes"].iter_rows(values_only=True)] == ["Número", "M-0001"]
    respuesta = staff.get("/reportes/abandonos/exportar?formato=csv")
    assert respuesta.mimetype == "text/csv" and "Abandonos" in respuesta.data.decode("utf-8-sig")
    assert staff.get("/reportes/estado/exportar?formato=pdf").status_code == 404
    assert len(auditorias("EXPORTAR_REPORTE")) == 2
    # Los enlaces de descarga conservan los filtros
    html = texto(staff.get(f"/reportes/estado?proceso={proceso.id}&ruta=DIRECTA"))
    assert f"/reportes/estado/exportar?formato=xlsx&amp;proceso={proceso.id}&amp;ruta=DIRECTA" in html
