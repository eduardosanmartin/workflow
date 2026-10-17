"""Pantallas de configuración del Administrador (§10: 8, 8b, 9 y 10)."""

import re
import time

import pytest

from app import create_app
from app import models as m
from app.config import TestConfig
from app.security import verificar_password
from app.services import configuracion as cfg


@pytest.fixture
def app(bd):
    return create_app(TestConfig)


def usuario(email, rol="ADMIN", **extra):
    return m.Usuario.create(email=email, nombre=email.split("@")[0].title(), rol=rol,
                            must_change_password=False, **extra)


def entrar(cliente, quien):
    with cliente.session_transaction() as sesion:
        sesion.update(usuario_id=quien.id, inicio=time.time(), ultimo_acceso=time.time())
    return cliente


@pytest.fixture
def admin(app):
    return usuario("admin@unach.cl")


@pytest.fixture
def cliente(app, admin):
    return entrar(app.test_client(), admin)


def texto(respuesta):
    return respuesta.get_data(as_text=True)


DATOS_PROCESO = {
    "codigo": "MAT2027", "nombre": "Matrícula 2027", "descripcion": "",
    "fecha_inicio": "2027-01-10", "fecha_fin": "2027-03-31", "prefijo_atencion": "M",
    "enmascarar_datos_qr": "on",
}


def crear_proceso(cliente):
    respuesta = cliente.post("/admin/procesos/nuevo", data=DATOS_PROCESO)
    assert respuesta.status_code == 302, texto(respuesta)
    return m.Proceso.get(m.Proceso.codigo == "MAT2027")


def fase(proceso, identificador):
    return m.Fase.get((m.Fase.proceso == proceso.id) & (m.Fase.identificador == identificador))


# ── Acceso ──

@pytest.mark.parametrize("rol", ["GESTOR", "USUARIO_FASE"])
def test_solo_el_administrador_entra(app, rol):
    cliente = entrar(app.test_client(), usuario("otro@unach.cl", rol=rol))
    for ruta in ("/admin/procesos", "/admin/usuarios", "/admin/procesos/nuevo"):
        assert cliente.get(ruta).status_code == 403
    assert cliente.post("/admin/procesos/nuevo", data=DATOS_PROCESO).status_code == 403
    assert m.Proceso.select().count() == 0


def test_sin_sesion_redirige_al_login(app):
    respuesta = app.test_client().get("/admin/usuarios")
    assert respuesta.status_code == 302 and respuesta.headers["Location"].startswith("/login")


def test_menu_de_administracion_solo_para_admin(app, cliente):
    assert 'href="/admin/procesos"' in texto(cliente.get("/"))
    gestor = entrar(app.test_client(), usuario("g@unach.cl", rol="GESTOR"))
    assert 'href="/admin/procesos"' not in texto(gestor.get("/"))


def test_paginas_de_admin_no_se_guardan_en_cache(cliente):
    assert cliente.get("/admin/usuarios").headers["Cache-Control"] == "no-store"


def test_404_para_entidades_inexistentes(cliente):
    for ruta in ("/admin/procesos/999", "/admin/fases/999", "/admin/flujos/999", "/admin/usuarios/999"):
        assert cliente.get(ruta).status_code == 404
    assert cliente.post("/admin/procesos/999/activar").status_code == 404


# ── Procesos y fases ──

def test_crear_proceso_y_ver_su_detalle(cliente):
    assert cliente.get("/admin/procesos/nuevo").status_code == 200
    proceso = crear_proceso(cliente)
    detalle = texto(cliente.get(f"/admin/procesos/{proceso.id}"))
    assert "Matrícula 2027" in detalle and "Borrador" in detalle
    assert "Iniciado" in detalle and "MNP" in detalle
    assert "Para activar el proceso falta" in detalle and "Sin responsables" in detalle
    assert "MAT2027" in texto(cliente.get("/admin/procesos"))


def test_error_de_validacion_vuelve_al_formulario_con_los_datos(cliente):
    respuesta = cliente.post("/admin/procesos/nuevo", data={**DATOS_PROCESO, "fecha_fin": "2026-01-01"})
    assert respuesta.status_code == 200
    html = texto(respuesta)
    assert "no puede ser anterior a la de inicio" in html
    assert 'value="Matrícula 2027"' in html


def test_editar_proceso_desmarcando_casilla(cliente):
    proceso = crear_proceso(cliente)
    datos = {k: v for k, v in DATOS_PROCESO.items() if k != "enmascarar_datos_qr"}
    respuesta = cliente.post(f"/admin/procesos/{proceso.id}/editar", data=datos)
    assert respuesta.status_code == 302
    assert m.Proceso.get_by_id(proceso.id).enmascarar_datos_qr is False


