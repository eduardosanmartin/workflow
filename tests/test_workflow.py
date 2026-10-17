from datetime import datetime, timedelta

import pytest

from app.services.workflow import (
    ABANDONADA,
    ABANDONO,
    CIERRE_DIGITAL,
    COMPLETADO,
    COMPLETADO_CON_OBSERVACIONES,
    CONSULTA,
    DERIVADA,
    DIGITAL,
    EN_ATENCION,
    EN_CURSO,
    EN_ESPERA,
    INICIAL,
    MANUAL,
    MATRICULADO,
    PLANIFICADA,
    PROCESO_PENDIENTE,
    Estructura,
    Fase,
    FlujoAlternativo,
    Recorrido,
    ReglaVioladaError,
    puede_actuar,
    tiempo_atencion,
    tiempo_espera,
    validar_activacion,
    validar_cambio_estructura,
)

T0 = datetime(2027, 1, 10, 9, 0)

INI, BEC, BEN, FIN, CAJ, FIR, MNP = 1, 2, 3, 4, 5, 6, 7
EFECTIVO = 10


def minutos(n):
    return T0 + timedelta(minutes=n)


@pytest.fixture
def est():
    fases = [
        Fase(INI, "Iniciado", INICIAL),
        Fase(BEC, "Becas", CONSULTA),
        Fase(BEN, "Beneficios", CONSULTA),
        Fase(FIN, "Finanzas", CONSULTA),
        Fase(CAJ, "Caja", CONSULTA, mostrar_en_qr=False),
        Fase(FIR, "Firma de contrato", CONSULTA, mostrar_en_qr=False),
        Fase(MNP, "MNP", CIERRE_DIGITAL),
    ]
    flujos = [FlujoAlternativo(EFECTIVO, "Pago en efectivo", (FIN, CAJ, FIR))]
    return Estructura(fases, flujos)


def fases_de(rec):
    return [(a.fase_id, a.estado) for a in rec.ruta]


def atender_y_finalizar(rec, t, **kw):
    rec.atender("ana", minutos(t))
    rec.finalizar("ana", minutos(t + 5), **kw)


# ── Estructura y activación ─────────────────────────────────────────


class TestEstructura:
    def test_identifica_fases_de_sistema(self, est):
        assert est.inicial.id == INI
        assert est.mnp.id == MNP

    def test_fases_ofrecidas_en_qr(self, est):
        assert [f.id for f in est.consultas_qr()] == [BEC, BEN, FIN]

    def test_flujo_sin_fases_es_invalido(self, est):
        flujos = [FlujoAlternativo(11, "Vacío", ())]
        errores = validar_activacion(Estructura(est.fases, flujos), fases_con_responsable={1, 2, 3, 4, 5, 6, 7})
        assert any("Vacío" in e for e in errores)

    def test_flujo_con_fase_repetida_es_invalido(self, est):
        flujos = [FlujoAlternativo(11, "Repetido", (FIN, FIN))]
        errores = validar_activacion(Estructura(est.fases, flujos), fases_con_responsable={1, 2, 3, 4, 5, 6, 7})
        assert any("repite" in e for e in errores)

    def test_flujo_solo_con_fases_de_consulta(self, est):
        flujos = [FlujoAlternativo(11, "Con MNP", (FIN, MNP))]
        errores = validar_activacion(Estructura(est.fases, flujos), fases_con_responsable={1, 2, 3, 4, 5, 6, 7})
        assert any("consulta" in e for e in errores)

    def test_activacion_valida(self, est):
        assert validar_activacion(est, fases_con_responsable={1, 2, 3, 4, 5, 6, 7}) == []

    def test_activacion_exige_responsables(self, est):
        errores = validar_activacion(est, fases_con_responsable={1, 2, 3, 4, 5, 6})
        assert any("MNP" in e for e in errores)

    def test_activacion_exige_una_inicial_y_una_mnp(self):
        estructura = Estructura([Fase(2, "Becas", CONSULTA)], [])
        errores = validar_activacion(estructura, fases_con_responsable={2})
        assert any("inicial" in e for e in errores)
        assert any("MNP" in e or "cierre digital" in e for e in errores)

    def test_bloqueo_de_estructura(self):
        validar_cambio_estructura(proceso_iniciado=False)
        with pytest.raises(ReglaVioladaError) as e:
            validar_cambio_estructura(proceso_iniciado=True)
        assert e.value.codigo_http == 409


