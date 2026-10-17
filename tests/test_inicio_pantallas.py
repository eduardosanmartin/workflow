"""Pantallas de inicio por staff (§10, 2), formulario QR (14) y seguimiento (15)."""

import re

import pytest

import seed_carreras
from app import create_app
from app import models as m
from app.config import TestConfig
from app.services import configuracion as cfg
from tests.test_admin import entrar, texto, usuario
from tests.test_inicio import armar_proceso, datos, fase, precargar


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


@pytest.fixture
def publico(app):
    return app.test_client()


def confirmar(cliente, **extra):
    formulario = {**datos(), "dudas": "no", **extra}
    return cliente.post("/iniciar/confirmar", data=formulario)


# ── Inicio por staff ──

def test_sin_proceso_activo_no_muestra_el_formulario_ni_permite_iniciar(app, staff, admin):
    with app.app_context():
        armar_proceso(admin, activar=False)
    respuesta = staff.get("/iniciar/")
    assert respuesta.status_code == 200 and "No hay un proceso de matrícula activo" in texto(respuesta)
    assert 'name="rut"' not in texto(respuesta)
    assert confirmar(staff).status_code == 409
    assert m.Expediente.select().count() == 0


def test_usuario_de_fase_sin_iniciado_recibe_403(app, proceso):
    becas = usuario("bec@unach.cl", rol="USUARIO_FASE", puede_registrar_postulantes=True)
    m.FaseResponsable.create(fase=fase(proceso, "BEC"), usuario=becas)
    cliente = entrar(app.test_client(), becas)
    assert cliente.get("/iniciar/").status_code == 403
    assert cliente.post("/iniciar/buscar", data={"rut": "12.345.678-5"}).status_code == 403
    assert confirmar(cliente).status_code == 403
    assert m.Expediente.select().count() == 0
    assert 'href="/iniciar/"' not in texto(cliente.get("/"))


def test_responsable_de_iniciado_ve_el_menu_e_inicia(app, proceso):
    responsable = m.Usuario.get(m.Usuario.email == "ini-MAT2027@unach.cl")
    cliente = entrar(app.test_client(), responsable)
    assert 'href="/iniciar/"' in texto(cliente.get("/"))
    respuesta = confirmar(cliente)
    assert respuesta.status_code == 302


def test_flujo_completo_rut_nuevo(staff, proceso):
    respuesta = staff.post("/iniciar/buscar", data={"rut": "12.345.678-5"})
    html = texto(respuesta)
    assert respuesta.status_code == 200 and "RUT sin registro" in html
    assert 'name="rut" value="12345678-5"' in html and "¿El postulante tiene dudas?" in html
    assert "Pago en efectivo: desde Finanzas" in html

    respuesta = confirmar(staff, dudas="si", **{f"orden_{fase(proceso, 'BEC').id}": "1"})
    assert respuesta.status_code == 302
    listo = staff.get(respuesta.headers["Location"])
    html = texto(listo)
    assert "M-0001" in html and "Debe dirigirse a: <strong>Becas</strong>" in html
    assert listo.headers["Cache-Control"] == "no-store"


def test_rut_existente_muestra_datos_completos_y_pendientes(staff, proceso):
    precargar(telefono=None)
    html = texto(staff.post("/iniciar/buscar", data={"rut": "12345678-5"}))
    assert "Postulante registrado" in html
    assert 'value="Juan Andrés"' in html and 'value="juan.perez@gmail.com"' in html
    assert re.search(r'Teléfono <span class="insignia[^"]*">Pendiente', html)


def test_rut_invalido(staff, proceso):
    respuesta = staff.post("/iniciar/buscar", data={"rut": "12.345.678-9"})
    assert respuesta.status_code == 422 and "Dígito verificador inválido" in texto(respuesta)


def test_sin_responder_dudas_no_se_confirma(staff, proceso):
    respuesta = confirmar(staff, dudas="")
    assert respuesta.status_code == 422 and "Indique si el postulante tiene dudas" in texto(respuesta)
    assert 'value="Juan Andrés"' in texto(respuesta)  # conserva lo ingresado
    assert m.Expediente.select().count() == 0


def test_errores_por_campo(staff, proceso):
    respuesta = confirmar(staff, correo_personal="no-es-correo")
    html = texto(respuesta)
    assert respuesta.status_code == 422 and 'aria-invalid="true"' in html
    assert "El correo personal no tiene un formato válido" in html


