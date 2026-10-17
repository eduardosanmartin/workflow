"""Esquema (migraciones) y persistencia del recorrido contra PostgreSQL."""

from datetime import date, datetime, timedelta, timezone

import pytest
from peewee import IntegrityError

import migrate
from app import models as m
from app.config import TestConfig
from app.services import recorridos
from app.services import workflow as wf

T0 = datetime(2027, 1, 10, 12, 0, tzinfo=timezone.utc)


def minutos(n):
    return T0 + timedelta(minutes=n)


@pytest.fixture
def escenario(bd):
    """Proceso ACTIVO con Iniciado, Becas, Finanzas, Caja, Firma y MNP, y un flujo alternativo."""
    proceso = m.Proceso.create(
        codigo="MAT2027", nombre="Matrícula 2027", fecha_inicio=date(2027, 1, 1),
        fecha_fin=date(2027, 3, 31), fecha_eliminacion=date(2027, 11, 30), estado="ACTIVO",
    )
    fases = {}
    for orden, (ident, nombre, tipo, qr) in enumerate([
        ("INI", "Iniciado", wf.INICIAL, True),
        ("BEC", "Becas", wf.CONSULTA, True),
        ("FIN", "Finanzas", wf.CONSULTA, True),
        ("CAJ", "Caja", wf.CONSULTA, False),
        ("FIR", "Firma de contrato", wf.CONSULTA, False),
        ("MNP", "MNP", wf.CIERRE_DIGITAL, True),
    ], 1):
        fases[ident] = m.Fase.create(proceso=proceso, identificador=ident, nombre=nombre,
                                     tipo=tipo, orden=orden, mostrar_en_qr=qr)
    flujo = m.FlujoAlternativo.create(proceso=proceso, codigo="EFE", nombre="Pago en efectivo")
    for posicion, ident in enumerate(["FIN", "CAJ", "FIR"], 1):
        m.FlujoAlternativoFase.create(flujo_alternativo=flujo, posicion=posicion, fase=fases[ident])
    usuario = m.Usuario.create(email="ana@unach.cl", nombre="Ana", rol="USUARIO_FASE")
    carrera = m.Carrera.create(codigo="MED", nombre="Medicina")
    postulante = m.Postulante.create(rut="11111111-1", nombres="Juan", apellidos="Pérez",
                                     correo_personal="juan@gmail.com", origen="INICIO_STAFF")
    m.PostulanteCarrera.create(postulante=postulante, preferencia=1, carrera=carrera)
    return {"proceso": proceso, "fases": {k: f.id for k, f in fases.items()}, "flujo": flujo.id,
            "usuario": usuario.id, "postulante": postulante}


def nuevo_expediente(esc, recorrido, numero=None, postulante=None):
    numero = numero or recorridos.siguiente_numero_atencion(esc["proceso"].id)
    expediente = m.Expediente.create(
        proceso=esc["proceso"], postulante=postulante or esc["postulante"], via_inicio="STAFF",
        iniciado_por=esc["usuario"], numero_atencion=numero, token_seguimiento=f"tok-{numero}",
    )
    recorridos.guardar(expediente, recorrido)
    return expediente


def fases_guardadas(expediente, esc):
    nombres = {v: k for k, v in esc["fases"].items()}
    return [
        (nombres[a.fase_id], a.estado)
        for a in m.Atencion.select().where(m.Atencion.expediente == expediente.id).order_by(m.Atencion.secuencia)
    ]


# ── Migraciones ─────────────────────────────────────────────────────


class TestMigraciones:
    def test_estan_aplicadas_y_no_se_repiten(self, base_de_pruebas):
        conexion = migrate.conectar(TestConfig)
        try:
            assert "001" in migrate.aplicadas(conexion)
            assert migrate.migrar(conexion, salida=lambda _m: None) == []
        finally:
            conexion.close()

    def test_nombres_de_scripts_validos(self, tmp_path):
        (tmp_path / "001_uno.sql").write_text("SELECT 1;")
        (tmp_path / "otro.sql").write_text("SELECT 1;")
        with pytest.raises(ValueError):
            migrate.scripts_disponibles(tmp_path)


# ── Restricciones del esquema ───────────────────────────────────────