class TestPermisos:
    def test_admin_y_gestor_actuan_en_cualquier_fase(self):
        assert puede_actuar("ADMIN", set(), BEC)
        assert puede_actuar("GESTOR", set(), BEC)

    def test_usuario_de_fase_solo_en_sus_fases(self):
        assert puede_actuar("USUARIO_FASE", {BEC}, BEC)
        assert not puede_actuar("USUARIO_FASE", {BEC}, FIN)

    def test_rol_desconocido(self):
        assert not puede_actuar("OTRO", {BEC}, BEC)


# ── Inicio de proceso ───────────────────────────────────────────────


class TestInicio:
    def test_staff_ruta_directa(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0)
        assert fases_de(rec) == [(INI, COMPLETADO), (MNP, EN_ESPERA)]
        assert rec.fase_actual.id == MNP
        assert rec.fase_siguiente is None
        assert rec.actual.en_espera_desde == T0

    def test_staff_ruta_con_consultas(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC, FIN])
        assert fases_de(rec) == [(INI, COMPLETADO), (BEC, EN_ESPERA), (FIN, PLANIFICADA), (MNP, PLANIFICADA)]
        assert rec.fase_siguiente.id == FIN

    def test_staff_no_acepta_fases_de_sistema_como_consulta(self, est):
        with pytest.raises(ReglaVioladaError):
            Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[MNP])
        with pytest.raises(ReglaVioladaError):
            Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[INI])

    def test_staff_puede_elegir_flujo_alternativo(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, flujo_id=EFECTIVO, fase_entrada_id=CAJ)
        assert fases_de(rec) == [(INI, COMPLETADO), (CAJ, EN_ESPERA), (FIR, PLANIFICADA)]
        assert rec.flujo_alternativo_id == EFECTIVO

    def test_qr_queda_en_espera_en_iniciado_con_ruta_propuesta(self, est):
        rec = Recorrido.iniciar_por_qr(est, T0, consultas=[BEC])
        assert fases_de(rec) == [(INI, EN_ESPERA), (BEC, PLANIFICADA), (MNP, PLANIFICADA)]

    def test_qr_solo_acepta_fases_ofrecidas_en_qr(self, est):
        with pytest.raises(ReglaVioladaError):
            Recorrido.iniciar_por_qr(est, T0, consultas=[CAJ])

    def test_qr_confirmar_o_ajustar_ruta_en_iniciado(self, est):
        rec = Recorrido.iniciar_por_qr(est, T0, consultas=[BEC])
        atender_y_finalizar(rec, 1, consultas=[BEN, FIN])
        assert fases_de(rec) == [(INI, COMPLETADO), (BEN, EN_ESPERA), (FIN, PLANIFICADA), (MNP, PLANIFICADA)]


# ── Atención y derivación en el flujo principal ─────────────────────


