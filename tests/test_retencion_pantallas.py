"""Comando `flask purgar-datos`, avisos del panel y totales conservados en reportes (RF-26)."""

from datetime import date, timedelta

import pytest

from app import models as m
from app.services import configuracion as cfg
from tests.test_admin import entrar, texto
from tests.test_bandeja import T0, iniciar
from tests.test_inicio import armar_proceso
from tests.test_inicio_pantallas import admin, app  # noqa: F401


@pytest.fixture
def cerrado(app, admin):
    with app.app_context():
        proceso = armar_proceso(admin)
        iniciar(admin, 11111111, ahora=T0)
        cfg.cerrar_proceso(proceso.id, admin)
    return proceso


def test_comando_simular_y_eliminar(app, cerrado):
    runner = app.test_cli_runner()
    resultado = runner.invoke(args=["purgar-datos", "--simular", "--fecha", "2027-11-30"])
    assert resultado.exit_code == 0, resultado.output
    assert "[SIMULACIÓN] Se eliminarían de «Matrícula 2027»: 1 expedientes" in resultado.output
    assert m.Expediente.select().count() == 1
    resultado = runner.invoke(args=["purgar-datos", "--fecha", "2027-11-30"])
    assert "Eliminados de «Matrícula 2027»: 1 expedientes" in resultado.output
    assert m.Expediente.select().count() == 0
    assert "No hay datos" in runner.invoke(args=["purgar-datos", "--fecha", "2027-11-30"]).output


def test_comando_alerta_si_el_proceso_sigue_activo(app, admin):
    with app.app_context():
        armar_proceso(admin)
    resultado = app.test_cli_runner().invoke(args=["purgar-datos", "--fecha", "2027-12-01"])
    assert resultado.exit_code == 0 and "ALERTA" in resultado.output and "sigue ACTIVO" in resultado.output


def test_aviso_en_el_panel(app, admin, cerrado):
    hoy = m.ahora().date()
    m.Proceso.update(fecha_inicio=hoy - timedelta(days=90), fecha_fin=hoy - timedelta(days=60),
                     fecha_eliminacion=hoy + timedelta(days=5)).where(m.Proceso.id == cerrado.id).execute()
    html = texto(entrar(app.test_client(), admin).get("/"))
    assert "se eliminarán el" in html and "Ir a reportes" in html


def test_reportes_muestran_los_totales_conservados(app, admin, cerrado):
    with app.app_context():
        from app.services import retencion
        retencion.purgar(hoy=date(2027, 11, 30))
    html = texto(entrar(app.test_client(), admin).get(f"/reportes/?proceso={cerrado.id}"))
    assert "Totales conservados" in html and "datos eliminados" in html and "Proceso pendiente" in html
