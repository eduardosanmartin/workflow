"""Pantallas de la bandeja de trabajo (§10: 7 y 7b)."""

import time

import pytest

import seed_carreras
from app import create_app
from app import models as m
from app.config import TestConfig
from app.services import inicio
from app.services import workflow as wf
from tests.test_admin import entrar, texto, usuario
from tests.test_bandeja import iniciar, version
from tests.test_inicio import armar_proceso, datos, fase, ruta_de


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


def expediente_en_becas(app, admin):
    with app.app_context():
        return iniciar(admin, consultas=["BEC"])


# ── Acceso ──

def test_sin_proceso_activo(staff, app, admin):
    with app.app_context():
        armar_proceso(admin, activar=False)
    respuesta = staff.get("/bandeja/")
    assert respuesta.status_code == 200 and "No hay un proceso de matrícula activo" in texto(respuesta)


def test_usuario_sin_fases_en_el_proceso_activo_recibe_403(app, proceso):
    otro = armar_proceso(usuario("otro@unach.cl"), activar=False, codigo="MAT2028")
    cliente = entrar(app.test_client(), usuario("ajeno@unach.cl", rol="USUARIO_FASE"))
    m.FaseResponsable.create(fase=fase(otro, "BEC"), usuario=m.Usuario.get(m.Usuario.email == "ajeno@unach.cl"))
    assert cliente.get("/bandeja/").status_code == 403
    assert 'href="/bandeja/"' not in texto(cliente.get("/"))


def test_menu_y_bandeja_del_responsable(app, admin, proceso):
    expediente_en_becas(app, admin)
    becas = de_fase(app, proceso, "BEC")
    assert 'href="/bandeja/"' in texto(becas.get("/"))
    respuesta = becas.get("/bandeja/")
    html = texto(respuesta)
    assert respuesta.status_code == 200 and "M-0001" in html and "Juan Andrés Pérez Soto" in html
    assert "En espera de atención" in html and "Atender" in html and "Finalizar y derivar" in html
    assert respuesta.headers["Cache-Control"] == "no-store"


def test_responsable_de_otra_fase_no_ve_al_postulante(app, admin, proceso):
    expediente_en_becas(app, admin)
    finanzas = de_fase(app, proceso, "FIN", email="fin@unach.cl")
    assert "Juan Andrés" not in texto(finanzas.get("/bandeja/"))


def test_parcial_para_el_sondeo(app, admin, staff, proceso):
    expediente_en_becas(app, admin)
    respuesta = staff.get("/bandeja/?parcial=1", headers={"X-Sondeo": "1"})
    html = texto(respuesta)
    assert "M-0001" in html and "<html" not in html


def test_el_sondeo_no_cuenta_como_actividad_de_la_sesion(app, staff, proceso):
    hace_diez = time.time() - 600
    with staff.session_transaction() as sesion:
        sesion["ultimo_acceso"] = hace_diez
    staff.get("/bandeja/?parcial=1", headers={"X-Sondeo": "1"})
    with staff.session_transaction() as sesion:
        assert sesion["ultimo_acceso"] == hace_diez
    staff.get("/bandeja/")
    with staff.session_transaction() as sesion:
        assert sesion["ultimo_acceso"] > hace_diez


def test_filtros(app, admin, staff, proceso):
    with app.app_context():
        iniciar(admin, 11111111, ["BEC"], carrera1="ENF")
        iniciar(admin, 22222222, ["BEC"], carrera1="KIN")
    html = texto(staff.get("/bandeja/?carrera=KIN"))
    assert "M-0002" in html and "M-0001" not in html
    html = texto(staff.get(f"/bandeja/?fase={fase(proceso, 'FIN').id}"))
    assert "M-0001" not in html
    assert staff.get("/bandeja/?fecha=no-es-fecha&fase=x").status_code == 200  # se ignoran


# ── Acciones ──

def test_atender_desde_la_bandeja(app, admin, proceso):
    expediente = expediente_en_becas(app, admin)
    becas = de_fase(app, proceso, "BEC")
    respuesta = becas.post(f"/bandeja/{expediente.id}/atender",
                           data={"version": version(expediente), "volver": "/bandeja/?carrera=ENF"})
    assert respuesta.status_code == 302 and respuesta.headers["Location"] == "/bandeja/?carrera=ENF"
    assert ruta_de(expediente)[1] == ("BEC", wf.EN_ATENCION)
    html = texto(becas.get("/bandeja/"))
    assert "Atendiendo a M-0001 en Becas" in html and "En atención" in html


def test_atender_con_version_vieja_avisa(app, admin, staff, proceso):
    expediente = expediente_en_becas(app, admin)
    vieja = version(expediente)
    staff.post(f"/bandeja/{expediente.id}/atender", data={"version": vieja})
    respuesta = staff.post(f"/bandeja/{expediente.id}/atender", data={"version": vieja}, follow_redirects=True)
    assert "Recargue la página" in texto(respuesta)