class TestRestricciones:
    def test_un_solo_proceso_activo(self, escenario):
        with pytest.raises(IntegrityError):
            m.Proceso.create(codigo="OTRO", nombre="Otro", fecha_inicio=date(2027, 1, 1),
                             fecha_fin=date(2027, 2, 1), fecha_eliminacion=date(2027, 11, 30),
                             estado="ACTIVO")

    def test_una_sola_fase_inicial_por_proceso(self, escenario):
        with pytest.raises(IntegrityError):
            m.Fase.create(proceso=escenario["proceso"], identificador="INI2", nombre="Otra",
                          tipo=wf.INICIAL, orden=99)

    def test_una_sola_atencion_activa_por_expediente(self, escenario):
        est = recorridos.estructura_del_proceso(escenario["proceso"].id)
        expediente = nuevo_expediente(escenario, wf.Recorrido.iniciar_por_staff(est, escenario["usuario"], T0))
        with pytest.raises(IntegrityError):
            m.Atencion.create(expediente=expediente, fase=escenario["fases"]["BEC"], secuencia=9,
                              estado=wf.EN_ESPERA)

    def test_un_expediente_por_postulante_y_proceso(self, escenario):
        est = recorridos.estructura_del_proceso(escenario["proceso"].id)
        nuevo_expediente(escenario, wf.Recorrido.iniciar_por_staff(est, escenario["usuario"], T0))
        with pytest.raises(IntegrityError):
            nuevo_expediente(escenario, wf.Recorrido.iniciar_por_staff(est, escenario["usuario"], T0))

    def test_observacion_obligatoria_en_la_base(self, escenario):
        est = recorridos.estructura_del_proceso(escenario["proceso"].id)
        expediente = nuevo_expediente(escenario, wf.Recorrido.iniciar_por_staff(est, escenario["usuario"], T0))
        with pytest.raises(IntegrityError):
            m.Atencion.create(expediente=expediente, fase=escenario["fases"]["BEC"], secuencia=9,
                              estado=wf.COMPLETADO_CON_OBSERVACIONES)


# ── Persistencia del recorrido ──────────────────────────────────────


