"""RF-22 (autenticación), RNF-01 (CSRF y control de acceso en el servidor) y la parte de
sesiones de RF-24 (auditoría)."""

import time
from datetime import timedelta

import pytest
from flask import Blueprint

from app import create_app
from app import models as m
from app.config import TestConfig
from app.security import error_politica, hash_password, requiere_rol, verificar_password
from app.services import autenticacion

CLAVE = "clave-segura-123"


def crear_usuario(email="ana@unach.cl", clave=CLAVE, rol="USUARIO_FASE", **extra):
    datos = {"must_change_password": False, **extra}
    return m.Usuario.create(
        email=email, nombre="Ana Pérez", rol=rol, password_hash=hash_password(clave), **datos
    )


def recargar(usuario):
    return m.Usuario.get_by_id(usuario.id)


def auditorias(accion):
    return list(m.Auditoria.select().where(m.Auditoria.accion == accion).order_by(m.Auditoria.id))


@pytest.fixture
def app(bd):
    app = create_app(TestConfig)
    # Rutas de prueba para el control de acceso por rol
    prueba = Blueprint("prueba", __name__)

    @prueba.get("/solo-admin")
    @requiere_rol("ADMIN")
    def solo_admin():
        return "admin ok"

    @prueba.get("/protegida")
    def protegida():
        return "protegida ok"

    app.register_blueprint(prueba)
    return app


@pytest.fixture
def cliente(app):
    return app.test_client()


def ingresar(cliente, email="ana@unach.cl", clave=CLAVE, **extra):
    return cliente.post("/login", data={"email": email, "password": clave, **extra})


# ── Contraseñas ──

def test_politica_exige_10_caracteres():
    assert error_politica("123456789") is not None
    assert error_politica("1234567890") is None


def test_hash_bcrypt_y_verificacion():
    h = hash_password(CLAVE)
    assert h.startswith("$2") and CLAVE not in h
    assert verificar_password(CLAVE, h)
    assert not verificar_password("otra-clave-123", h)
    assert not verificar_password(CLAVE, None)
    assert not verificar_password(CLAVE, "no-es-un-hash")


# ── Inicio de sesión ──

def test_pantalla_de_login(cliente):
    respuesta = cliente.get("/login")
    assert respuesta.status_code == 200
    assert "Iniciar sesión" in respuesta.get_data(as_text=True)


def test_login_exitoso_redirige_al_inicio_y_audita(cliente):
    usuario = crear_usuario(intentos_fallidos=3)
    respuesta = ingresar(cliente)
    assert respuesta.status_code == 302
    assert respuesta.headers["Location"] == "/"
    with cliente.session_transaction() as sesion:
        assert sesion["usuario_id"] == usuario.id
    assert recargar(usuario).intentos_fallidos == 0
    [registro] = auditorias("LOGIN")
    assert registro.usuario_id == usuario.id and registro.rol == "USUARIO_FASE"
    assert registro.actor == "USUARIO" and registro.entidad == "usuario"
    assert cliente.get("/").status_code == 200


def test_login_no_distingue_mayusculas_en_el_correo(cliente):
    crear_usuario()
    assert ingresar(cliente, email="  Ana@UNACH.cl ").status_code == 302


def test_clave_incorrecta_suma_intento_y_audita(cliente):
    usuario = crear_usuario()
    respuesta = ingresar(cliente, clave="incorrecta-123")
    assert respuesta.status_code == 200
    assert "Correo o contraseña incorrectos" in respuesta.get_data(as_text=True)
    assert recargar(usuario).intentos_fallidos == 1
    [registro] = auditorias("LOGIN_FALLIDO")
    assert registro.usuario_id == usuario.id
    with cliente.session_transaction() as sesion:
        assert "usuario_id" not in sesion


def test_correo_inexistente_da_el_mismo_mensaje(cliente):
    respuesta = ingresar(cliente, email="nadie@unach.cl")
    assert "Correo o contraseña incorrectos" in respuesta.get_data(as_text=True)
    [registro] = auditorias("LOGIN_FALLIDO")
    assert registro.usuario_id is None and registro.actor == "PUBLICO"
    assert registro.valor_nuevo == {"email": "nadie@unach.cl"}


def test_usuario_desactivado_no_ingresa(cliente):
    crear_usuario(activo=False)
    respuesta = ingresar(cliente)
    assert "Correo o contraseña incorrectos" in respuesta.get_data(as_text=True)


def test_usuario_sin_clave_no_ingresa(cliente):
    usuario = crear_usuario()
    usuario.password_hash = None
    usuario.save()
    assert ingresar(cliente).status_code == 200