def test_agregar_editar_reordenar_y_eliminar_fases(cliente):
    proceso = crear_proceso(cliente)
    for ident, nombre in (("BEC", "Becas"), ("FIN", "Finanzas")):
        assert cliente.post(f"/admin/procesos/{proceso.id}/fases/nueva",
                            data={"identificador": ident, "nombre": nombre, "mostrar_en_qr": "on"}).status_code == 302
    becas, finanzas = fase(proceso, "BEC"), fase(proceso, "FIN")
    cliente.post(f"/admin/fases/{finanzas.id}/mover", data={"direccion": "subir"})
    orden = [f.identificador for f in m.Fase.select().where(m.Fase.proceso == proceso.id).order_by(m.Fase.orden)]
    assert orden == ["INI", "FIN", "BEC", "MNP"]
    cliente.post(f"/admin/fases/{becas.id}", data={"identificador": "BEC", "nombre": "Becas y créditos"})
    becas = m.Fase.get_by_id(becas.id)
    assert becas.nombre == "Becas y créditos" and not becas.mostrar_en_qr
    cliente.post(f"/admin/fases/{becas.id}/eliminar")
    assert m.Fase.get_or_none(m.Fase.id == becas.id) is None


def test_fase_de_sistema_no_se_elimina_y_muestra_el_error(cliente):
    proceso = crear_proceso(cliente)
    respuesta = cliente.post(f"/admin/fases/{fase(proceso, 'INI').id}/eliminar", follow_redirects=True)
    assert "no se pueden eliminar" in texto(respuesta)


def test_asignar_responsables_desde_la_fase(cliente, admin):
    proceso = crear_proceso(cliente)
    ini = fase(proceso, "INI")
    html = texto(cliente.get(f"/admin/fases/{ini.id}"))
    assert "Responsables" in html and admin.email in html
    cliente.post(f"/admin/fases/{ini.id}/responsables", data={"usuarios": [str(admin.id)]})
    assert cfg.responsables_de(ini.id) == {admin.id}


def test_flujo_alternativo_con_secuencia_por_posiciones(cliente):
    proceso = crear_proceso(cliente)
    for ident, nombre in (("FIN", "Finanzas"), ("CAJA", "Caja"), ("FIRMA", "Firma de contrato")):
        cliente.post(f"/admin/procesos/{proceso.id}/fases/nueva", data={"identificador": ident, "nombre": nombre})
    fin, caja, firma = fase(proceso, "FIN"), fase(proceso, "CAJA"), fase(proceso, "FIRMA")
    assert cliente.get(f"/admin/procesos/{proceso.id}/flujos/nuevo").status_code == 200
    respuesta = cliente.post(f"/admin/procesos/{proceso.id}/flujos/nuevo", data={
        "codigo": "EFE", "nombre": "Pago en efectivo", "activo": "on",
        f"posicion_{firma.id}": "3", f"posicion_{caja.id}": "2", f"posicion_{fin.id}": "1",
    })
    assert respuesta.status_code == 302, texto(respuesta)
    flujo = m.FlujoAlternativo.get(m.FlujoAlternativo.codigo == "EFE")
    assert cfg.fases_del_flujo(flujo.id) == [fin.id, caja.id, firma.id]
    assert "Finanzas → Caja → Firma de contrato" in texto(cliente.get(f"/admin/procesos/{proceso.id}"))

    # Edición: quitar Finanzas y desactivar
    html = texto(cliente.get(f"/admin/flujos/{flujo.id}"))
    assert f'name="posicion_{caja.id}"' in html and 'value="2"' in html
    cliente.post(f"/admin/flujos/{flujo.id}", data={
        "codigo": "EFE", "nombre": "Pago en efectivo", f"posicion_{caja.id}": "1", f"posicion_{firma.id}": "2",
    })
    flujo = m.FlujoAlternativo.get_by_id(flujo.id)
    assert cfg.fases_del_flujo(flujo.id) == [caja.id, firma.id] and not flujo.activo


def test_flujo_sin_fases_muestra_error(cliente):
    proceso = crear_proceso(cliente)
    respuesta = cliente.post(f"/admin/procesos/{proceso.id}/flujos/nuevo", data={"codigo": "X", "nombre": "X"})
    assert respuesta.status_code == 200 and "no tiene fases" in texto(respuesta)


def test_activar_y_cerrar_proceso(cliente, admin):
    proceso = crear_proceso(cliente)
    for f in m.Fase.select().where(m.Fase.proceso == proceso.id):
        cliente.post(f"/admin/fases/{f.id}/responsables", data={"usuarios": [str(admin.id)]})
    assert "Activar proceso" in texto(cliente.get(f"/admin/procesos/{proceso.id}"))
    cliente.post(f"/admin/procesos/{proceso.id}/activar")
    assert m.Proceso.get_by_id(proceso.id).estado == "ACTIVO"

    pantalla = texto(cliente.get(f"/admin/procesos/{proceso.id}/cerrar"))
    assert "<strong>0</strong> expediente(s)" in pantalla
    respuesta = cliente.post(f"/admin/procesos/{proceso.id}/cerrar", data={"confirmacion": "otro"})
    assert "escriba el código del proceso" in texto(respuesta)
    assert m.Proceso.get_by_id(proceso.id).estado == "ACTIVO"
    cliente.post(f"/admin/procesos/{proceso.id}/cerrar", data={"confirmacion": "MAT2027"})
    assert m.Proceso.get_by_id(proceso.id).estado == "CERRADO"
    assert "solo lectura" in texto(cliente.get(f"/admin/procesos/{proceso.id}"))


