"""Bandeja de trabajo (RF-17) y acciones sobre la atención actual: atender, finalizar y
derivar (RF-13, RF-14), abandono (RF-28) y flujos alternativos (RF-29)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app import models as m
from app.services import bandeja, inicio
from app.services import configuracion as cfg
from app.services import workflow as wf
from app.services.rut import calcular_dv
from app.services.workflow import ReglaVioladaError
from tests.test_inicio import admin, armar_proceso, auditorias, ctx, datos, fase, ruta_de  # noqa: F401

T0 = datetime(2027, 1, 15, 13, 0, tzinfo=timezone.utc)  # 10:00 en Chile (horario de verano)


@pytest.fixture
def proceso(admin):
    return armar_proceso(admin)


def rut(cuerpo):
    return f"{cuerpo}-{calcular_dv(str(cuerpo))}"


def iniciar(por, cuerpo=12345678, consultas=(), ahora=T0, **cambios):
    """Inicio por staff con la ruta dada (identificadores de fase de consulta)."""
    proceso = inicio.proceso_activo()
    ruta = {"consultas": [fase(proceso, c).id for c in consultas]}
    return inicio.iniciar_por_staff(datos(rut=rut(cuerpo), **cambios), ruta, por, ahora=ahora)


def version(expediente):
    actual = (m.Atencion.select()
              .where((m.Atencion.expediente == expediente.id) & m.Atencion.estado.in_(wf.ESTADOS_ACTIVOS))
              .get())
    return actual.version


def finalizar(expediente, usuario, ahora=T0, **form):
    form = {"version": str(version(expediente)), "accion": "seguir", "resultado": "completado", **form}
    return bandeja.finalizar(expediente.id, form, usuario, ahora=ahora)


def orden(proceso, *identificadores):
    return {f"orden_{fase(proceso, i).id}": str(n) for n, i in enumerate(identificadores, 1)}


def usuario_de(proceso, *identificadores, email="bec@unach.cl"):
    usuario = m.Usuario.create(email=email, nombre="Beatriz Becas", rol="USUARIO_FASE", must_change_password=False)
    for identificador in identificadores:
        m.FaseResponsable.create(fase=fase(proceso, identificador), usuario=usuario)
    return usuario


def numeros(filas):
    return [f["numero"] for f in filas]


# ── Qué fases ve cada usuario ──

def test_fases_del_usuario(admin, proceso):
    becas = usuario_de(proceso, "BEC")
    assert [f.identificador for f in bandeja.fases_del_usuario(becas, proceso)] == ["BEC"]
    assert len(bandeja.fases_del_usuario(admin, proceso)) == 5
    assert bandeja.puede_ver(becas, proceso) and bandeja.puede_ver(admin, proceso)


def test_usuario_de_fase_de_otro_proceso_no_ve_la_bandeja(admin, proceso):
    otro = armar_proceso(admin, activar=False, codigo="MAT2028")
    ajeno = usuario_de(otro, "BEC", email="ajeno@unach.cl")
    assert bandeja.fases_del_usuario(ajeno, proceso) == []
    assert not bandeja.puede_ver(ajeno, proceso)


# ── Listas de la bandeja ──

def test_en_espera_ordenados_por_llegada_con_tiempo_de_espera(admin, proceso):
    iniciar(admin, 11111111, ["BEC"], ahora=T0 + timedelta(minutes=5), nombres="Ana")
    iniciar(admin, 22222222, ["BEC"], ahora=T0, nombres="Bruno")
    iniciar(admin, 33333333, [], ahora=T0)  # va a MNP: no está en Becas
    becas = usuario_de(proceso, "BEC")
    listas = bandeja.listar(becas, proceso, ahora=T0 + timedelta(minutes=72))
    assert numeros(listas["en_espera"]) == ["M-0002", "M-0001"]
    primera = listas["en_espera"][0]
    assert primera["nombre"] == "Bruno Pérez Soto" and primera["fase"] == "Becas"
    assert primera["viene_de"] == "Iniciado" and primera["espera"] == "1 h 12 min"
    assert listas["en_espera"][1]["espera"] == "1 h 07 min"
    assert listas["en_atencion"] == []


def test_en_atencion_muestra_quien_atiende_y_desde_que_hora(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    becas = usuario_de(proceso, "BEC")
    bandeja.atender(expediente.id, version(expediente), becas, ahora=T0 + timedelta(minutes=3))
    listas = bandeja.listar(becas, proceso, ahora=T0 + timedelta(minutes=10))
    assert listas["en_espera"] == []
    fila = listas["en_atencion"][0]
    assert fila["atendido_por"] == "Beatriz Becas" and fila["desde"] == "10:03"


def test_proximos_son_los_que_tienen_la_fase_como_siguiente(admin, proceso):
    iniciar(admin, 11111111, ["BEC", "FIN"])
    iniciar(admin, 22222222, ["FIN", "BEC"])
    finanzas = usuario_de(proceso, "FIN", email="fin@unach.cl")
    listas = bandeja.listar(finanzas, proceso, ahora=T0)
    assert numeros(listas["en_espera"]) == ["M-0002"]
    assert numeros(listas["proximos"]) == ["M-0001"]
    assert listas["proximos"][0]["actual"] == "Becas"


def test_iniciado_ve_en_espera_a_los_del_qr_con_su_ruta_propuesta(admin, proceso):
    inicio.iniciar_por_qr(datos(), [str(fase(proceso, "BEC").id)], ahora=T0)
    responsable = m.Usuario.get(m.Usuario.email == "ini-MAT2027@unach.cl")
    fila = bandeja.listar(responsable, proceso, fase_id=fase(proceso, "INI").id, ahora=T0)["en_espera"][0]
    assert fila["via_qr"] and fila["ruta_propuesta"] == ["Becas", "MNP"]
    assert fila["viene_de"] is None


def test_filtro_por_fase_carrera_y_fecha(admin, proceso):
    iniciar(admin, 11111111, ["BEC"], carrera1="ENF")
    iniciar(admin, 22222222, ["BEC"], carrera1="KIN", carrera2="ENF")
    iniciar(admin, 33333333, ["BEC"], carrera1="DER", ahora=T0 + timedelta(days=1))
    todas = bandeja.listar(admin, proceso, ahora=T0 + timedelta(days=1))
    assert len(todas["en_espera"]) == 3
    assert numeros(bandeja.listar(admin, proceso, carrera="ENF")["en_espera"]) == ["M-0001", "M-0002"]
    assert numeros(bandeja.listar(admin, proceso, fecha=date(2027, 1, 16))["en_espera"]) == ["M-0003"]
    assert bandeja.listar(admin, proceso, fase_id=fase(proceso, "FIN").id)["en_espera"] == []


def test_filtro_por_una_fase_ajena_no_muestra_nada(admin, proceso):
    iniciar(admin, consultas=["FIN"])
    becas = usuario_de(proceso, "BEC")
    assert bandeja.listar(becas, proceso, fase_id=fase(proceso, "FIN").id)["en_espera"] == []


def test_el_abandono_sale_de_la_bandeja(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    bandeja.marcar_abandono(expediente.id, version(expediente), admin, ahora=T0)
    assert bandeja.listar(admin, proceso)["en_espera"] == []


# ── Atender ──

def test_atender(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    becas = usuario_de(proceso, "BEC")
    mensaje = bandeja.atender(expediente.id, version(expediente), becas, ahora=T0)
    assert "M-0001" in mensaje
    assert ruta_de(expediente)[1] == ("BEC", wf.EN_ATENCION)
    registro = auditorias("ATENDER")[0]
    assert registro.usuario_id == becas.id and registro.valor_nuevo["fase_actual"] == "Becas"


def test_atender_dos_veces_o_con_version_vieja_responde_409(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    vieja = version(expediente)
    bandeja.atender(expediente.id, vieja, admin, ahora=T0)
    with pytest.raises(ReglaVioladaError) as error:
        bandeja.atender(expediente.id, vieja, admin, ahora=T0)
    assert error.value.codigo_http == 409


def test_solo_actua_el_responsable_de_la_fase_actual(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC", "FIN"])
    finanzas = usuario_de(proceso, "FIN", email="fin@unach.cl")  # está en la ruta, pero no es la actual
    for accion in (lambda: bandeja.atender(expediente.id, version(expediente), finanzas),
                   lambda: finalizar(expediente, finanzas, sin_atender="on"),
                   lambda: bandeja.marcar_abandono(expediente.id, version(expediente), finanzas)):
        with pytest.raises(ReglaVioladaError) as error:
            accion()
        assert error.value.codigo_http == 403
    assert ruta_de(expediente)[1] == ("BEC", wf.EN_ESPERA)


def test_sin_version_no_se_actua(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    with pytest.raises(ReglaVioladaError):
        bandeja.atender(expediente.id, None, admin)


# ── Finalizar y derivar ──

def test_finalizar_con_la_ruta_modificada(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    bandeja.atender(expediente.id, version(expediente), admin, ahora=T0)
    mensaje = finalizar(expediente, admin, ahora=T0 + timedelta(minutes=8), **orden(proceso, "FIN", "BEC"))
    assert mensaje == "M-0001 debe dirigirse a: Finanzas"
    assert ruta_de(expediente) == [("INI", wf.COMPLETADO), ("BEC", wf.COMPLETADO),
                                   ("FIN", wf.EN_ESPERA), ("BEC", wf.PLANIFICADA), ("MNP", wf.PLANIFICADA)]
    registro = auditorias("FINALIZAR_ATENCION")[0]
    assert registro.valor_anterior["fase_actual"] == "Becas"
    assert registro.valor_nuevo["fase_actual"] == "Finanzas" and registro.valor_nuevo["origen"] == "Becas"
    assert registro.valor_nuevo["ruta"] == ["Becas", "MNP"]


def test_finalizar_sin_fases_va_directo_a_mnp(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC", "FIN"])
    finalizar(expediente, admin, sin_atender="on")  # sin orden_*: se quitan las consultas
    assert [f for f, e in ruta_de(expediente) if e == wf.PLANIFICADA] == []
    assert ruta_de(expediente)[-1] == ("MNP", wf.EN_ESPERA)


def test_finalizar_exige_resultado_y_texto_de_observacion(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    bandeja.atender(expediente.id, version(expediente), admin, ahora=T0)
    with pytest.raises(ReglaVioladaError, match="resultado"):
        finalizar(expediente, admin, resultado="")
    with pytest.raises(ReglaVioladaError, match="observación"):
        finalizar(expediente, admin, resultado="observaciones", observacion="  ")
    finalizar(expediente, admin, resultado="observaciones", observacion="Falta certificado")
    atencion = m.Atencion.get((m.Atencion.expediente == expediente.id) & (m.Atencion.secuencia == 2))
    assert atencion.estado == wf.COMPLETADO_CON_OBSERVACIONES and atencion.observacion == "Falta certificado"


def test_finalizar_sin_atender_exige_confirmacion(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    with pytest.raises(ReglaVioladaError, match="Atender"):
        finalizar(expediente, admin)
    finalizar(expediente, admin, sin_atender="on")
    atencion = m.Atencion.get((m.Atencion.expediente == expediente.id) & (m.Atencion.secuencia == 2))
    assert atencion.sin_tiempo_atencion


def test_finalizar_mnp_matricula(admin, proceso):
    expediente = iniciar(admin)
    mensaje = finalizar(expediente, admin, sin_atender="on")
    assert mensaje == "M-0001 quedó Matriculado"
    expediente = m.Expediente.get_by_id(expediente.id)
    assert expediente.estado == wf.MATRICULADO and expediente.tipo_cierre == wf.DIGITAL
    assert auditorias("FINALIZAR_ATENCION")[0].valor_nuevo["estado"] == wf.MATRICULADO


def test_iniciado_confirma_la_ruta_propuesta_del_qr(admin, proceso):
    expediente = inicio.iniciar_por_qr(datos(), [str(fase(proceso, "BEC").id)], ahora=T0)
    responsable = m.Usuario.get(m.Usuario.email == "ini-MAT2027@unach.cl")
    detalle = bandeja.detalle(expediente.id, responsable)
    assert detalle["orden"] == {fase(proceso, "BEC").id: 1}  # preseleccionada
    bandeja.atender(expediente.id, version(expediente), responsable, ahora=T0)
    finalizar(expediente, responsable, **orden(proceso, "BEC", "FIN"))
    assert ruta_de(expediente) == [("INI", wf.COMPLETADO), ("BEC", wf.EN_ESPERA),
                                   ("FIN", wf.PLANIFICADA), ("MNP", wf.PLANIFICADA)]


def test_orden_repetido_o_invalido(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    with pytest.raises(ReglaVioladaError, match="No repita"):
        finalizar(expediente, admin, sin_atender="on", **{**orden(proceso, "FIN"), **orden(proceso, "CAJA")})
    with pytest.raises(ReglaVioladaError, match="número"):
        finalizar(expediente, admin, sin_atender="on", **{f"orden_{fase(proceso, 'FIN').id}": "x"})


def test_derivar_ahora_desde_mnp_y_volver(admin, proceso):
    expediente = iniciar(admin)
    bandeja.atender(expediente.id, version(expediente), admin, ahora=T0)
    mensaje = finalizar(expediente, admin, accion="derivar", resultado="", destino=str(fase(proceso, "FIN").id))
    assert mensaje == "M-0001 debe dirigirse a: Finanzas"
    assert ruta_de(expediente)[1:] == [("MNP", wf.DERIVADA), ("FIN", wf.EN_ESPERA), ("MNP", wf.PLANIFICADA)]
    registro = auditorias("DERIVAR_AHORA")[0]
    assert registro.valor_nuevo["origen"] == "MNP" and registro.valor_nuevo["fase_actual"] == "Finanzas"


def test_derivar_ahora_exige_destino(admin, proceso):
    expediente = iniciar(admin)
    with pytest.raises(ReglaVioladaError, match="fase de destino"):
        finalizar(expediente, admin, accion="derivar", destino="")


def test_derivar_a_flujo_alternativo(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    bandeja.atender(expediente.id, version(expediente), admin, ahora=T0)
    entrada = f"{m.FlujoAlternativo.get().id}:{fase(proceso, 'FIN').id}"
    mensaje = finalizar(expediente, admin, accion="flujo", entrada=entrada)
    assert mensaje == "M-0001 debe dirigirse a: Finanzas (Pago en efectivo)"
    assert ruta_de(expediente) == [("INI", wf.COMPLETADO), ("BEC", wf.COMPLETADO),
                                   ("FIN", wf.EN_ESPERA), ("CAJA", wf.PLANIFICADA)]
    registro = auditorias("DERIVAR_A_FLUJO")[0]
    assert registro.valor_nuevo["flujo"] == "Pago en efectivo" and registro.valor_nuevo["quitadas"] == ["MNP"]


def test_derivar_a_flujo_interrumpiendo_la_atencion(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    bandeja.atender(expediente.id, version(expediente), admin, ahora=T0)
    entrada = f"{m.FlujoAlternativo.get().id}:{fase(proceso, 'CAJA').id}"
    finalizar(expediente, admin, accion="flujo", entrada=entrada, interrumpir="on", resultado="")
    assert ruta_de(expediente)[1:] == [("BEC", wf.DERIVADA), ("CAJA", wf.EN_ESPERA)]


def _en_flujo(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    entrada = f"{m.FlujoAlternativo.get().id}:{fase(proceso, 'FIN').id}"
    finalizar(expediente, admin, accion="flujo", entrada=entrada, sin_atender="on")
    return expediente


def test_flujo_alternativo_sigue_su_secuencia_y_cierra_manual(admin, proceso):
    expediente = _en_flujo(admin, proceso)
    detalle = bandeja.detalle(expediente.id, admin)
    assert detalle["flujo"] == "Pago en efectivo" and detalle["siguiente"] == "Caja"
    assert not detalle["final_manual"]
    assert finalizar(expediente, admin, sin_atender="on") == "M-0001 debe dirigirse a: Caja (Pago en efectivo)"

    assert bandeja.detalle(expediente.id, admin)["final_manual"]
    with pytest.raises(ReglaVioladaError, match="detalle"):
        finalizar(expediente, admin, sin_atender="on", confirmar_cierre="on")
    with pytest.raises(ReglaVioladaError, match="Confirme"):
        finalizar(expediente, admin, sin_atender="on", detalle_cierre="Boleta 123")
    mensaje = finalizar(expediente, admin, sin_atender="on", detalle_cierre="Boleta 123", confirmar_cierre="on")
    assert mensaje == "M-0001 quedó Matriculado (cierre manual)"
    expediente = m.Expediente.get_by_id(expediente.id)
    assert expediente.estado == wf.MATRICULADO and expediente.tipo_cierre == wf.MANUAL
    assert auditorias("CIERRE_MANUAL")[0].valor_nuevo["detalle_cierre"] == "Boleta 123"


def test_en_flujo_alternativo_no_se_cambia_la_ruta(admin, proceso):
    expediente = _en_flujo(admin, proceso)
    finalizar(expediente, admin, sin_atender="on", **orden(proceso, "BEC"))  # se ignora: secuencia fija
    assert ruta_de(expediente)[-1] == ("CAJA", wf.EN_ESPERA)
    with pytest.raises(ReglaVioladaError):
        finalizar(expediente, admin, accion="derivar", destino=str(fase(proceso, "BEC").id))


def test_devolver_al_flujo_principal(admin, proceso):
    expediente = _en_flujo(admin, proceso)
    with pytest.raises(ReglaVioladaError, match="motivo"):
        finalizar(expediente, admin, accion="devolver", sin_atender="on", motivo="")
    mensaje = finalizar(expediente, admin, accion="devolver", sin_atender="on", motivo="Pagará con tarjeta",
                        **orden(proceso, "BEC"))
    assert mensaje == "M-0001 debe dirigirse a: Becas"
    assert ruta_de(expediente)[-2:] == [("BEC", wf.EN_ESPERA), ("MNP", wf.PLANIFICADA)]
    assert m.Expediente.get_by_id(expediente.id).flujo_alternativo_id is None
    assert auditorias("DEVOLVER_A_PRINCIPAL")[0].valor_nuevo["motivo"] == "Pagará con tarjeta"


def test_accion_desconocida(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    with pytest.raises(ReglaVioladaError):
        finalizar(expediente, admin, accion="borrar")


# ── Abandono ──

def test_abandono_solo_en_espera(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    bandeja.atender(expediente.id, version(expediente), admin, ahora=T0)
    with pytest.raises(ReglaVioladaError, match="en espera"):
        bandeja.marcar_abandono(expediente.id, version(expediente), admin, ahora=T0)


def test_abandono(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    becas = usuario_de(proceso, "BEC")
    assert "M-0001" in bandeja.marcar_abandono(expediente.id, version(expediente), becas, ahora=T0)
    assert m.Expediente.get_by_id(expediente.id).estado == wf.ABANDONO
    assert auditorias("ABANDONO")[0].valor_nuevo["estado"] == wf.ABANDONO


# ── Proceso cerrado ──

def test_proceso_cerrado_no_admite_acciones(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    vieja = version(expediente)
    cfg.cerrar_proceso(proceso.id, admin)
    with pytest.raises(ReglaVioladaError):
        bandeja.atender(expediente.id, vieja, admin)