def test_expediente_existente_se_muestra_sin_crear_otro(staff, proceso):
    confirmar(staff)
    respuesta = staff.post("/iniciar/buscar", data={"rut": "12.345.678-5"})
    assert "ya tiene su proceso iniciado" in texto(respuesta) and "M-0001" in texto(respuesta)
    assert "ya tiene su proceso iniciado" in texto(confirmar(staff))
    assert m.Expediente.select().count() == 1


def test_listo_de_otro_proceso_es_404(staff, proceso):
    assert staff.get("/iniciar/listo/999999").status_code == 404


# ── Formulario QR ──

def test_qr_sin_proceso_activo(publico, app, admin):
    with app.app_context():
        armar_proceso(admin, activar=False)
    respuesta = publico.get("/qr/")
    assert "El proceso de matrícula no está abierto" in texto(respuesta) and 'name="rut"' not in texto(respuesta)
    assert publico.post("/qr/iniciar", data=datos()).status_code == 409
    assert publico.post("/qr/rut", data={"rut": "12.345.678-5"}).status_code == 409
    assert m.Expediente.select().count() == 0


def test_qr_es_publico(publico, proceso):
    respuesta = publico.get("/qr/")
    assert respuesta.status_code == 200 and "Inicia aquí tu matrícula" in texto(respuesta)


def test_qr_enmascarado_no_envia_los_datos_completos(publico, proceso):
    precargar(telefono=None)
    html = texto(publico.post("/qr/rut", data={"rut": "12.345.678-5"}))
    assert "Ju*** An***" in html and "j***@gmail.com" in html and "Enfermería" in html
    for completo in ("Juan", "Andrés", "Pérez", "juan.perez"):
        assert completo not in html
    assert 'name="telefono"' in html and 'name="nombres"' not in html  # solo pendientes
    assert "Becas" in html and "Finanzas" not in html  # solo temas con mostrar_en_qr


def test_qr_sin_enmascarar_muestra_datos_completos(publico, app, admin):
    with app.app_context():
        armar_proceso(admin, enmascarar_datos_qr="")
    precargar()
    assert "Juan Andrés" in texto(publico.post("/qr/rut", data={"rut": "12.345.678-5"}))


def test_qr_rut_nuevo_redirige_al_seguimiento(publico, proceso):
    html = texto(publico.post("/qr/rut", data={"rut": "12.345.678-5"}))
    assert 'name="nombres"' in html and "Ley 19.628" in html
    respuesta = publico.post("/qr/iniciar", data={**datos(), "consultas": [str(fase(proceso, "BEC").id)]})
    assert respuesta.status_code == 302
    expediente = m.Expediente.get()
    assert respuesta.headers["Location"] == f"/seguimiento/{expediente.token_seguimiento}"

    seguimiento = publico.get(respuesta.headers["Location"])
    html = texto(seguimiento)
    assert "M-0001" in html and "Dirígete a: Iniciado" in html and "No matriculado aún" in html
    assert "Becas" in html
    for dato in ("12345678-5", "juan.perez@gmail.com", "56912345678", "Pérez"):
        assert dato not in html
    assert seguimiento.headers["Referrer-Policy"] == "no-referrer"


def test_qr_errores_vuelven_al_formulario(publico, proceso):
    publico.post("/qr/rut", data={"rut": "12.345.678-5"})
    respuesta = publico.post("/qr/iniciar", data=datos(telefono="123"))
    assert respuesta.status_code == 422 and "entre 8 y 15 dígitos" in texto(respuesta)
    assert m.Expediente.select().count() == 0


def test_qr_con_expediente_no_muestra_el_enlace(publico, staff, proceso):
    publico.post("/qr/rut", data={"rut": "12.345.678-5"})  # aún sin expediente
    confirmar(staff)  # el staff lo inicia mientras el postulante completa el QR
    token = m.Expediente.get().token_seguimiento
    for respuesta in (publico.post("/qr/iniciar", data=datos()),
                      publico.post("/qr/rut", data={"rut": "12.345.678-5"})):
        html = texto(respuesta)
        assert "Te enviamos tu enlace a j***@gmail.com" in html and token not in html
    assert m.Expediente.select().count() == 1
    assert m.EnvioCorreo.select().where(m.EnvioCorreo.tipo == "REENVIO_QR").count() == 2


# ── Seguimiento ──

def test_seguimiento_token_inexistente(publico, proceso):
    assert publico.get("/seguimiento/no-existe").status_code == 404


def test_seguimiento_proceso_finalizado(publico, staff, app, admin, proceso):
    confirmar(staff)
    token = m.Expediente.get().token_seguimiento
    with app.app_context():
        cfg.cerrar_proceso(proceso.id, admin)
    assert "Este proceso ha finalizado" in texto(publico.get(f"/seguimiento/{token}"))
