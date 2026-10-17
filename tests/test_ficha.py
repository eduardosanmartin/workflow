"""Búsqueda por RUT y ficha del postulante (RF-15), reactivación (RF-28) y correcciones de la
ruta y reapertura (RF-27)."""

from datetime import timedelta

import pytest

from app import models as m
from app.services import bandeja, ficha, inicio
from app.services import configuracion as cfg
from app.services import workflow as wf
from app.services.workflow import ReglaVioladaError
from tests.test_bandeja import T0, finalizar, iniciar, orden, usuario_de, version
from tests.test_inicio import admin, armar_proceso, auditorias, ctx, datos, fase, ruta_de  # noqa: F401


@pytest.fixture
def proceso(admin):
    return armar_proceso(admin)


def gestor():
    return m.Usuario.create(email="gestor@unach.cl", nombre="Gabriela Gestora", rol="GESTOR", must_change_password=False)


def abandonado(admin, consultas=("BEC",)):
    expediente = iniciar(admin, consultas=list(consultas))
    bandeja.marcar_abandono(expediente.id, version(expediente), admin, ahora=T0)
    return expediente


# ── Búsqueda ──

def test_buscar_por_rut(admin, proceso):
    expediente = iniciar(admin)
    assert ficha.buscar("12.345.678-5").id == expediente.postulante_id
    assert ficha.buscar("11.111.111-1") is None
    with pytest.raises(ReglaVioladaError) as error:
        ficha.buscar("12.345.678-9")
    assert error.value.codigo_http == 422


# ── Ficha ──

