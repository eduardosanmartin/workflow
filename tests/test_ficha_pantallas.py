"""Pantallas de búsqueda por RUT y ficha del postulante (§10, pantalla 3)."""

import pytest

import seed_carreras
from app import create_app
from app import models as m
from app.config import TestConfig
from app.services import bandeja
from app.services import workflow as wf
from tests.test_admin import entrar, texto, usuario
from tests.test_bandeja import finalizar, iniciar, orden, version
from tests.test_inicio import armar_proceso, fase, ruta_de


@pytest.fixture
def app(bd):
    aplicacion = create_app(TestConfig)
    with aplicacion.app_context():
        seed_carreras.sembrar()
    return aplicacion


@pytest.fixture
def admin(app):
    return usuario("admin@unach.cl")


@pytest.fixture
def proceso(app, admin):
    with app.app_context():
        return armar_proceso(admin)


@pytest.fixture
def staff(app, admin):
    return entrar(app.test_client(), admin)


def de_fase(app, proceso, *identificadores, email="bec@unach.cl"):
    quien = usuario(email, rol="USUARIO_FASE")
    for identificador in identificadores:
        m.FaseResponsable.create(fase=fase(proceso, identificador), usuario=quien)
    return entrar(app.test_client(), quien)


def expediente(app, admin, consultas=("BEC",)):
    with app.app_context():
        return iniciar(admin, consultas=list(consultas))


def test_menu_y_busqueda_para_todos(app, admin, proceso):
    exp = expediente(app, admin)
    cliente = de_fase(app, proceso, "FIN", email="fin@unach.cl")
    assert 'href="/ficha/"' in texto(cliente.get("/"))
    respuesta = cliente.post("/ficha/buscar", data={"rut": "12.345.678-5"})
    assert respuesta.status_code == 302 and respuesta.headers["Location"] == f"/ficha/{exp.postulante_id}"
    html = texto(cliente.get(respuesta.headers["Location"]))
    assert "Juan Andrés Pérez Soto" in html and "M-0001" in html and "No matriculado aún" in html
    assert "Becas" in html and "Atender" not in html  # Finanzas no actúa sobre Becas


def test_busqueda_sin_resultado_y_rut_invalido(staff, proceso):
    respuesta = staff.post("/ficha/buscar", data={"rut": "11.111.111-1"})
    assert respuesta.status_code == 404 and "No hay un postulante con el RUT 11111111-1" in texto(respuesta)
    respuesta = staff.post("/ficha/buscar", data={"rut": "12.345.678-9"})
    assert respuesta.status_code == 422 and "Dígito verificador inválido" in texto(respuesta)


def test_ficha_sin_cache_y_404(staff, app, admin, proceso):
    exp = expediente(app, admin)
    assert staff.get(f"/ficha/{exp.postulante_id}").headers["Cache-Control"] == "no-store"
    assert staff.get("/ficha/999999").status_code == 404


def test_responsable_actua_desde_la_ficha_y_vuelve_a_ella(app, admin, proceso):
    exp = expediente(app, admin)
    becas = de_fase(app, proceso, "BEC")
    ficha_url = f"/ficha/{exp.postulante_id}"
    html = texto(becas.get(ficha_url))
    assert "Atender" in html and "Finalizar y derivar" in html
    respuesta = becas.post(f"/bandeja/{exp.id}/atender", data={"version": version(exp), "volver": ficha_url})
    assert respuesta.headers["Location"] == ficha_url
    assert "En atención" in texto(becas.get(ficha_url))


def test_reactivar_con_confirmacion(app, admin, staff, proceso):
    exp = expediente(app, admin)
    with app.app_context():
        bandeja.marcar_abandono(exp.id, version(exp), admin)
    html = texto(staff.get(f"/ficha/{exp.postulante_id}"))
    assert "Abandonó" in html and "Reactivar" in html
    html = texto(staff.get(f"/ficha/expediente/{exp.id}/reactivar"))
    assert "¿Reactivar a M-0001?" in html and f'value="{fase(proceso, "BEC").id}" selected' in html
    respuesta = staff.post(f"/ficha/expediente/{exp.id}/reactivar", data={"fase": fase(proceso, "FIN").id})
    assert respuesta.headers["Location"] == f"/ficha/{exp.postulante_id}"
    assert "M-0001 debe dirigirse a: Finanzas" in texto(staff.get(respuesta.headers["Location"]))
    assert ruta_de(exp)[2] == ("FIN", wf.EN_ESPERA)


def test_usuario_de_otra_fase_no_reactiva(app, admin, proceso):
    exp = expediente(app, admin)
    with app.app_context():
        bandeja.marcar_abandono(exp.id, version(exp), admin)
    finanzas = de_fase(app, proceso, "FIN", email="fin@unach.cl")
    assert "Reactivar" not in texto(finanzas.get(f"/ficha/{exp.postulante_id}"))
    assert finanzas.get(f"/ficha/expediente/{exp.id}/reactivar").status_code == 403
    assert finanzas.post(f"/ficha/expediente/{exp.id}/reactivar", data={}).status_code == 403


def test_corregir_ruta(app, admin, staff, proceso):
    exp = expediente(app, admin, ("BEC", "FIN"))
    html = texto(staff.get(f"/ficha/expediente/{exp.id}/corregir"))
    assert "Corregir la ruta" in html and f'name="orden_{fase(proceso, "FIN").id}"' in html and 'value="1"' in html
    respuesta = staff.post(f"/ficha/expediente/{exp.id}/corregir", data={
        "version": version(exp), "motivo": "", **orden(proceso, "CAJA")})
    assert respuesta.status_code == 400 and "motivo" in texto(respuesta)
    respuesta = staff.post(f"/ficha/expediente/{exp.id}/corregir", data={
        "version": version(exp), "motivo": "Error al iniciar", **orden(proceso, "CAJA")})
    assert respuesta.status_code == 302
    assert ruta_de(exp)[2] == ("CAJA", wf.PLANIFICADA)


def test_usuario_de_fase_no_ve_correcciones(app, admin, proceso):
    exp = expediente(app, admin, ("BEC", "FIN"))
    becas = de_fase(app, proceso, "BEC")
    assert "Corregir la ruta" not in texto(becas.get(f"/ficha/{exp.postulante_id}"))
    assert becas.get(f"/ficha/expediente/{exp.id}/corregir").status_code == 403


def test_reabrir(app, admin, staff, proceso):
    exp = expediente(app, admin)
    with app.app_context():
        finalizar(exp, admin, sin_atender="on")
    html = texto(staff.get(f"/ficha/{exp.postulante_id}"))
    assert "Reabrir Becas" in html
    html = texto(staff.get(f"/ficha/expediente/{exp.id}/reabrir"))
    objetivo = m.Atencion.get((m.Atencion.expediente == exp.id) & (m.Atencion.secuencia == 2)).id
    assert f'name="atencion" value="{objetivo}"' in html
    respuesta = staff.post(f"/ficha/expediente/{exp.id}/reabrir", data={"motivo": "Error", "atencion": objetivo})
    assert respuesta.status_code == 302
    assert ruta_de(exp)[1] == ("BEC", wf.EN_ESPERA)
