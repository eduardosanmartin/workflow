"""Conservación limitada al período lectivo (RF-26): totales agregados, eliminación de los datos
de procesos vencidos, anonimización de la auditoría, simulación, avisos y comando."""

from datetime import date, timedelta

import pytest

from app import models as m
from app.services import bandeja, retencion
from app.services import configuracion as cfg
from tests.test_bandeja import T0, finalizar, iniciar, rut, version
from tests.test_inicio import admin, armar_proceso, ctx, precargar  # noqa: F401

ELIMINACION = date(2027, 11, 30)  # por defecto, 30 de noviembre del año de inicio


@pytest.fixture
def escenario(admin):
    """Matrícula 2027 cerrada: P1 matriculado, P2 pendiente, P3 sigue en Matrícula 2028 (activo).
    P4 cargado sin iniciar y vencido; P5 cargado sin iniciar y vigente."""
    proceso = armar_proceso(admin)
    e1 = iniciar(admin, 11111111, ahora=T0, nombres="Pía", correo_personal="pia@gmail.com")
    bandeja.atender(e1.id, version(e1), admin, ahora=T0)
    finalizar(e1, admin, ahora=T0 + timedelta(minutes=5))
    e2 = iniciar(admin, 22222222, ["BEC"], ahora=T0)
    e3 = iniciar(admin, 33333333, ahora=T0)
    cfg.cerrar_proceso(proceso.id, admin)
    nuevo = armar_proceso(admin, codigo="MAT2028")
    m.Proceso.update(fecha_eliminacion=date(2028, 11, 30)).where(m.Proceso.id == nuevo.id).execute()
    e3b = iniciar(admin, 33333333, ahora=T0)
    p4 = precargar(rut=rut(44444444), eliminar_en=ELIMINACION)
    p5 = precargar(rut=rut(55555555), eliminar_en=date(2028, 11, 30))
    return {"proceso": m.Proceso.get_by_id(proceso.id), "nuevo": nuevo, "e1": e1, "e2": e2, "e3": e3,
            "e3b": e3b, "p4": p4, "p5": p5}


def existe(modelo, id_):
    return modelo.select().where(modelo.id == id_).exists()


# ── Totales agregados ──

def test_al_cerrar_se_calculan_los_totales(escenario):
    datos = retencion.resumen(escenario["proceso"].id)
    total = datos["total"]
    assert (total.matriculados, total.matriculados_cierre_manual, total.proceso_pendiente) == (1, 0, 2)
    assert [(f.carrera.nombre, f.matriculados, f.proceso_pendiente) for f in datos["carreras"]] == [("Enfermería", 1, 2)]
    assert [(f.fase.nombre, f.matriculados, f.proceso_pendiente) for f in datos["fases"]] == [
        ("Becas", 0, 1), ("MNP", 1, 1)]


# ── Eliminación ──

def test_antes_de_la_fecha_no_se_elimina_nada(escenario):
    informe = retencion.purgar(hoy=ELIMINACION - timedelta(days=1))
    assert informe.procesos == [] and informe.sin_proceso.postulantes == 0
    assert m.Expediente.select().count() == 4


def test_elimina_los_datos_del_proceso_vencido(escenario):
    e1, e2, e3 = escenario["e1"], escenario["e2"], escenario["e3"]
    informe = retencion.purgar(hoy=ELIMINACION)
    resultado = informe.procesos[0]
    assert resultado.proceso == "Matrícula 2027"
    assert (resultado.expedientes, resultado.postulantes) == (3, 2)  # P3 se conserva: sigue en 2028
    assert resultado.atenciones > 0 and resultado.correos >= 3
    for expediente in (e1, e2, e3):
        assert not existe(m.Expediente, expediente.id)
    assert not m.Atencion.select().where(m.Atencion.expediente.in_([e1.id, e2.id, e3.id])).exists()
    assert not m.EnvioCorreo.select().where(m.EnvioCorreo.expediente.in_([e1.id, e2.id, e3.id])).exists()
    assert not existe(m.Postulante, e1.postulante_id) and not existe(m.Postulante, e2.postulante_id)
    assert not m.PostulanteCarrera.select().where(m.PostulanteCarrera.postulante == e1.postulante_id).exists()
    assert existe(m.Postulante, e3.postulante_id) and existe(m.Expediente, escenario["e3b"].id)
    # Se conserva la configuración y los totales
    proceso = m.Proceso.get_by_id(escenario["proceso"].id)
    assert proceso.datos_eliminados_en is not None and m.Fase.select().where(m.Fase.proceso == proceso.id).exists()
    assert retencion.resumen(proceso.id)["total"].matriculados == 1
    # El proceso activo con la fecha vencida no se toca: queda como alerta
    assert [p.id for p in informe.alertas] == []  # 2028 vence en 2028


