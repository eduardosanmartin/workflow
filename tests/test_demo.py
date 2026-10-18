"""Datos de demostración (`flask datos-demo`)."""

import pytest

from app import models as m
from app.services import demo
from app.services import workflow as wf
from tests.test_inicio import admin, ctx  # noqa: F401


def test_crea_un_proceso_completo_sin_enviar_correos(admin, monkeypatch):
    enviados = []
    monkeypatch.setattr("app.services.correo._enviar_smtp", enviados.append)
    resumen = demo.crear()
    proceso = resumen.proceso
    assert proceso.estado == "ACTIVO" and proceso.codigo == "DEMO"
    assert [r for _n, _c, r, _k in resumen.usuarios] == ["GESTOR"] + ["USUARIO_FASE"] * 4
    assert all(m.Usuario.get(m.Usuario.email == c).must_change_password for _n, c, _r, _k in resumen.usuarios)
    expedientes = list(m.Expediente.select().where(m.Expediente.proceso == proceso.id))
    assert len(expedientes) == 20 and resumen.cargados == 10
    estados = [e.estado for e in expedientes]
    assert estados.count(wf.MATRICULADO) == 7 and estados.count(wf.ABANDONO) == 1
    assert sum(e.con_observaciones for e in expedientes) == 1
    assert sum(e.tipo_cierre == wf.MANUAL for e in expedientes) == 1
    assert sum(e.via_inicio == "QR" for e in expedientes) == 2
    activas = m.Atencion.select().join(m.Expediente).where(
        (m.Expediente.proceso == proceso.id) & m.Atencion.estado.in_(wf.ESTADOS_ACTIVOS))
    assert activas.count() == 12
    assert enviados == []  # ningún correo salió, aunque hubiera SMTP
    assert all(e.estado == "ERROR" for e in m.EnvioCorreo.select())
    assert m.Postulante.select().where(~m.Postulante.correo_personal.endswith("@example.com")).count() == 0


def test_se_niega_en_un_ambiente_con_datos(admin):
    from tests.test_inicio import armar_proceso
    armar_proceso(admin)
    with pytest.raises(demo.DemoNoPermitidaError, match="proceso activo"):
        demo.crear()


def test_eliminar_la_demostracion(admin):
    demo.crear()
    assert demo.eliminar() == 20
    assert not m.Proceso.select().where(m.Proceso.codigo == "DEMO").exists()
    assert m.Postulante.select().count() == 0 and m.Expediente.select().count() == 0
    assert not m.Usuario.select().where(m.Usuario.email.startswith("demo.") & m.Usuario.activo).exists()
    assert m.Usuario.get_by_id(admin.id).activo
    with pytest.raises(demo.DemoNoPermitidaError):
        demo.eliminar()


def test_comando(admin):
    from app import create_app
    from app.config import TestConfig
    runner = create_app(TestConfig).test_cli_runner()
    resultado = runner.invoke(args=["datos-demo"])
    assert resultado.exit_code == 0, resultado.output
    assert "Matrícula DEMO" in resultado.output and "demo.gestor@unach.cl" in resultado.output
    otra = runner.invoke(args=["datos-demo"])
    assert otra.exit_code != 0 and "proceso activo" in otra.output
    assert "eliminado" in runner.invoke(args=["datos-demo", "--eliminar"]).output
