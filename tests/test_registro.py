"""Catálogo de carreras (RF-25), carga manual (RF-07) y edición de postulantes."""

from datetime import date

import openpyxl
import pytest
from io import BytesIO

from app import models as m
from app.services import archivos, catalogo, registro
from app.services.inicio import DatosInvalidosError
from app.services.workflow import ReglaVioladaError
from tests.test_inicio import admin, armar_proceso, auditorias, ctx, datos, precargar  # noqa: F401


def usuario(rol, email, **extra):
    return m.Usuario.create(email=email, nombre=email.split("@")[0].title(), rol=rol, must_change_password=False, **extra)


def xlsx(filas):
    libro = openpyxl.Workbook()
    for fila in filas:
        libro.active.append(fila)
    salida = BytesIO()
    libro.save(salida)
    return salida.getvalue()


# ── Lectura de archivos (compartida por la carga masiva y el catálogo) ──

def test_csv_con_punto_y_coma_bom_y_encabezados_libres():
    contenido = "﻿ Codigo_Carrera ;NOMBRE_CARRERA;extra\nENF;Enfermería;x\n\n;;\nKIN;Kinesiología;y\n".encode()
    filas = archivos.leer_tabla(contenido, "c.csv", ("codigo_carrera", "nombre_carrera"))
    assert filas == [(2, {"codigo_carrera": "ENF", "nombre_carrera": "Enfermería", "extra": "x"}),
                     (5, {"codigo_carrera": "KIN", "nombre_carrera": "Kinesiología", "extra": "y"})]


def test_csv_con_coma_y_alias():
    contenido = b'rut,telefonos_postulante\n"1-9","+569 1"\n'
    filas = archivos.leer_tabla(contenido, "a.CSV", ("rut", "telefono_postulante"),
                                alias={"telefonos_postulante": "telefono_postulante"})
    assert filas == [(2, {"rut": "1-9", "telefono_postulante": "+569 1"})]


def test_falta_una_columna_obligatoria():
    with pytest.raises(archivos.ArchivoInvalido, match="nombre_carrera"):
        archivos.leer_tabla(b"codigo_carrera\nENF\n", "c.csv", ("codigo_carrera", "nombre_carrera"))


def test_formatos_rechazados():
    with pytest.raises(archivos.ArchivoInvalido, match=".csv o .xlsx"):
        archivos.leer_tabla(b"x", "c.xls", ("a",))
    with pytest.raises(archivos.ArchivoInvalido, match="UTF-8"):
        archivos.leer_tabla("a\nEnfermería\n".encode("latin-1"), "c.csv", ("a",))
    with pytest.raises(archivos.ArchivoInvalido, match="Excel"):
        archivos.leer_tabla(b"no es excel", "c.xlsx", ("a",))
    with pytest.raises(archivos.ArchivoInvalido, match="vacío"):
        archivos.leer_tabla(b"", "c.csv", ("a",))


def test_excel_lee_todo_como_texto():
    contenido = xlsx([["rut", "telefono"], ["12345678-K", 56912345678], ["7654321-0", 1234.0], [None, None]])
    assert archivos.leer_tabla(contenido, "p.xlsx", ("rut", "telefono")) == [
        (2, {"rut": "12345678-K", "telefono": "56912345678"}), (3, {"rut": "7654321-0", "telefono": "1234"})]


def test_limite_de_filas(monkeypatch):
    monkeypatch.setattr(archivos, "MAX_FILAS", 2)
    with pytest.raises(archivos.ArchivoInvalido, match="2 filas"):
        archivos.leer_tabla(b"a\n1\n2\n3\n", "c.csv", ("a",))


# ── Catálogo de carreras (RF-25) ──

def test_crear_y_editar_carrera(admin):
    carrera = catalogo.crear({"codigo": " nut ", "nombre": "Nutrición"}, admin)
    assert carrera.codigo == "NUT" and carrera.activo
    with pytest.raises(ReglaVioladaError, match="ya existe"):
        catalogo.crear({"codigo": "NUT", "nombre": "Otra"}, admin)
    with pytest.raises(ReglaVioladaError, match="código"):
        catalogo.crear({"codigo": "N U", "nombre": "Otra"}, admin)
    catalogo.editar(carrera.id, {"codigo": "NUTR", "nombre": "Nutrición y Dietética"}, admin)
    assert m.Carrera.get_by_id(carrera.id).codigo == "NUTR"
    assert [a.accion for a in auditorias("CREAR_CARRERA") + auditorias("EDITAR_CARRERA")] == ["CREAR_CARRERA", "EDITAR_CARRERA"]


def test_carrera_usada_no_cambia_codigo_ni_se_elimina(admin):
    precargar()  # usa ENF
    enf = m.Carrera.get(m.Carrera.codigo == "ENF")
    with pytest.raises(ReglaVioladaError, match="código"):
        catalogo.editar(enf.id, {"codigo": "ENFE", "nombre": "Enfermería"}, admin)
    with pytest.raises(ReglaVioladaError, match="desactívela"):
        catalogo.eliminar(enf.id, admin)
    catalogo.cambiar_estado(enf.id, False, admin)
    assert not m.Carrera.get_by_id(enf.id).activo
    fila = next(c for c in catalogo.listar() if c["carrera"].id == enf.id)
    assert fila["postulantes"] == 1