class TestAtencion:
    def test_atender_registra_inicio_y_responsable(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        rec.atender("beto", minutos(4))
        assert rec.actual.estado == EN_ATENCION
        assert rec.actual.inicio_atencion == minutos(4)
        assert rec.actual.atendido_por == "beto"

    def test_no_se_atiende_dos_veces(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0)
        rec.atender("beto", minutos(1))
        with pytest.raises(ReglaVioladaError):
            rec.atender("beto", minutos(2))

    def test_finalizar_pasa_la_siguiente_a_espera(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        atender_y_finalizar(rec, 1)
        assert fases_de(rec) == [(INI, COMPLETADO), (BEC, COMPLETADO), (MNP, EN_ESPERA)]
        assert rec.actual.en_espera_desde == minutos(6)

    def test_tiempos_de_espera_y_atencion(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        atender_y_finalizar(rec, 3)
        becas = rec.ruta[1]
        assert tiempo_espera(becas) == timedelta(minutes=3)
        assert tiempo_atencion(becas) == timedelta(minutes=5)

    def test_finalizar_sin_atender_requiere_confirmacion(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        with pytest.raises(ReglaVioladaError):
            rec.finalizar("beto", minutos(5))
        rec.finalizar("beto", minutos(5), confirmar_sin_atender=True)
        becas = rec.ruta[1]
        assert becas.sin_tiempo_atencion
        assert becas.inicio_atencion == becas.fin_atencion == minutos(5)

    def test_observacion_obligatoria(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        rec.atender("beto", minutos(1))
        with pytest.raises(ReglaVioladaError) as e:
            rec.finalizar("beto", minutos(2), con_observaciones=True, observacion="  ")
        assert e.value.codigo_http == 400
        rec.finalizar("beto", minutos(2), con_observaciones=True, observacion="Falta certificado")
        assert rec.ruta[1].estado == COMPLETADO_CON_OBSERVACIONES
        assert rec.con_observaciones

    def test_al_finalizar_se_puede_modificar_la_ruta(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC, BEN])
        atender_y_finalizar(rec, 1, consultas=[FIN])
        assert fases_de(rec)[2:] == [(FIN, EN_ESPERA), (MNP, PLANIFICADA)]
        assert {a.fase_id for a in rec.quitadas} == {BEN, MNP}

    def test_ruta_vacia_va_directo_a_mnp(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC, BEN])
        atender_y_finalizar(rec, 1, consultas=[])
        assert fases_de(rec)[2:] == [(MNP, EN_ESPERA)]

    def test_una_consulta_puede_repetirse(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[FIN, BEC, FIN])
        assert [a.fase_id for a in rec.ruta] == [INI, FIN, BEC, FIN, MNP]

    def test_derivar_ahora_desde_mnp(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0)
        rec.atender("beto", minutos(1))
        rec.derivar_ahora(FIN, "beto", minutos(3))
        assert fases_de(rec) == [(INI, COMPLETADO), (MNP, DERIVADA), (FIN, EN_ESPERA), (MNP, PLANIFICADA)]
        assert rec.ruta[2].derivada_desde is rec.ruta[1]

    def test_derivar_ahora_solo_a_consultas(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        with pytest.raises(ReglaVioladaError):
            rec.derivar_ahora(MNP, "beto", minutos(1))

    def test_no_se_deriva_ahora_desde_iniciado(self, est):
        rec = Recorrido.iniciar_por_qr(est, T0)
        with pytest.raises(ReglaVioladaError):
            rec.derivar_ahora(BEC, "ana", minutos(1))

    def test_completar_mnp_matricula_con_cierre_digital(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        atender_y_finalizar(rec, 1)
        atender_y_finalizar(rec, 10)
        assert rec.estado == MATRICULADO
        assert rec.tipo_cierre == DIGITAL
        assert rec.fase_cierre_id == MNP
        assert rec.matriculado_en == minutos(15)
        assert rec.actual is None

    def test_mnp_no_acepta_nueva_ruta(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0)
        rec.atender("beto", minutos(1))
        with pytest.raises(ReglaVioladaError):
            rec.finalizar("beto", minutos(2), consultas=[BEC])

    def test_matriculado_con_observaciones(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        atender_y_finalizar(rec, 1, con_observaciones=True, observacion="Beca condicionada")
        atender_y_finalizar(rec, 10)
        assert rec.estado == MATRICULADO
        assert rec.con_observaciones

    def test_no_se_actua_sobre_expediente_matriculado(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0)
        atender_y_finalizar(rec, 1)
        with pytest.raises(ReglaVioladaError):
            rec.atender("beto", minutos(10))

    def test_proceso_no_activo_es_solo_lectura(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0)
        rec.proceso_estado = "CERRADO"
        with pytest.raises(ReglaVioladaError):
            rec.atender("beto", minutos(1))

    def test_corregir_ruta(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC, BEN])
        rec.corregir_ruta([FIN], motivo="Error de digitación", usuario="gestor", ahora=minutos(1))
        assert fases_de(rec)[1:] == [(BEC, EN_ESPERA), (FIN, PLANIFICADA), (MNP, PLANIFICADA)]

    def test_corregir_ruta_exige_motivo(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        with pytest.raises(ReglaVioladaError):
            rec.corregir_ruta([FIN], motivo="", usuario="gestor", ahora=minutos(1))


# ── Abandono y reactivación ─────────────────────────────────────────


class TestAbandono:
    def test_marcar_abandono(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        rec.marcar_abandono("beto", minutos(20))
        assert rec.estado == ABANDONO
        assert rec.ruta[1].estado == ABANDONADA
        assert rec.actual is None
        assert rec.fase_siguiente is None

    def test_solo_se_abandona_en_espera(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        rec.atender("beto", minutos(1))
        with pytest.raises(ReglaVioladaError):
            rec.marcar_abandono("beto", minutos(2))

    def test_reactivar_en_la_misma_fase_conserva_la_ruta(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC, FIN])
        rec.marcar_abandono("beto", minutos(20))
        rec.reactivar("beto", minutos(60))
        assert rec.estado == EN_CURSO
        assert fases_de(rec)[1:] == [(BEC, ABANDONADA), (BEC, EN_ESPERA), (FIN, PLANIFICADA), (MNP, PLANIFICADA)]
        assert rec.actual.en_espera_desde == minutos(60)

    def test_reactivar_en_otra_fase(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0)
        rec.marcar_abandono("beto", minutos(20))
        rec.reactivar("beto", minutos(60), fase_id=FIN)
        assert fases_de(rec)[1:] == [(MNP, ABANDONADA), (FIN, EN_ESPERA), (MNP, PLANIFICADA)]

    def test_solo_se_reactiva_un_abandono(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0)
        with pytest.raises(ReglaVioladaError):
            rec.reactivar("beto", minutos(1))


# ── Flujos alternativos ─────────────────────────────────────────────


class TestFlujoAlternativo:
    def test_derivar_desde_consulta_en_la_fase_de_entrada(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        rec.atender("beto", minutos(1))
        rec.derivar_a_flujo(EFECTIVO, FIN, "beto", minutos(5))
        assert fases_de(rec) == [(INI, COMPLETADO), (BEC, COMPLETADO), (FIN, EN_ESPERA), (CAJ, PLANIFICADA), (FIR, PLANIFICADA)]
        assert rec.flujo_alternativo_id == EFECTIVO
        assert any(a.fase_id == MNP for a in rec.quitadas)

    def test_derivar_desde_mnp_entrando_a_mitad_del_flujo(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0)
        rec.atender("beto", minutos(1))
        rec.derivar_a_flujo(EFECTIVO, CAJ, "beto", minutos(5))
        assert fases_de(rec)[1:] == [(MNP, COMPLETADO), (CAJ, EN_ESPERA), (FIR, PLANIFICADA)]

    def test_derivar_desde_iniciado_por_qr(self, est):
        rec = Recorrido.iniciar_por_qr(est, T0, consultas=[BEC])
        rec.atender("ana", minutos(1))
        rec.derivar_a_flujo(EFECTIVO, FIN, "ana", minutos(3))
        assert fases_de(rec) == [(INI, COMPLETADO), (FIN, EN_ESPERA), (CAJ, PLANIFICADA), (FIR, PLANIFICADA)]

    def test_derivar_interrumpiendo_deja_la_atencion_derivada(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        rec.atender("beto", minutos(1))
        rec.derivar_a_flujo(EFECTIVO, FIN, "beto", minutos(5), interrumpir=True)
        assert rec.ruta[1].estado == DERIVADA

    def test_derivar_desde_espera_deja_la_atencion_derivada(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        rec.derivar_a_flujo(EFECTIVO, FIN, "beto", minutos(5))
        assert rec.ruta[1].estado == DERIVADA

    def test_flujo_inactivo_no_admite_entradas(self, est):
        inactivo = FlujoAlternativo(11, "Crédito externo", (FIN, FIR), activo=False)
        estructura = Estructura(est.fases, est.flujos + [inactivo])
        rec = Recorrido.iniciar_por_staff(estructura, "ana", T0)
        with pytest.raises(ReglaVioladaError):
            rec.derivar_a_flujo(11, FIN, "beto", minutos(1))

    def test_fase_de_entrada_debe_ser_del_flujo(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0)
        with pytest.raises(ReglaVioladaError):
            rec.derivar_a_flujo(EFECTIVO, BEC, "beto", minutos(1))

    def test_flujo_inexistente(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0)
        with pytest.raises(ReglaVioladaError) as e:
            rec.derivar_a_flujo(99, FIN, "beto", minutos(1))
        assert e.value.codigo_http == 404

    def test_secuencia_estricta(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, flujo_id=EFECTIVO, fase_entrada_id=FIN)
        rec.atender("beto", minutos(1))
        with pytest.raises(ReglaVioladaError):
            rec.finalizar("beto", minutos(2), consultas=[BEC])
        with pytest.raises(ReglaVioladaError):
            rec.derivar_ahora(BEC, "beto", minutos(2))
        with pytest.raises(ReglaVioladaError):
            rec.corregir_ruta([BEC], motivo="x", usuario="gestor", ahora=minutos(2))
        rec.finalizar("beto", minutos(3))
        assert rec.fase_actual.id == CAJ

    def test_no_se_deriva_a_otro_flujo_desde_un_flujo(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, flujo_id=EFECTIVO, fase_entrada_id=FIN)
        with pytest.raises(ReglaVioladaError):
            rec.derivar_a_flujo(EFECTIVO, CAJ, "beto", minutos(1))

    def test_cierre_manual_exige_detalle(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, flujo_id=EFECTIVO, fase_entrada_id=FIR)
        rec.atender("beto", minutos(1))
        with pytest.raises(ReglaVioladaError) as e:
            rec.finalizar("beto", minutos(2))
        assert e.value.codigo_http == 400

    def test_cierre_manual_matricula(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, flujo_id=EFECTIVO, fase_entrada_id=CAJ)
        atender_y_finalizar(rec, 1)
        atender_y_finalizar(rec, 10, detalle_cierre="Pago en efectivo, boleta 12345; contrato firmado")
        assert rec.estado == MATRICULADO
        assert rec.tipo_cierre == MANUAL
        assert rec.fase_cierre_id == FIR
        assert rec.ruta[-1].detalle_cierre.startswith("Pago en efectivo")

    def test_detalle_de_cierre_solo_en_la_fase_final(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, flujo_id=EFECTIVO, fase_entrada_id=CAJ)
        rec.atender("beto", minutos(1))
        with pytest.raises(ReglaVioladaError):
            rec.finalizar("beto", minutos(2), detalle_cierre="Pagado")

    def test_devolver_al_flujo_principal(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, flujo_id=EFECTIVO, fase_entrada_id=FIN)
        rec.atender("beto", minutos(1))
        rec.devolver_a_principal([BEC], motivo="Pagará con tarjeta", usuario="beto", ahora=minutos(4))
        assert rec.flujo_alternativo_id is None
        assert fases_de(rec)[1:] == [(FIN, COMPLETADO), (BEC, EN_ESPERA), (MNP, PLANIFICADA)]
        assert {a.fase_id for a in rec.quitadas} == {CAJ, FIR}

    def test_devolver_exige_motivo_y_estar_en_un_flujo(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, flujo_id=EFECTIVO, fase_entrada_id=FIN)
        with pytest.raises(ReglaVioladaError):
            rec.devolver_a_principal([], motivo=" ", usuario="beto", ahora=minutos(1))
        principal = Recorrido.iniciar_por_staff(est, "ana", T0)
        with pytest.raises(ReglaVioladaError):
            principal.devolver_a_principal([], motivo="x", usuario="beto", ahora=minutos(1))

    def test_reactivar_en_flujo_solo_en_fases_del_flujo(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, flujo_id=EFECTIVO, fase_entrada_id=CAJ)
        rec.marcar_abandono("beto", minutos(5))
        with pytest.raises(ReglaVioladaError):
            rec.reactivar("beto", minutos(9), fase_id=MNP)
        rec.reactivar("beto", minutos(9))
        assert fases_de(rec)[1:] == [(CAJ, ABANDONADA), (CAJ, EN_ESPERA), (FIR, PLANIFICADA)]


# ── Reapertura (RF-27) ──────────────────────────────────────────────


class TestReapertura:
    def test_reabrir_ultima_completada(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        atender_y_finalizar(rec, 1)
        rec.reabrir(motivo="Faltó revisar documento", usuario="gestor", ahora=minutos(8))
        assert fases_de(rec)[1:] == [(BEC, EN_ESPERA), (MNP, PLANIFICADA)]
        assert rec.actual.en_espera_desde == minutos(8)
        assert rec.actual.fin_atencion is None

    def test_no_se_reabre_si_la_siguiente_ya_comenzo(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        atender_y_finalizar(rec, 1)
        rec.atender("beto", minutos(8))
        with pytest.raises(ReglaVioladaError):
            rec.reabrir(motivo="x", usuario="gestor", ahora=minutos(9))

    def test_no_se_reabre_iniciado(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0)
        with pytest.raises(ReglaVioladaError):
            rec.reabrir(motivo="x", usuario="gestor", ahora=minutos(1))

    def test_reabrir_mnp_desmatricula(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0)
        atender_y_finalizar(rec, 1)
        rec.reabrir(motivo="Error al marcar", usuario="gestor", ahora=minutos(9))
        assert rec.estado == EN_CURSO
        assert rec.tipo_cierre is None
        assert rec.matriculado_en is None
        assert rec.actual.fase_id == MNP

    def test_reabrir_cierre_manual(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, flujo_id=EFECTIVO, fase_entrada_id=FIR)
        atender_y_finalizar(rec, 1, detalle_cierre="Pagado")
        rec.reabrir(motivo="Boleta anulada", usuario="gestor", ahora=minutos(9))
        assert rec.estado == EN_CURSO
        assert rec.actual.fase_id == FIR
        assert rec.actual.detalle_cierre is None

    def test_reabrir_exige_motivo(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        atender_y_finalizar(rec, 1)
        with pytest.raises(ReglaVioladaError):
            rec.reabrir(motivo="", usuario="gestor", ahora=minutos(9))


# ── Cierre del proceso ──────────────────────────────────────────────


class TestCierreDelProceso:
    def test_en_curso_queda_pendiente_con_fase_detenida(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        rec.cerrar_proceso()
        assert rec.estado == PROCESO_PENDIENTE
        assert rec.fase_detenida_id == BEC
        assert rec.proceso_estado == "CERRADO"

    def test_abandono_queda_pendiente(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0, consultas=[BEC])
        rec.marcar_abandono("beto", minutos(5))
        rec.cerrar_proceso()
        assert rec.estado == PROCESO_PENDIENTE
        assert rec.fase_detenida_id == BEC
        assert rec.hubo_abandono

    def test_matriculado_no_cambia(self, est):
        rec = Recorrido.iniciar_por_staff(est, "ana", T0)
        atender_y_finalizar(rec, 1)
        rec.cerrar_proceso()
        assert rec.estado == MATRICULADO
        assert rec.fase_detenida_id is None