class TestRecorridos:
    def test_estructura_del_proceso(self, escenario):
        est = recorridos.estructura_del_proceso(escenario["proceso"].id)
        assert est.inicial.id == escenario["fases"]["INI"]
        assert est.mnp.id == escenario["fases"]["MNP"]
        f = escenario["fases"]
        assert est.flujo(escenario["flujo"]).fases == (f["FIN"], f["CAJ"], f["FIR"])
        assert [x.id for x in est.consultas_qr()] == [f["BEC"], f["FIN"]]

    def test_numero_de_atencion_correlativo(self, escenario):
        pid = escenario["proceso"].id
        assert [recorridos.siguiente_numero_atencion(pid) for _ in range(3)] == [1, 2, 3]

    def test_guardar_y_cargar_ida_y_vuelta(self, escenario):
        f = escenario["fases"]
        est = recorridos.estructura_del_proceso(escenario["proceso"].id)
        rec = wf.Recorrido.iniciar_por_staff(est, escenario["usuario"], T0, consultas=[f["BEC"]])
        expediente = nuevo_expediente(escenario, rec)
        assert fases_guardadas(expediente, escenario) == [
            ("INI", wf.COMPLETADO), ("BEC", wf.EN_ESPERA), ("MNP", wf.PLANIFICADA)]
        _, cargado = recorridos.cargar(expediente.id)
        assert [a.fase_id for a in cargado.ruta] == [f["INI"], f["BEC"], f["MNP"]]
        assert cargado.actual.en_espera_desde == T0
        assert cargado.proceso_estado == "ACTIVO"

    def test_recorrido_completo_hasta_matricula_digital(self, escenario):
        f, u = escenario["fases"], escenario["usuario"]
        est = recorridos.estructura_del_proceso(escenario["proceso"].id)
        expediente = nuevo_expediente(
            escenario, wf.Recorrido.iniciar_por_staff(est, u, T0, consultas=[f["BEC"]]))

        recorridos.ejecutar(expediente.id, lambda r: r.atender(u, minutos(2)))
        recorridos.ejecutar(expediente.id, lambda r: r.finalizar(
            u, minutos(6), consultas=[f["FIN"]], con_observaciones=True, observacion="Beca parcial"))
        recorridos.ejecutar(expediente.id, lambda r: r.atender(u, minutos(8)))
        recorridos.ejecutar(expediente.id, lambda r: r.derivar_ahora(f["BEC"], u, minutos(9)))
        assert fases_guardadas(expediente, escenario) == [
            ("INI", wf.COMPLETADO), ("BEC", wf.COMPLETADO_CON_OBSERVACIONES), ("FIN", wf.DERIVADA),
            ("BEC", wf.EN_ESPERA), ("FIN", wf.PLANIFICADA), ("MNP", wf.PLANIFICADA)]

        for t in (10, 20, 30):
            recorridos.ejecutar(expediente.id, lambda r, t=t: r.atender(u, minutos(t)))
            recorridos.ejecutar(expediente.id, lambda r, t=t: r.finalizar(u, minutos(t + 5)))

        expediente = m.Expediente.get_by_id(expediente.id)
        assert expediente.estado == wf.MATRICULADO
        assert expediente.tipo_cierre == wf.DIGITAL
        assert expediente.fase_cierre_id == f["MNP"]
        assert expediente.con_observaciones
        derivada = m.Atencion.get(m.Atencion.expediente == expediente.id, m.Atencion.secuencia == 4)
        assert derivada.derivada_desde.estado == wf.DERIVADA

    def test_flujo_alternativo_hasta_cierre_manual(self, escenario):
        f, u = escenario["fases"], escenario["usuario"]
        est = recorridos.estructura_del_proceso(escenario["proceso"].id)
        expediente = nuevo_expediente(escenario, wf.Recorrido.iniciar_por_staff(est, u, T0))
        recorridos.ejecutar(expediente.id, lambda r: r.atender(u, minutos(1)))
        recorridos.ejecutar(expediente.id, lambda r: r.derivar_a_flujo(escenario["flujo"], f["CAJ"], u, minutos(3)))
        assert fases_guardadas(expediente, escenario) == [
            ("INI", wf.COMPLETADO), ("MNP", wf.COMPLETADO), ("CAJ", wf.EN_ESPERA), ("FIR", wf.PLANIFICADA)]
        assert m.Expediente.get_by_id(expediente.id).flujo_alternativo_id == escenario["flujo"]

        recorridos.ejecutar(expediente.id, lambda r: r.finalizar(u, minutos(5), confirmar_sin_atender=True))
        recorridos.ejecutar(expediente.id, lambda r: r.atender(u, minutos(6)))
        recorridos.ejecutar(expediente.id, lambda r: r.finalizar(u, minutos(9), detalle_cierre="Boleta 123"))

        expediente = m.Expediente.get_by_id(expediente.id)
        assert (expediente.estado, expediente.tipo_cierre, expediente.fase_cierre_id) == (
            wf.MATRICULADO, wf.MANUAL, f["FIR"])
        final = m.Atencion.get(m.Atencion.expediente == expediente.id, m.Atencion.fase == f["FIR"])
        assert final.detalle_cierre == "Boleta 123"

    def test_las_atenciones_quitadas_se_eliminan(self, escenario):
        f, u = escenario["fases"], escenario["usuario"]
        est = recorridos.estructura_del_proceso(escenario["proceso"].id)
        expediente = nuevo_expediente(
            escenario, wf.Recorrido.iniciar_por_staff(est, u, T0, consultas=[f["BEC"], f["FIN"]]))
        recorridos.ejecutar(expediente.id, lambda r: r.corregir_ruta([], "Sin dudas", u, minutos(1)))
        assert fases_guardadas(expediente, escenario) == [
            ("INI", wf.COMPLETADO), ("BEC", wf.EN_ESPERA), ("MNP", wf.PLANIFICADA)]

    def test_version_desactualizada_responde_409(self, escenario):
        u = escenario["usuario"]
        est = recorridos.estructura_del_proceso(escenario["proceso"].id)
        expediente = nuevo_expediente(escenario, wf.Recorrido.iniciar_por_staff(est, u, T0))
        _, rec = recorridos.cargar(expediente.id, bloquear=False)
        version_vista = rec.actual.version
        recorridos.ejecutar(expediente.id, lambda r: r.atender(u, minutos(1)), version=version_vista)
        with pytest.raises(wf.ReglaVioladaError) as e:
            recorridos.ejecutar(expediente.id, lambda r: r.finalizar(u, minutos(2)), version=version_vista)
        assert e.value.codigo_http == 409

    def test_un_error_de_dominio_no_deja_cambios(self, escenario):
        u = escenario["usuario"]
        est = recorridos.estructura_del_proceso(escenario["proceso"].id)
        expediente = nuevo_expediente(escenario, wf.Recorrido.iniciar_por_staff(est, u, T0))

        def atender_y_fallar(r):
            r.atender(u, minutos(1))
            r.atender(u, minutos(2))  # segunda vez: regla violada

        with pytest.raises(wf.ReglaVioladaError):
            recorridos.ejecutar(expediente.id, atender_y_fallar)
        assert fases_guardadas(expediente, escenario)[-1] == ("MNP", wf.EN_ESPERA)

    def test_proceso_cerrado_es_solo_lectura(self, escenario):
        u = escenario["usuario"]
        est = recorridos.estructura_del_proceso(escenario["proceso"].id)
        expediente = nuevo_expediente(escenario, wf.Recorrido.iniciar_por_staff(est, u, T0))
        m.Proceso.update(estado="CERRADO").where(m.Proceso.id == escenario["proceso"].id).execute()
        with pytest.raises(wf.ReglaVioladaError):
            recorridos.ejecutar(expediente.id, lambda r: r.atender(u, minutos(1)))

    def test_expediente_inexistente_responde_404(self, bd):
        with pytest.raises(wf.ReglaVioladaError) as e:
            recorridos.cargar(999999)
        assert e.value.codigo_http == 404


