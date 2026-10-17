"""Pantallas de carga manual (4), carga masiva (5), edición de postulante (6) y catálogo de carreras (13)."""

import io
import re

import pytest

from app import models as m
from tests.test_admin import entrar, texto, usuario
from tests.test_carga_masiva import ENCABEZADO, NUEVO
from tests.test_inicio import datos, precargar
from tests.test_inicio_pantallas import admin, app  # noqa: F401


@pytest.fixture(autouse=True)
def carpeta(app, tmp_path):
    app.config["CARGAS_DIR"] = str(tmp_path)


@pytest.fixture
def staff(app, admin):
    return entrar(app.test_client(), admin)


def cliente_de(app, rol, email, **extra):
    return entrar(app.test_client(), usuario(email, rol=rol, **extra))


# ── Menú y permisos ──

def test_menu_segun_rol(app, staff):
    html = texto(staff.get("/"))
    for enlace in ('href="/postulantes/nuevo"', 'href="/postulantes/carga"', 'href="/admin/carreras"'):
        assert enlace in html
    gestor = cliente_de(app, "GESTOR", "g@unach.cl")
    html = texto(gestor.get("/"))
    assert 'href="/postulantes/carga"' in html and 'href="/admin/carreras"' not in html
    assert gestor.get("/admin/carreras").status_code == 403
    fase = cliente_de(app, "USUARIO_FASE", "f@unach.cl")
    html = texto(fase.get("/"))
    assert 'href="/postulantes/nuevo"' not in html
    assert fase.get("/postulantes/nuevo").status_code == 403
    assert fase.get("/postulantes/carga").status_code == 403


def test_usuario_de_fase_con_permiso_solo_registra(app):
    fase = cliente_de(app, "USUARIO_FASE", "f@unach.cl", puede_registrar_postulantes=True)
    assert fase.get("/postulantes/nuevo").status_code == 200
    assert fase.get("/postulantes/carga").status_code == 403
    postulante = precargar()
    assert fase.get(f"/postulantes/{postulante.id}/editar").status_code == 403
    assert fase.post(f"/postulantes/{postulante.id}/estado", data={"activo": "0"}).status_code == 403


# ── Carga manual ──

def test_registrar_y_ver_la_ficha(staff):
    respuesta = staff.post("/postulantes/nuevo", data=datos())
    postulante = m.Postulante.get()
    assert respuesta.status_code == 302 and respuesta.headers["Location"] == f"/ficha/{postulante.id}"
    html = texto(staff.get(respuesta.headers["Location"]))
    assert "Se registró a Juan Andrés Pérez Soto" in html and "no ha iniciado ningún proceso" in html


def test_registro_con_errores_y_rut_existente(staff):
    respuesta = staff.post("/postulantes/nuevo", data=datos(correo_personal="malo"))
    assert respuesta.status_code == 422 and "formato válido" in texto(respuesta)
    existente = precargar()
    respuesta = staff.post("/postulantes/nuevo", data=datos())
    html = texto(respuesta)
    assert respuesta.status_code == 409 and "ya está registrado" in html and f"/ficha/{existente.id}" in html


# ── Edición ──

def test_editar_desde_la_ficha(staff):
    postulante = precargar()
    assert f"/postulantes/{postulante.id}/editar" in texto(staff.get(f"/ficha/{postulante.id}"))
    html = texto(staff.get(f"/postulantes/{postulante.id}/editar"))
    assert "12345678-5" in html and 'name="rut"' not in html
    respuesta = staff.post(f"/postulantes/{postulante.id}/editar", data=datos(telefono="+56987654321"),
                           follow_redirects=True)
    assert "Datos actualizados." in texto(respuesta)
    assert m.Postulante.get().telefono == "+56987654321"
    staff.post(f"/postulantes/{postulante.id}/estado", data={"activo": "0"})
    assert not m.Postulante.get().activo


# ── Carga masiva ──

