"""Carga masiva de postulantes desde CSV o Excel (RF-08)."""

import csv
import io
import json
import time

import openpyxl
import pytest
from flask import current_app

from app import models as m
from app.services import carga_masiva as cm
from app.services import catalogo
from app.services.workflow import ReglaVioladaError
from tests.test_inicio import admin, armar_proceso, auditorias, ctx, precargar  # noqa: F401
from tests.test_registro import usuario

ENCABEZADO = ("rut_postulante;nombres_postulante;apellidos_postulante;telefono_postulante;"
              "correo_personal_postulante;carrera1_postulante;carrera2_postulante;carrera3_postulante")


@pytest.fixture(autouse=True)
def carpeta(ctx, tmp_path, monkeypatch):
    monkeypatch.setitem(current_app.config, "CARGAS_DIR", str(tmp_path))
    return tmp_path


def archivo(*filas):
    return "\n".join((ENCABEZADO, *filas)).encode()


NUEVO = "11.111.111-1;Ana María;Paz Rojas;+56 9 1111 1111;ANA@gmail.com;ENF;KIN;"
EXISTENTE = "12345678-5;Juan;Pérez Soto;+56912345678;juan.perez@gmail.com;ENF;PSI;"


def analizar(admin, contenido, nombre="carga.csv"):
    token = cm.analizar_archivo(contenido, nombre, admin)
    return token, cm.vista_previa(token, admin)


# ── Vista previa ──

def test_vista_previa_en_tres_grupos(admin):
    precargar()
    contenido = archivo(
        NUEVO, EXISTENTE,
        "12.345.678-9;X;Y;+56911111111;x@y.cl;ENF;;",       # RUT inválido
        "22222222-2;Luis;Soto;123;malo;ZZZ;;",              # teléfono, correo y carrera
        "11111111-1;Otra;Vez;+56911111111;o@v.cl;ENF;;",    # repetido en el archivo
    )
    _, previa = analizar(admin, contenido)
    assert previa["total"] == 5 and previa["archivo"] == "carga.csv"
    assert [(n["fila"], n["rut"], n["nombre"]) for n in previa["nuevos"]] == [(2, "11111111-1", "Ana María Paz Rojas")]
    [existente] = previa["existentes"]
    assert existente["fila"] == 3 and existente["cambios"] == {
        "nombres": ("Juan Andrés", "Juan"), "carrera2": (None, "PSI")}
    assert existente["completar"] == {"carrera2": (None, "PSI")}
    errores = {e["fila"]: e["motivo"] for e in previa["errores"]}
    assert set(errores) == {4, 5, 6}
    assert "Dígito verificador" in errores[4]
    assert "teléfono" in errores[5] and "correo" in errores[5] and "ZZZ" in errores[5]
    assert errores[6] == "RUT repetido en el archivo (fila 2)"


def test_columna_obligatoria_faltante_rechaza_el_archivo(admin):
    with pytest.raises(ReglaVioladaError, match="correo_personal_postulante"):
        cm.analizar_archivo(b"rut_postulante;nombres_postulante;apellidos_postulante;telefono_postulante;"
                            b"carrera1_postulante\n1-9;a;b;c;ENF\n", "c.csv", admin)


def test_columnas_opcionales_y_alias(admin):
    contenido = ("RUT_POSTULANTE,Nombres_Postulante,apellidos_postulante,Teléfonos_postulante,"
                 "correo_personal_postulante,carrera1_postulante\n11111111-1,Ana,Paz,+56911111111,a@b.cl,ENF\n").encode()
    _, previa = analizar(admin, contenido)
    assert len(previa["nuevos"]) == 1


def test_carrera_inactiva_se_rechaza(admin):
    catalogo.cambiar_estado(m.Carrera.get(m.Carrera.codigo == "KIN").id, False, admin)
    _, previa = analizar(admin, archivo(NUEVO))
    assert "KIN" in previa["errores"][0]["motivo"]


def test_excel(admin):
    libro = openpyxl.Workbook()
    libro.active.append(ENCABEZADO.split(";"))
    libro.active.append(["11111111-1", "Ana", "Paz", 56911111111, "a@b.cl", "enf", None, None])
    salida = io.BytesIO()
    libro.save(salida)
    _, previa = analizar(admin, salida.getvalue(), "carga.xlsx")
    assert previa["nuevos"][0]["datos"]["telefono"] == "56911111111"


def test_solo_administrador_y_gestor(admin):
    con_permiso = usuario("USUARIO_FASE", "f@unach.cl", puede_registrar_postulantes=True)
    with pytest.raises(ReglaVioladaError) as error:
        cm.analizar_archivo(archivo(NUEVO), "c.csv", con_permiso)
    assert error.value.codigo_http == 403


def test_la_carga_pendiente_es_del_usuario_que_la_subio(admin):
    token, _ = analizar(admin, archivo(NUEVO))
    gestor = usuario("GESTOR", "g@unach.cl")
    with pytest.raises(ReglaVioladaError) as error:
        cm.vista_previa(token, gestor)
    assert error.value.codigo_http == 404
    with pytest.raises(ReglaVioladaError) as error:
        cm.vista_previa("../../etc/passwd", admin)
    assert error.value.codigo_http == 404