def test_bloqueo_de_15_minutos_tras_5_intentos(cliente):
    usuario = crear_usuario()
    for _ in range(4):
        ingresar(cliente, clave="incorrecta-123")
    assert recargar(usuario).bloqueado_hasta is None
    respuesta = ingresar(cliente, clave="incorrecta-123")
    bloqueado = recargar(usuario)
    assert bloqueado.bloqueado_hasta is not None
    restante = bloqueado.bloqueado_hasta - m.ahora()
    assert timedelta(minutes=14) < restante <= timedelta(minutes=15)
    assert "bloqueada" in respuesta.get_data(as_text=True)
    assert len(auditorias("BLOQUEO_CUENTA")) == 1

    # Ni la clave correcta sirve mientras dure el bloqueo
    respuesta = ingresar(cliente)
    assert respuesta.status_code == 200
    assert "bloqueada" in respuesta.get_data(as_text=True)

    # Vencido el bloqueo, ingresa y se reinicia el contador
    bloqueado.bloqueado_hasta = m.ahora() - timedelta(seconds=1)
    bloqueado.save()
    assert ingresar(cliente).status_code == 302
    final = recargar(usuario)
    assert final.intentos_fallidos == 0 and final.bloqueado_hasta is None


def test_intentos_fallidos_son_consecutivos(cliente):
    usuario = crear_usuario()
    for _ in range(4):
        ingresar(cliente, clave="incorrecta-123")
    ingresar(cliente)  # el ingreso correcto reinicia la cuenta
    cliente.post("/logout")
    ingresar(cliente, clave="incorrecta-123")
    assert recargar(usuario).intentos_fallidos == 1
    assert recargar(usuario).bloqueado_hasta is None


def test_next_respeta_rutas_internas_y_rechaza_externas(cliente):
    crear_usuario()
    assert ingresar(cliente, next="/protegida").headers["Location"] == "/protegida"
    cliente.post("/logout")
    assert ingresar(cliente, next="//evil.example").headers["Location"] == "/"
    cliente.post("/logout")
    assert ingresar(cliente, next="https://evil.example").headers["Location"] == "/"


def test_ya_autenticado_no_ve_el_login(cliente):
    crear_usuario()
    ingresar(cliente)
    assert cliente.get("/login").status_code == 302


# ── Control de acceso ──

def test_pagina_protegida_sin_sesion_redirige_al_login(cliente):
    respuesta = cliente.get("/protegida")
    assert respuesta.status_code == 302
    assert respuesta.headers["Location"] == "/login?next=/protegida"


def test_salud_es_publica(cliente):
    assert cliente.get("/salud").status_code == 200


def test_requiere_rol_responde_403(cliente):
    crear_usuario()
    ingresar(cliente)
    respuesta = cliente.get("/solo-admin")
    assert respuesta.status_code == 403
    assert "No tiene permiso" in respuesta.get_data(as_text=True)


def test_requiere_rol_permite_al_rol_indicado(cliente):
    crear_usuario(rol="ADMIN")
    ingresar(cliente)
    assert cliente.get("/solo-admin").get_data(as_text=True) == "admin ok"


def test_usuario_desactivado_durante_la_sesion_pierde_acceso(cliente):
    usuario = crear_usuario()
    ingresar(cliente)
    usuario.activo = False
    usuario.save()
    assert cliente.get("/protegida").status_code == 302


# ── Cambio obligatorio de contraseña ──

def test_must_change_password_bloquea_todas_las_pantallas(cliente):
    crear_usuario(must_change_password=True)
    respuesta = ingresar(cliente)
    assert respuesta.headers["Location"] == "/cambiar-password"
    for ruta in ("/", "/protegida", "/solo-admin"):
        respuesta = cliente.get(ruta)
        assert respuesta.status_code == 302
        assert respuesta.headers["Location"] == "/cambiar-password"
    assert cliente.get("/cambiar-password").status_code == 200


def cambiar(cliente, actual=CLAVE, nueva="nueva-clave-456", confirmacion=None):
    return cliente.post("/cambiar-password", data={
        "actual": actual, "nueva": nueva,
        "confirmacion": nueva if confirmacion is None else confirmacion,
    })


def test_cambio_de_contraseña_libera_el_acceso_y_audita(cliente):
    usuario = crear_usuario(must_change_password=True)
    ingresar(cliente)
    respuesta = cambiar(cliente)
    assert respuesta.status_code == 302 and respuesta.headers["Location"] == "/"
    actualizado = recargar(usuario)
    assert not actualizado.must_change_password
    assert verificar_password("nueva-clave-456", actualizado.password_hash)
    assert len(auditorias("CAMBIO_PASSWORD")) == 1
    assert cliente.get("/protegida").status_code == 200