def test_postulantes_cargados_sin_iniciar_segun_su_propia_fecha(escenario):
    informe = retencion.purgar(hoy=ELIMINACION)
    assert informe.sin_proceso.postulantes == 1
    assert not existe(m.Postulante, escenario["p4"].id) and existe(m.Postulante, escenario["p5"].id)


def test_la_auditoria_se_anonimiza_y_no_se_borra(escenario):
    e1 = escenario["e1"]
    antes = m.Auditoria.select().count()
    creado = m.Auditoria.get((m.Auditoria.accion == "CREAR_POSTULANTE") & (m.Auditoria.entidad_id == e1.postulante_id))
    assert creado.valor_nuevo["rut"] == "11111111-1" and creado.valor_nuevo["nombres"] == "Pía"
    retencion.purgar(hoy=ELIMINACION)
    creado = m.Auditoria.get_by_id(creado.id)
    assert creado.accion == "CREAR_POSTULANTE" and creado.usuario_id is not None
    for clave in ("rut", "nombres", "apellidos", "telefono", "correo_personal"):
        assert clave not in creado.valor_nuevo
    assert creado.valor_nuevo["carrera1"] == "ENF"
    correos = m.Auditoria.select().where((m.Auditoria.accion == "ENVIAR_CORREO") & (m.Auditoria.entidad_id == e1.id))
    assert correos.count() >= 2 and all("destinatario" not in c.valor_nuevo for c in correos)
    assert m.Auditoria.select().count() == antes + 2  # solo se agregan los registros de la eliminación
    registro = m.Auditoria.get((m.Auditoria.accion == "PURGAR_DATOS") & (m.Auditoria.entidad == "proceso"))
    assert registro.actor == "SISTEMA" and registro.usuario_id is None and registro.valor_nuevo["expedientes"] == 3
    # El que sigue en 2028 conserva su auditoría
    vigente = m.Auditoria.get((m.Auditoria.accion == "INICIAR_PROCESO") & (m.Auditoria.entidad_id == escenario["e3b"].id))
    assert vigente.valor_nuevo["postulante_id"] == escenario["e3"].postulante_id


def test_anonimizar_textos_y_estructuras():
    valor = {"rut": "1-9", "detalle": "Rechazado: pia@gmail.com (12.345.678-5)", "lista": [{"correo": "x@y.cl"}, 3],
             "fase": "Becas"}
    assert retencion.anonimizar(valor) == {"detalle": "Rechazado: [eliminado] ([eliminado])", "lista": [{}, 3],
                                           "fase": "Becas"}


def test_simulacion_no_elimina_nada(escenario):
    expedientes, auditoria = m.Expediente.select().count(), m.Auditoria.select().count()
    informe = retencion.purgar(hoy=ELIMINACION, simular=True)
    assert informe.simulado and informe.procesos[0].expedientes == 3 and informe.sin_proceso.postulantes == 1
    assert m.Expediente.select().count() == expedientes and m.Auditoria.select().count() == auditoria
    assert m.Proceso.get_by_id(escenario["proceso"].id).datos_eliminados_en is None
    assert m.Auditoria.get((m.Auditoria.accion == "CREAR_POSTULANTE")
                           & (m.Auditoria.entidad_id == escenario["e1"].postulante_id)).valor_nuevo["rut"]


def test_no_se_repite_y_no_toca_procesos_activos(escenario):
    retencion.purgar(hoy=ELIMINACION)
    segundo = retencion.purgar(hoy=date(2028, 12, 1))
    assert segundo.procesos == []  # 2027 ya se eliminó y 2028 sigue activo
    assert [p.id for p in segundo.alertas] == [escenario["nuevo"].id]
    assert existe(m.Expediente, escenario["e3b"].id)


# ── Avisos ──

def test_avisos_del_panel(escenario, admin):
    gestor = m.Usuario.create(email="g@unach.cl", nombre="G", rol="GESTOR", must_change_password=False)
    fase = m.Usuario.create(email="f@unach.cl", nombre="F", rol="USUARIO_FASE", must_change_password=False)
    avisos = retencion.avisos(gestor, hoy=ELIMINACION - timedelta(days=10))
    assert len(avisos) == 1 and "Matrícula 2027" in avisos[0] and "en 10 días" in avisos[0]
    assert retencion.avisos(gestor, hoy=ELIMINACION - timedelta(days=31)) == []
    assert retencion.avisos(fase, hoy=ELIMINACION - timedelta(days=10)) == []
    vencido = retencion.avisos(admin, hoy=date(2028, 12, 1))
    assert any("sigue activo" in a for a in vencido)
    assert not any("sigue activo" in a for a in retencion.avisos(gestor, hoy=date(2028, 12, 1)))