def test_carga_pendiente_vencida(admin, carpeta):
    token, _ = analizar(admin, archivo(NUEVO))
    ruta = carpeta / f"{token}.json"
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    datos["creado"] = time.time() - 2 * 3600
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    with pytest.raises(ReglaVioladaError, match="venció"):
        cm.vista_previa(token, admin)


# ── Importación ──

def test_importar_nuevos_sin_iniciar_proceso(admin):
    proceso = armar_proceso(admin)
    token, _ = analizar(admin, archivo(NUEVO, "22.222.222-2;Luis;Soto;+56922222222;l@s.cl;PSI;;"))
    carga = cm.importar(token, "NO_MODIFICAR", admin)
    assert (carga.total_filas, carga.creados, carga.actualizados, carga.omitidos, carga.con_error) == (2, 2, 0, 0, 0)
    ana = m.Postulante.get(m.Postulante.rut == "11111111-1")
    assert ana.origen == "CARGA_MASIVA" and ana.carga_id == carga.id and ana.correo_personal == "ana@gmail.com"
    assert ana.telefono == "+56911111111" and ana.eliminar_en == proceso.fecha_eliminacion
    assert [c.carrera.codigo for c in ana.carreras.order_by(m.PostulanteCarrera.preferencia)] == ["ENF", "KIN"]
    assert m.Expediente.select().count() == 0 and m.EnvioCorreo.select().count() == 0
    registro = auditorias("CARGA_MASIVA")[0]
    assert registro.valor_nuevo["creados"] == 2 and registro.valor_nuevo["archivo"] == "carga.csv"


def test_politica_no_modificar(admin):
    precargar()
    token, _ = analizar(admin, archivo(EXISTENTE))
    carga = cm.importar(token, "NO_MODIFICAR", admin)
    assert (carga.actualizados, carga.omitidos) == (0, 1)
    assert m.Postulante.get().nombres == "Juan Andrés"


def test_politica_completar_solo_vacios(admin):
    precargar(telefono=None)
    token, _ = analizar(admin, archivo(EXISTENTE))
    carga = cm.importar(token, "COMPLETAR", admin)
    postulante = m.Postulante.get()
    assert carga.actualizados == 1 and postulante.nombres == "Juan Andrés" and postulante.telefono == "+56912345678"
    assert [c.carrera.codigo for c in postulante.carreras.order_by(m.PostulanteCarrera.preferencia)] == ["ENF", "PSI"]
    assert auditorias("EDITAR_POSTULANTE")[0].valor_nuevo["carga_id"] == carga.id


def test_completar_no_duplica_una_carrera(admin):
    precargar()  # ENF como carrera 1
    token, _ = analizar(admin, archivo("12345678-5;Juan;Pérez;+56912345678;j@g.cl;PSI;ENF;"))
    previa = cm.vista_previa(token, admin)
    assert previa["existentes"][0]["completar"] == {}
    carga = cm.importar(token, "COMPLETAR", admin)
    assert carga.omitidos == 1


def test_politica_sobrescribir(admin):
    precargar()
    token, _ = analizar(admin, archivo(EXISTENTE))
    carga = cm.importar(token, "SOBRESCRIBIR", admin)
    assert carga.actualizados == 1 and m.Postulante.get().nombres == "Juan"


def test_filas_con_error_no_impiden_importar(admin):
    token, _ = analizar(admin, archivo(NUEVO, "12.345.678-9;X;Y;+56911111111;x@y.cl;ENF;;"))
    carga = cm.importar(token, "NO_MODIFICAR", admin)
    assert (carga.creados, carga.con_error) == (1, 1)
    filas = list(csv.reader(io.StringIO(cm.errores_csv(token, admin).lstrip("﻿")), delimiter=";"))
    assert filas[0] == ["fila", "rut_postulante", "motivo"] and filas[1][:2] == ["3", "12.345.678-9"]


def test_no_se_importa_dos_veces(admin):
    token, _ = analizar(admin, archivo(NUEVO))
    cm.importar(token, "NO_MODIFICAR", admin)
    with pytest.raises(ReglaVioladaError) as error:
        cm.importar(token, "NO_MODIFICAR", admin)
    assert error.value.codigo_http == 409


def test_politica_invalida(admin):
    token, _ = analizar(admin, archivo(NUEVO))
    with pytest.raises(ReglaVioladaError, match="existentes"):
        cm.importar(token, "BORRAR", admin)


def test_se_revalida_al_confirmar(admin):
    token, _ = analizar(admin, archivo(NUEVO))
    precargar(rut="11111111-1")  # otro lo registró entre la vista previa y la confirmación
    carga = cm.importar(token, "NO_MODIFICAR", admin)
    assert (carga.creados, carga.omitidos) == (0, 1)


# ── Plantillas ──

def test_plantillas(admin):
    texto = cm.plantilla_csv().decode("utf-8-sig")
    assert texto.splitlines()[0] == ENCABEZADO
    libro = openpyxl.load_workbook(io.BytesIO(cm.plantilla_xlsx()))
    assert libro.sheetnames == ["Postulantes", "Carreras"]
    assert [c.value for c in libro["Postulantes"][1]] == ENCABEZADO.split(";")
    assert ("ENF", "Enfermería") in [(f[0].value, f[1].value) for f in libro["Carreras"].iter_rows(min_row=2)]
    # la plantilla se puede cargar tal cual: su fila de ejemplo es válida
    _, previa = analizar(admin, cm.plantilla_xlsx(), "plantilla.xlsx")
    assert len(previa["nuevos"]) == 1