def test_volver_no_permite_redireccion_externa(app, admin, staff, proceso):
    expediente = expediente_en_becas(app, admin)
    respuesta = staff.post(f"/bandeja/{expediente.id}/atender",
                           data={"version": version(expediente), "volver": "https://malo.example"})
    assert respuesta.headers["Location"] == "/bandeja/"


def test_actuar_sobre_una_fase_ajena_es_403(app, admin, proceso):
    expediente = expediente_en_becas(app, admin)
    finanzas = de_fase(app, proceso, "FIN", email="fin@unach.cl")
    assert finanzas.post(f"/bandeja/{expediente.id}/atender", data={"version": version(expediente)}).status_code == 403
    assert finanzas.get(f"/bandeja/{expediente.id}/finalizar").status_code == 403


def test_pantalla_finalizar_y_derivar(app, admin, staff, proceso):
    expediente = expediente_en_becas(app, admin)
    html = texto(staff.get(f"/bandeja/{expediente.id}/finalizar"))
    assert "Finalizar y derivar" in html and "M-0001" in html
    assert f'name="orden_{fase(proceso, "BEC").id}"' in html
    assert "Confirmo finalizar sin haber presionado «Atender»" in html  # aún en espera
    assert "Derivar ahora" in html and "Pago en efectivo: desde Finanzas" in html


def test_finalizar_y_derivar(app, admin, staff, proceso):
    expediente = expediente_en_becas(app, admin)
    staff.post(f"/bandeja/{expediente.id}/atender", data={"version": version(expediente)})
    respuesta = staff.post(f"/bandeja/{expediente.id}/finalizar", data={
        "version": version(expediente), "accion": "seguir", "resultado": "completado",
        f"orden_{fase(proceso, 'FIN').id}": "1"})
    assert respuesta.status_code == 302
    assert "M-0001 debe dirigirse a: Finanzas" in texto(staff.get(respuesta.headers["Location"]))
    assert ruta_de(expediente)[2] == ("FIN", wf.EN_ESPERA)


def test_finalizar_con_error_vuelve_al_formulario_con_lo_ingresado(app, admin, staff, proceso):
    expediente = expediente_en_becas(app, admin)
    respuesta = staff.post(f"/bandeja/{expediente.id}/finalizar", data={
        "version": version(expediente), "accion": "seguir", "resultado": "observaciones",
        "observacion": "", f"orden_{fase(proceso, 'FIN').id}": "2", "sin_atender": "on"})
    html = texto(respuesta)
    assert respuesta.status_code == 400 and "Debe ingresar una observación" in html
    assert f'name="orden_{fase(proceso, "FIN").id}"' in html and 'value="2"' in html


def test_mnp_ofrece_matricular_y_no_cambiar_la_ruta(app, admin, staff, proceso):
    with app.app_context():
        expediente = iniciar(admin)
    html = texto(staff.get(f"/bandeja/{expediente.id}/finalizar"))
    assert "queda Matriculado" in html and 'name="orden_' not in html


def test_fase_final_del_flujo_alternativo(app, admin, staff, proceso):
    with app.app_context():
        expediente = inicio.iniciar_por_staff(
            datos(), {"flujo_id": m.FlujoAlternativo.get().id, "fase_entrada_id": fase(proceso, "CAJA").id}, admin)
    html = texto(staff.get("/bandeja/"))
    assert "Pago en efectivo" in html and "Cerrar matrícula manualmente" in html
    html = texto(staff.get(f"/bandeja/{expediente.id}/finalizar"))
    assert 'name="detalle_cierre"' in html and "Devolver al flujo principal" in html
    assert "Derivar ahora" not in html


def test_abandono_con_confirmacion(app, admin, staff, proceso):
    expediente = expediente_en_becas(app, admin)
    html = texto(staff.get(f"/bandeja/{expediente.id}/abandono"))
    assert "¿El postulante M-0001 se retiró de la fila?" in html
    respuesta = staff.post(f"/bandeja/{expediente.id}/abandono", data={"version": version(expediente)},
                           follow_redirects=True)
    assert "salió de la fila" in texto(respuesta)
    assert m.Expediente.get_by_id(expediente.id).estado == wf.ABANDONO


def test_expediente_sin_atencion_en_curso(app, admin, staff, proceso):
    with app.app_context():
        expediente = iniciar(admin)
    staff.post(f"/bandeja/{expediente.id}/finalizar",
               data={"version": version(expediente), "resultado": "completado", "sin_atender": "on"})
    respuesta = staff.get(f"/bandeja/{expediente.id}/finalizar", follow_redirects=True)
    assert "ya está matriculado" in texto(respuesta)
    assert staff.get("/bandeja/999999/finalizar").status_code == 404