def test_carga_masiva_de_punta_a_punta(staff):
    for formato in ("csv", "xlsx"):
        assert staff.get(f"/postulantes/carga/plantilla.{formato}").status_code == 200
    contenido = "\n".join((ENCABEZADO, NUEVO, "12.345.678-9;X;Y;+56911111111;x@y.cl;ENF;;")).encode()
    respuesta = staff.post("/postulantes/carga", data={"archivo": (io.BytesIO(contenido), "carga.csv")},
                           content_type="multipart/form-data")
    assert respuesta.status_code == 302
    previa = respuesta.headers["Location"]
    html = texto(staff.get(previa))
    assert "1 nuevos" in html and "1 con errores" in html and "Ana María Paz Rojas" in html
    assert m.Postulante.select().count() == 0  # aún no se guarda nada

    token = re.search(r"/postulantes/carga/([^/]+)$", previa).group(1)
    respuesta = staff.post(f"/postulantes/carga/{token}/importar", data={"politica": "NO_MODIFICAR"})
    assert respuesta.status_code == 302 and respuesta.headers["Location"].endswith("/resultado")
    html = texto(staff.get(respuesta.headers["Location"]))
    assert "Carga importada" in html and "Descargar errores" in html
    errores = staff.get(f"/postulantes/carga/{token}/errores.csv")
    assert errores.mimetype == "text/csv" and "12.345.678-9" in texto(errores)
    assert m.Postulante.select().count() == 1
    # La vista previa ya no sirve: se importó
    assert staff.get(previa, follow_redirects=True).status_code == 200
    assert "ya fue importada" in texto(staff.get(previa, follow_redirects=True))


def test_carga_masiva_archivo_invalido(staff):
    respuesta = staff.post("/postulantes/carga", data={"archivo": (io.BytesIO(b"a;b\n1;2\n"), "carga.csv")},
                           content_type="multipart/form-data")
    assert respuesta.status_code == 422 and "Falta la columna obligatoria" in texto(respuesta)
    assert staff.post("/postulantes/carga", data={}).status_code == 422


def test_carga_ajena_no_se_ve(app, staff):
    contenido = "\n".join((ENCABEZADO, NUEVO)).encode()
    previa = staff.post("/postulantes/carga", data={"archivo": (io.BytesIO(contenido), "carga.csv")},
                        content_type="multipart/form-data").headers["Location"]
    gestor = cliente_de(app, "GESTOR", "g@unach.cl")
    assert gestor.get(previa).status_code == 404


# ── Catálogo de carreras ──

def test_catalogo_de_carreras(staff):
    html = texto(staff.get("/admin/carreras"))
    assert "Enfermería" in html and "ENF" in html
    respuesta = staff.post("/admin/carreras", data={"codigo": "nut", "nombre": "Nutrición"}, follow_redirects=True)
    assert "Carrera «Nutrición» (NUT) creada." in texto(respuesta)
    nut = m.Carrera.get(m.Carrera.codigo == "NUT")
    staff.post(f"/admin/carreras/{nut.id}", data={"codigo": "NUT", "nombre": "Nutrición y Dietética"})
    staff.post(f"/admin/carreras/{nut.id}/estado", data={"activo": "0"})
    nut = m.Carrera.get_by_id(nut.id)
    assert nut.nombre == "Nutrición y Dietética" and not nut.activo
    assert staff.post("/admin/carreras", data={"codigo": "NUT", "nombre": "x"}).status_code == 422
    staff.post(f"/admin/carreras/{nut.id}/eliminar")
    assert m.Carrera.get_or_none(m.Carrera.id == nut.id) is None


def test_importar_catalogo_desde_la_pantalla(staff):
    contenido = "codigo_carrera,nombre_carrera\nNUT,Nutrición\n,Sin código\n".encode()
    respuesta = staff.post("/admin/carreras/importar", data={"archivo": (io.BytesIO(contenido), "c.csv")},
                           content_type="multipart/form-data")
    html = texto(respuesta)
    assert "1 nuevas" in html and "Fila 3" in html