def test_eliminar_carrera_sin_uso(admin):
    carrera = catalogo.crear({"codigo": "NUT", "nombre": "Nutrición"}, admin)
    catalogo.eliminar(carrera.id, admin)
    assert m.Carrera.get_or_none(m.Carrera.codigo == "NUT") is None
    assert auditorias("ELIMINAR_CARRERA")[0].valor_anterior["codigo"] == "NUT"


def test_importar_catalogo(admin):
    contenido = "codigo_carrera;nombre_carrera\nNUT;Nutrición\nENF;Enfermería (actualizada)\nKIN;Kinesiología\n;Sin código\nX Y;Mal\n".encode()
    resultado = catalogo.importar(contenido, "carreras.csv", admin)
    assert (resultado["creadas"], resultado["actualizadas"], resultado["sin_cambios"]) == (1, 1, 1)
    assert [fila for fila, _ in resultado["errores"]] == [5, 6]
    assert m.Carrera.get(m.Carrera.codigo == "ENF").nombre == "Enfermería (actualizada)"
    assert auditorias("IMPORTAR_CARRERAS")[0].valor_nuevo["creadas"] == 1


# ── Carga manual (RF-07) ──

def test_quien_puede_registrar(admin):
    gestor = usuario("GESTOR", "g@unach.cl")
    fase_sin = usuario("USUARIO_FASE", "f@unach.cl")
    fase_con = usuario("USUARIO_FASE", "f2@unach.cl", puede_registrar_postulantes=True)
    assert registro.puede_registrar(admin) and registro.puede_registrar(gestor)
    assert not registro.puede_registrar(fase_sin) and registro.puede_registrar(fase_con)
    assert registro.puede_editar(gestor) and not registro.puede_editar(fase_con)
    with pytest.raises(ReglaVioladaError) as error:
        registro.registrar(datos(), fase_sin)
    assert error.value.codigo_http == 403


def test_registrar_sin_iniciar_proceso(admin):
    proceso = armar_proceso(admin)
    postulante = registro.registrar(datos(carrera2="KIN"), admin)
    assert postulante.origen == "CARGA_MANUAL" and postulante.rut == "12345678-5"
    assert postulante.eliminar_en == proceso.fecha_eliminacion
    assert m.Expediente.select().count() == 0 and m.EnvioCorreo.select().count() == 0
    assert [c.carrera.codigo for c in postulante.carreras.order_by(m.PostulanteCarrera.preferencia)] == ["ENF", "KIN"]
    assert auditorias("CREAR_POSTULANTE")[0].valor_nuevo["origen"] == "CARGA_MANUAL"


def test_sin_proceso_activo_se_elimina_el_30_de_noviembre(admin):
    postulante = registro.registrar(datos(), admin, hoy=date(2027, 2, 1))
    assert postulante.eliminar_en == date(2027, 11, 30)


def test_registro_valida_y_rechaza_rut_existente(admin):
    with pytest.raises(DatosInvalidosError) as error:
        registro.registrar(datos(correo_personal="malo", carrera1=""), admin)
    assert set(error.value.campos) == {"correo_personal", "carrera1"}
    existente = precargar()
    with pytest.raises(registro.PostulanteExistenteError) as error:
        registro.registrar(datos(), admin)
    assert error.value.postulante.id == existente.id and error.value.codigo_http == 409


def test_carrera_inactiva_se_rechaza_al_registrar(admin):
    catalogo.cambiar_estado(m.Carrera.get(m.Carrera.codigo == "KIN").id, False, admin)
    with pytest.raises(DatosInvalidosError) as error:
        registro.registrar(datos(carrera1="KIN"), admin)
    assert "carrera1" in error.value.campos


# ── Edición (Administrador y Gestor) ──

def test_editar_postulante_sin_cambiar_el_rut(admin):
    postulante = precargar()
    gestor = usuario("GESTOR", "g@unach.cl")
    cambios = registro.editar(postulante.id, datos(rut="11.111.111-1", telefono="+56 9 8765 4321"), gestor)
    assert cambios == {"telefono": ("+56912345678", "+56987654321")}
    postulante = m.Postulante.get_by_id(postulante.id)
    assert postulante.rut == "12345678-5" and postulante.telefono == "+56987654321"
    registro_ = auditorias("EDITAR_POSTULANTE")[0]
    assert registro_.valor_anterior == {"telefono": "+56912345678"} and registro_.usuario_id == gestor.id


def test_editar_conserva_una_carrera_inactiva(admin):
    postulante = precargar()
    catalogo.cambiar_estado(m.Carrera.get(m.Carrera.codigo == "ENF").id, False, admin)
    assert registro.editar(postulante.id, datos(nombres="Juan"), admin) == {"nombres": ("Juan Andrés", "Juan")}


def test_usuario_de_fase_no_edita(admin):
    postulante = precargar()
    fase_con = usuario("USUARIO_FASE", "f@unach.cl", puede_registrar_postulantes=True)
    with pytest.raises(ReglaVioladaError) as error:
        registro.editar(postulante.id, datos(), fase_con)
    assert error.value.codigo_http == 403


def test_desactivar_y_activar_postulante(admin):
    postulante = precargar()
    registro.cambiar_estado(postulante.id, False, admin)
    assert not m.Postulante.get_by_id(postulante.id).activo
    registro.cambiar_estado(postulante.id, True, admin)
    assert [a.valor_nuevo for a in auditorias("CAMBIAR_ESTADO_POSTULANTE")] == [{"activo": False}, {"activo": True}]