# ── Concurrencia real (RNF-06): dos responsables presionan «Atender» a la vez ──


def test_bloqueo_del_expediente_entre_conexiones(base_de_pruebas):
    import threading
    import time

    from app.db import db

    # Datos confirmados (visibles para otras conexiones); se limpian al final.
    with db.atomic():
        proceso = m.Proceso.create(codigo="CONC", nombre="Concurrencia", fecha_inicio=date(2027, 1, 1),
                                   fecha_fin=date(2027, 3, 31), fecha_eliminacion=date(2027, 11, 30))
        ini = m.Fase.create(proceso=proceso, identificador="INI", nombre="Iniciado", tipo=wf.INICIAL, orden=1)
        m.Fase.create(proceso=proceso, identificador="MNP", nombre="MNP", tipo=wf.CIERRE_DIGITAL, orden=2)
        usuario = m.Usuario.create(email="conc@unach.cl", nombre="Conc", rol="ADMIN")
        postulante = m.Postulante.create(rut="22222222-2", nombres="Ana", apellidos="Soto",
                                         correo_personal="ana@gmail.com", origen="INICIO_STAFF")
        expediente = m.Expediente.create(proceso=proceso, postulante=postulante, via_inicio="STAFF",
                                         numero_atencion=1, token_seguimiento="tok-conc")
        est = recorridos.estructura_del_proceso(proceso.id)
        recorridos.guardar(expediente, wf.Recorrido.iniciar_por_staff(est, usuario.id, T0))
        m.Proceso.update(estado="ACTIVO").where(m.Proceso.id == proceso.id).execute()
    assert ini.id

    tiene_el_bloqueo = threading.Event()
    resultados = {}

    def primero():
        db.connect(reuse_if_open=True)
        try:
            def atender_lento(r):
                r.atender(usuario.id, minutos(1))
                tiene_el_bloqueo.set()
                time.sleep(0.5)
            recorridos.ejecutar(expediente.id, atender_lento)
            resultados["primero"] = "ok"
        finally:
            db.close()

    def segundo():
        tiene_el_bloqueo.wait(5)
        db.connect(reuse_if_open=True)
        try:
            recorridos.ejecutar(expediente.id, lambda r: r.atender(usuario.id, minutos(1)))
            resultados["segundo"] = "ok"
        except wf.ReglaVioladaError as e:
            resultados["segundo"] = e.mensaje
        finally:
            db.close()

    try:
        hilos = [threading.Thread(target=primero), threading.Thread(target=segundo)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(10)
        assert resultados["primero"] == "ok"
        assert resultados["segundo"] == "El postulante ya está siendo atendido"
        assert m.Atencion.select().where(
            (m.Atencion.expediente == expediente.id) & (m.Atencion.estado == wf.EN_ATENCION)).count() == 1
    finally:
        with db.atomic():
            m.Expediente.delete().where(m.Expediente.id == expediente.id).execute()
            m.Postulante.delete().where(m.Postulante.id == postulante.id).execute()
            m.Proceso.delete().where(m.Proceso.id == proceso.id).execute()
            m.Usuario.delete().where(m.Usuario.id == usuario.id).execute()