def test_activar_sin_responsables_muestra_lo_que_falta(cliente):
    proceso = crear_proceso(cliente)
    respuesta = cliente.post(f"/admin/procesos/{proceso.id}/activar", follow_redirects=True)
    assert "La fase «Iniciado» no tiene responsables" in texto(respuesta)
    assert m.Proceso.get_by_id(proceso.id).estado == "BORRADOR"


# ── Usuarios ──

def clave_mostrada(respuesta):
    return re.search(r"<code>([^<]+)</code>", texto(respuesta)).group(1)


def test_crear_usuario_muestra_la_clave_una_vez(cliente):
    proceso = crear_proceso(cliente)
    ini = fase(proceso, "INI")
    assert cliente.get("/admin/usuarios/nuevo").status_code == 200
    respuesta = cliente.post("/admin/usuarios/nuevo", data={
        "email": "ana@unach.cl", "nombre": "Ana Pérez", "rol": "USUARIO_FASE", "fases": [str(ini.id)],
    })
    assert respuesta.status_code == 200 and "No se volverá a mostrar" in texto(respuesta)
    clave = clave_mostrada(respuesta)
    ana = m.Usuario.get(m.Usuario.email == "ana@unach.cl")
    assert verificar_password(clave, ana.password_hash) and ana.must_change_password
    with cliente.session_transaction() as sesion:
        assert clave not in str(dict(sesion))  # nunca en la cookie de sesión
    lista = texto(cliente.get("/admin/usuarios"))
    assert "Ana Pérez" in lista and "Clave temporal" in lista


def test_usuario_de_fase_sin_fases_muestra_error(cliente):
    respuesta = cliente.post("/admin/usuarios/nuevo", data={"email": "a@unach.cl", "nombre": "A", "rol": "USUARIO_FASE"})
    assert respuesta.status_code == 200 and "al menos una fase" in texto(respuesta)


def test_editar_usuario_y_permiso_de_carga(cliente):
    proceso = crear_proceso(cliente)
    ini, mnp = fase(proceso, "INI"), fase(proceso, "MNP")
    ana, _ = cfg.crear_usuario({"email": "ana@unach.cl", "nombre": "Ana", "rol": "USUARIO_FASE",
                                "fases": [ini.id]}, m.Usuario.get(m.Usuario.email == "admin@unach.cl"))
    html = texto(cliente.get(f"/admin/usuarios/{ana.id}"))
    assert f'value="{ini.id}" checked' in html
    respuesta = cliente.post(f"/admin/usuarios/{ana.id}", data={
        "email": "ana@unach.cl", "nombre": "Ana María", "rol": "USUARIO_FASE",
        "fases": [str(mnp.id)], "puede_registrar_postulantes": "on",
    })
    assert respuesta.status_code == 302
    ana = m.Usuario.get_by_id(ana.id)
    assert ana.nombre == "Ana María" and ana.puede_registrar_postulantes
    assert cfg.fases_abiertas_de(ana.id) == {mnp.id}


def test_desactivar_activar_y_restablecer(cliente):
    gestor = usuario("g@unach.cl", rol="GESTOR")
    cliente.post(f"/admin/usuarios/{gestor.id}/estado", data={"activo": "0"})
    assert not m.Usuario.get_by_id(gestor.id).activo
    cliente.post(f"/admin/usuarios/{gestor.id}/estado", data={"activo": "1"})
    assert m.Usuario.get_by_id(gestor.id).activo
    respuesta = cliente.post(f"/admin/usuarios/{gestor.id}/restablecer")
    assert verificar_password(clave_mostrada(respuesta), m.Usuario.get_by_id(gestor.id).password_hash)


def test_no_puede_desactivarse_a_si_mismo(cliente, admin):
    usuario("otro@unach.cl")
    respuesta = cliente.post(f"/admin/usuarios/{admin.id}/estado", data={"activo": "0"}, follow_redirects=True)
    assert "propia cuenta" in texto(respuesta)
    assert m.Usuario.get_by_id(admin.id).activo


def test_acciones_que_modifican_no_aceptan_get(cliente, admin):
    assert cliente.get(f"/admin/usuarios/{admin.id}/restablecer").status_code == 405
    assert cliente.get(f"/admin/usuarios/{admin.id}/estado").status_code == 405