def test_ficha_con_recorrido_y_fase_actual(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC", "FIN"])
    bandeja.atender(expediente.id, version(expediente), admin, ahora=T0 + timedelta(minutes=2))
    becas = usuario_de(proceso, "BEC")
    vista = ficha.ver(expediente.postulante_id, becas, ahora=T0 + timedelta(minutes=12))
    assert vista["postulante"].rut == "12345678-5" and vista["carreras"] == ["Enfermería"]
    exp = vista["expedientes"][0]
    assert exp["numero"] == "M-0001" and exp["estado"] == "No matriculado aún" and exp["flujo"] is None
    assert exp["actual"] == {"fase": "Becas", "estado": "En atención", "transcurrido": "10 min"}
    assert exp["siguiente"] == "Finanzas"
    filas = exp["atenciones"]
    assert [(f["fase"], f["estado"]) for f in filas] == [
        ("Iniciado", "Completado"), ("Becas", "En atención"), ("Finanzas", "Planificada"), ("MNP", "Planificada")]
    assert filas[0]["responsable"] == "Admin" and filas[0]["fin"] == "15-01 10:00"
    assert filas[1]["actual"] and filas[1]["espera"] == "2 min"
    assert exp["puede_actuar"]  # Becas es su fase
    assert not exp["puede_corregir"] and not exp["puede_reabrir"]  # solo Gestor y Administrador


def test_otro_usuario_de_fase_ve_la_ficha_sin_acciones(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    finanzas = usuario_de(proceso, "FIN", email="fin@unach.cl")
    exp = ficha.ver(expediente.postulante_id, finanzas)["expedientes"][0]
    assert not exp["puede_actuar"] and not exp["puede_reactivar"]


def test_historial_de_derivaciones(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    finalizar(expediente, admin, sin_atender="on", **orden(proceso, "FIN"))
    historial = ficha.ver(expediente.postulante_id, admin)["expedientes"][0]["historial"]
    assert [(h["accion"], h["desde"], h["hacia"], h["usuario"]) for h in historial] == [
        ("Inicio de proceso (staff)", "Iniciado", "Becas", "Admin"),
        ("Finalizó y derivó", "Becas", "Finanzas", "Admin"),
    ]


def test_matriculado_con_observaciones(admin, proceso):
    expediente = iniciar(admin)
    finalizar(expediente, admin, sin_atender="on", resultado="observaciones", observacion="Pagaré pendiente")
    exp = ficha.ver(expediente.postulante_id, admin)["expedientes"][0]
    assert exp["estado"] == "Matriculado con observaciones" and exp["actual"] is None
    assert exp["atenciones"][-1]["observacion"] == "Pagaré pendiente"
    assert exp["atenciones"][-1]["sin_tiempo_atencion"]


def test_postulante_sin_expediente(admin, proceso):
    m.Postulante.create(rut="11111111-1", nombres="Ana", apellidos="Paz", correo_personal="a@b.cl", origen="CARGA_MASIVA")
    vista = ficha.ver(m.Postulante.get().id, admin)
    assert vista["expedientes"] == []


def test_ficha_inexistente(admin, proceso):
    with pytest.raises(ReglaVioladaError) as error:
        ficha.ver(999999, admin)
    assert error.value.codigo_http == 404


# ── Reactivación (RF-28) ──

def test_reactivar_en_la_misma_fase(admin, proceso):
    expediente = abandonado(admin)
    becas = usuario_de(proceso, "BEC")
    exp = ficha.ver(expediente.postulante_id, becas)["expedientes"][0]
    assert exp["estado"] == "Abandonó" and exp["puede_reactivar"]
    mensaje = ficha.reactivar(expediente.id, {}, becas, ahora=T0 + timedelta(minutes=30))
    assert mensaje == "M-0001 debe dirigirse a: Becas"
    assert ruta_de(expediente)[1:] == [("BEC", wf.ABANDONADA), ("BEC", wf.EN_ESPERA), ("MNP", wf.PLANIFICADA)]
    assert m.Expediente.get_by_id(expediente.id).estado == wf.EN_CURSO
    assert auditorias("REACTIVAR")[0].valor_nuevo["fase_actual"] == "Becas"


def test_reactivar_en_otra_fase(admin, proceso):
    expediente = abandonado(admin)
    ficha.reactivar(expediente.id, {"fase": str(fase(proceso, "FIN").id)}, admin)
    assert ruta_de(expediente)[2] == ("FIN", wf.EN_ESPERA)


def test_reactiva_tambien_el_responsable_de_iniciado(admin, proceso):
    expediente = abandonado(admin)
    solo_iniciado = usuario_de(proceso, "INI", email="ini2@unach.cl")
    assert ficha.ver(expediente.postulante_id, solo_iniciado)["expedientes"][0]["puede_reactivar"]
    ficha.reactivar(expediente.id, {}, solo_iniciado)


def test_responsable_de_otra_fase_no_reactiva(admin, proceso):
    expediente = abandonado(admin)
    finanzas = usuario_de(proceso, "FIN", email="fin@unach.cl")
    with pytest.raises(ReglaVioladaError) as error:
        ficha.reactivar(expediente.id, {}, finanzas)
    assert error.value.codigo_http == 403


def test_reactivar_dos_veces_falla(admin, proceso):
    expediente = abandonado(admin)
    ficha.reactivar(expediente.id, {}, admin)
    with pytest.raises(ReglaVioladaError, match="Abandonó"):
        ficha.reactivar(expediente.id, {}, admin)


def test_opciones_de_reactivacion(admin, proceso):
    expediente = abandonado(admin)
    opciones = ficha.opciones_reactivar(expediente.id, admin)
    assert opciones["fase_id"] == fase(proceso, "BEC").id
    assert [f.nombre for f in opciones["fases"]] == ["Becas", "Finanzas", "Caja", "MNP"]


# ── Correcciones (RF-27) ──

def test_gestor_corrige_la_ruta(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC", "FIN"])
    g = gestor()
    exp = ficha.ver(expediente.postulante_id, g)["expedientes"][0]
    assert exp["puede_corregir"] and exp["orden"] == {fase(proceso, "FIN").id: 1}
    with pytest.raises(ReglaVioladaError, match="motivo"):
        ficha.corregir_ruta(expediente.id, {"version": str(version(expediente)), "motivo": ""}, g)
    ficha.corregir_ruta(expediente.id, {"version": str(version(expediente)), "motivo": "Error de digitación",
                                        **orden(proceso, "CAJA")}, g)
    assert ruta_de(expediente)[2:] == [("CAJA", wf.PLANIFICADA), ("MNP", wf.PLANIFICADA)]
    registro = auditorias("CORREGIR_RUTA")[0]
    assert registro.valor_anterior["ruta"] == ["Finanzas", "MNP"] and registro.valor_nuevo["motivo"] == "Error de digitación"


def test_usuario_de_fase_no_corrige_ni_reabre(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC", "FIN"])
    becas = usuario_de(proceso, "BEC")
    for accion in (lambda: ficha.corregir_ruta(expediente.id, {"version": str(version(expediente)), "motivo": "x"}, becas),
                   lambda: ficha.reabrir(expediente.id, {"motivo": "x"}, becas)):
        with pytest.raises(ReglaVioladaError) as error:
            accion()
        assert error.value.codigo_http == 403


def test_reabrir_la_ultima_completada(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    finalizar(expediente, admin, sin_atender="on")  # Becas completada; MNP en espera
    exp = ficha.ver(expediente.postulante_id, admin)["expedientes"][0]
    assert exp["puede_reabrir"] and exp["a_reabrir"]["fase"] == "Becas"
    ficha.reabrir(expediente.id, {"motivo": "Se cerró por error", "atencion": str(exp["a_reabrir"]["id"])}, admin)
    assert ruta_de(expediente)[1:] == [("BEC", wf.EN_ESPERA), ("MNP", wf.PLANIFICADA)]
    assert auditorias("REABRIR_ATENCION")[0].valor_nuevo["reabierta"] == "Becas"


def test_reabrir_dos_veces_no_reabre_otra_atencion(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC", "FIN"])
    finalizar(expediente, admin, sin_atender="on", **orden(proceso, "FIN"))
    finalizar(expediente, admin, sin_atender="on")  # Becas y Finanzas completadas
    objetivo = ficha.ver(expediente.postulante_id, admin)["expedientes"][0]["a_reabrir"]["id"]
    form = {"motivo": "Error", "atencion": str(objetivo)}
    ficha.reabrir(expediente.id, form, admin)
    with pytest.raises(ReglaVioladaError) as error:  # doble clic: no reabre Becas
        ficha.reabrir(expediente.id, form, admin)
    assert error.value.codigo_http == 409
    assert ruta_de(expediente)[1] == ("BEC", wf.COMPLETADO)


def test_reabrir_mnp_desmatricula(admin, proceso):
    expediente = iniciar(admin)
    finalizar(expediente, admin, sin_atender="on")
    objetivo = ficha.ver(expediente.postulante_id, admin)["expedientes"][0]["a_reabrir"]["id"]
    ficha.reabrir(expediente.id, {"motivo": "Faltó firmar", "atencion": str(objetivo)}, admin)
    assert m.Expediente.get_by_id(expediente.id).estado == wf.EN_CURSO


def test_proceso_cerrado_es_solo_lectura(admin, proceso):
    expediente = abandonado(admin)
    cfg.cerrar_proceso(proceso.id, admin)
    exp = ficha.ver(expediente.postulante_id, admin)["expedientes"][0]
    assert exp["estado"] == "Proceso pendiente (abandonó)"
    assert not (exp["puede_actuar"] or exp["puede_reactivar"] or exp["puede_corregir"] or exp["puede_reabrir"])
    with pytest.raises(ReglaVioladaError):
        ficha.reactivar(expediente.id, {}, admin)


def test_inicio_por_qr_en_el_historial(admin, proceso):
    expediente = inicio.iniciar_por_qr(datos(), [], ahora=T0)
    historial = ficha.ver(expediente.postulante_id, admin)["expedientes"][0]["historial"]
    assert historial[0]["accion"] == "Inicio de proceso (QR)" and historial[0]["usuario"] == "Postulante (QR)"
    assert historial[0]["hacia"] == "Iniciado"