@pytest.mark.parametrize("datos, mensaje", [
    ({"actual": "incorrecta-123"}, "La contraseña actual no es correcta"),
    ({"nueva": "corta"}, "al menos 10 caracteres"),
    ({"confirmacion": "otra-cosa-789"}, "no coinciden"),
    ({"nueva": CLAVE}, "distinta de la actual"),
])
def test_cambio_de_contraseña_invalido(cliente, datos, mensaje):
    usuario = crear_usuario(must_change_password=True)
    ingresar(cliente)
    respuesta = cambiar(cliente, **datos)
    assert respuesta.status_code == 200
    assert mensaje in respuesta.get_data(as_text=True)
    assert recargar(usuario).must_change_password
    assert auditorias("CAMBIO_PASSWORD") == []


# ── Expiración de la sesión ──

def test_sesion_expira_tras_30_minutos_de_inactividad(cliente):
    crear_usuario()
    ingresar(cliente)
    with cliente.session_transaction() as sesion:
        sesion["ultimo_acceso"] = time.time() - 31 * 60
    respuesta = cliente.get("/protegida")
    assert respuesta.status_code == 302 and respuesta.headers["Location"].startswith("/login")
    with cliente.session_transaction() as sesion:
        assert "usuario_id" not in sesion


def test_actividad_renueva_la_inactividad(cliente):
    crear_usuario()
    ingresar(cliente)
    with cliente.session_transaction() as sesion:
        sesion["ultimo_acceso"] = time.time() - 29 * 60
    assert cliente.get("/protegida").status_code == 200
    with cliente.session_transaction() as sesion:
        assert time.time() - sesion["ultimo_acceso"] < 5


def test_sesion_expira_a_las_8_horas_aunque_haya_actividad(cliente):
    crear_usuario()
    ingresar(cliente)
    with cliente.session_transaction() as sesion:
        sesion["inicio"] = time.time() - 8 * 60 * 60 - 1
    assert cliente.get("/protegida").status_code == 302


# ── Cierre de sesión ──

def test_logout_cierra_la_sesion_y_audita(cliente):
    crear_usuario()
    ingresar(cliente)
    assert cliente.get("/logout").status_code == 405  # solo POST, con CSRF
    respuesta = cliente.post("/logout")
    assert respuesta.status_code == 302 and respuesta.headers["Location"] == "/login"
    assert cliente.get("/protegida").status_code == 302
    assert len(auditorias("LOGOUT")) == 1


# ── CSRF ──

def test_csrf_activo_en_el_login(bd):
    class ConCsrf(TestConfig):
        WTF_CSRF_ENABLED = True

    cliente = create_app(ConCsrf).test_client()
    crear_usuario()
    assert ingresar(cliente).status_code == 400
    assert 'name="csrf_token"' in cliente.get("/login").get_data(as_text=True)


# ── Super administrador inicial ──

class ConfigSuper:
    SUPERADMIN_EMAIL = "Admin@UNACH.cl"
    SUPERADMIN_NAME = "Administrador"
    SUPERADMIN_PASSWORD_HASH = ""
    SUPERADMIN_PASSWORD = ""


def test_superadmin_desde_hash(bd):
    config = ConfigSuper()
    config.SUPERADMIN_PASSWORD_HASH = hash_password(CLAVE)
    usuario = autenticacion.asegurar_superadmin(config)
    assert usuario.email == "admin@unach.cl" and usuario.rol == "ADMIN"
    assert not usuario.must_change_password
    assert verificar_password(CLAVE, usuario.password_hash)
    [registro] = auditorias("CREAR_SUPERADMIN")
    assert registro.actor == "SISTEMA"


def test_superadmin_con_clave_en_texto_obliga_a_cambiarla(bd):
    config = ConfigSuper()
    config.SUPERADMIN_PASSWORD = CLAVE
    usuario = autenticacion.asegurar_superadmin(config)
    assert usuario.must_change_password
    assert verificar_password(CLAVE, usuario.password_hash)


def test_superadmin_es_idempotente_y_no_pisa_la_cuenta(bd):
    existente = crear_usuario(email="admin@unach.cl", rol="ADMIN")
    config = ConfigSuper()
    config.SUPERADMIN_PASSWORD = "otra-clave-999"
    assert autenticacion.asegurar_superadmin(config).id == existente.id
    assert verificar_password(CLAVE, recargar(existente).password_hash)
    assert auditorias("CREAR_SUPERADMIN") == []


def test_superadmin_sin_configuracion_no_hace_nada(bd):
    config = ConfigSuper()
    config.SUPERADMIN_EMAIL = ""
    assert autenticacion.asegurar_superadmin(config) is None


def test_superadmin_sin_clave_falla(bd):
    with pytest.raises(ValueError):
        autenticacion.asegurar_superadmin(ConfigSuper())
