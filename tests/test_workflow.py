import pytest

from app.services.workflow import (
    COMPLETADO,
    COMPLETADO_CON_OBSERVACIONES,
    PENDIENTE,
    FaseEstado,
    ReglaVioladaError,
    estado_global,
    fase_actual,
    fase_bloqueante,
    puede_modificar_fase,
    validar_cambio_estado,
    validar_cambio_estructura,
)


def expediente(*estados):
    """Fases en orden: la primera es "Iniciado" (de sistema)."""
    nombres = ["Iniciado", "Finanzas", "Contrato", "Becas", "Ficha"]
    return [
        FaseEstado(fase_id=i + 1, orden=i + 1, nombre=nombres[i], estado=e, es_sistema=(i == 0))
        for i, e in enumerate(estados)
    ]


class TestFaseActualYBloqueante:
    def test_fase_actual_es_la_primera_pendiente(self):
        fases = expediente(COMPLETADO, COMPLETADO_CON_OBSERVACIONES, PENDIENTE, PENDIENTE)
        assert fase_actual(fases).nombre == "Contrato"

    def test_sin_fase_actual_si_todo_completo(self):
        assert fase_actual(expediente(COMPLETADO, COMPLETADO)) is None

    def test_fase_bloqueante(self):
        fases = expediente(COMPLETADO, PENDIENTE, PENDIENTE)
        assert fase_bloqueante(fases, fase_id=3).nombre == "Finanzas"
        assert fase_bloqueante(fases, fase_id=2) is None

    def test_orden_no_depende_de_la_posicion_en_la_lista(self):
        fases = list(reversed(expediente(COMPLETADO, PENDIENTE, PENDIENTE)))
        assert fase_actual(fases).nombre == "Finanzas"
        assert fase_bloqueante(fases, fase_id=3).nombre == "Finanzas"


class TestValidarCambioEstado:
    def test_completar_fase_habilitada(self):
        validar_cambio_estado(expediente(COMPLETADO, PENDIENTE), fase_id=2, nuevo_estado=COMPLETADO)

    def test_no_se_puede_completar_fase_no_habilitada(self):
        with pytest.raises(ReglaVioladaError) as exc:
            validar_cambio_estado(
                expediente(COMPLETADO, PENDIENTE, PENDIENTE), fase_id=3, nuevo_estado=COMPLETADO
            )
        assert exc.value.codigo_http == 409
        assert "«Finanzas»" in exc.value.mensaje

    def test_observacion_obligatoria(self):
        with pytest.raises(ReglaVioladaError) as exc:
            validar_cambio_estado(
                expediente(COMPLETADO, PENDIENTE), fase_id=2,
                nuevo_estado=COMPLETADO_CON_OBSERVACIONES, observacion="   ",
            )
        assert exc.value.codigo_http == 400

    def test_completado_con_observaciones(self):
        validar_cambio_estado(
            expediente(COMPLETADO, PENDIENTE), fase_id=2,
            nuevo_estado=COMPLETADO_CON_OBSERVACIONES, observacion="Falta firma del aval",
        )

    def test_revertir_requiere_motivo(self):
        with pytest.raises(ReglaVioladaError) as exc:
            validar_cambio_estado(expediente(COMPLETADO, COMPLETADO), fase_id=2, nuevo_estado=PENDIENTE)
        assert exc.value.codigo_http == 400

    def test_revertir_ultima_fase_completa(self):
        validar_cambio_estado(
            expediente(COMPLETADO, COMPLETADO, PENDIENTE), fase_id=2,
            nuevo_estado=PENDIENTE, motivo="Error de digitación",
        )

    def test_no_revertir_si_hay_fases_posteriores_completas(self):
        with pytest.raises(ReglaVioladaError) as exc:
            validar_cambio_estado(
                expediente(COMPLETADO, COMPLETADO, COMPLETADO), fase_id=2,
                nuevo_estado=PENDIENTE, motivo="x",
            )
        assert exc.value.codigo_http == 409

    def test_iniciado_nunca_se_revierte(self):
        with pytest.raises(ReglaVioladaError) as exc:
            validar_cambio_estado(
                expediente(COMPLETADO, PENDIENTE), fase_id=1, nuevo_estado=PENDIENTE, motivo="x"
            )
        assert exc.value.codigo_http == 409

    def test_iniciado_no_se_marca_manualmente(self):
        with pytest.raises(ReglaVioladaError):
            validar_cambio_estado(expediente(COMPLETADO, PENDIENTE), fase_id=1, nuevo_estado=COMPLETADO)

    def test_mismo_estado_es_rechazado(self):
        with pytest.raises(ReglaVioladaError):
            validar_cambio_estado(expediente(COMPLETADO, PENDIENTE), fase_id=2, nuevo_estado=PENDIENTE, motivo="x")

    def test_cambiar_entre_completado_y_con_observaciones(self):
        validar_cambio_estado(
            expediente(COMPLETADO, COMPLETADO, PENDIENTE), fase_id=2,
            nuevo_estado=COMPLETADO_CON_OBSERVACIONES, observacion="Pago parcial",
        )

    def test_estado_desconocido(self):
        with pytest.raises(ReglaVioladaError):
            validar_cambio_estado(expediente(COMPLETADO, PENDIENTE), fase_id=2, nuevo_estado="APROBADO")

    def test_fase_inexistente(self):
        with pytest.raises(ReglaVioladaError) as exc:
            validar_cambio_estado(expediente(COMPLETADO, PENDIENTE), fase_id=99, nuevo_estado=COMPLETADO)
        assert exc.value.codigo_http == 404

    def test_proceso_cerrado_es_solo_lectura(self):
        with pytest.raises(ReglaVioladaError) as exc:
            validar_cambio_estado(
                expediente(COMPLETADO, PENDIENTE), fase_id=2, nuevo_estado=COMPLETADO,
                proceso_estado="CERRADO",
            )
        assert exc.value.codigo_http == 409


class TestEstadoGlobal:
    def test_matriculado(self):
        fases = expediente(COMPLETADO, COMPLETADO_CON_OBSERVACIONES, COMPLETADO)
        assert estado_global(fases, "ACTIVO") == "MATRICULADO"

    def test_no_matriculado_aun(self):
        assert estado_global(expediente(COMPLETADO, PENDIENTE), "ACTIVO") == "NO_MATRICULADO"

    def test_proceso_pendiente_al_cerrar(self):
        assert estado_global(expediente(COMPLETADO, PENDIENTE), "CERRADO") == "PROCESO_PENDIENTE"

    def test_matriculado_se_mantiene_al_cerrar(self):
        assert estado_global(expediente(COMPLETADO, COMPLETADO), "CERRADO") == "MATRICULADO"


class TestValidarCambioEstructura:
    def test_proceso_no_iniciado_permite_cambios(self):
        validar_cambio_estructura(proceso_iniciado=False)

    def test_proceso_iniciado_rechaza_cambios(self):
        with pytest.raises(ReglaVioladaError) as exc:
            validar_cambio_estructura(proceso_iniciado=True)
        assert exc.value.codigo_http == 409


class TestPuedeModificarFase:
    @pytest.mark.parametrize("rol", ["ADMIN", "GESTOR"])
    def test_admin_y_gestor_cualquier_fase(self, rol):
        assert puede_modificar_fase(rol, fases_asignadas=set(), fase_id=3)

    def test_usuario_de_fase_solo_sus_fases(self):
        assert puede_modificar_fase("USUARIO_FASE", fases_asignadas={2, 4}, fase_id=2)
        assert not puede_modificar_fase("USUARIO_FASE", fases_asignadas={2, 4}, fase_id=3)

    def test_rol_desconocido(self):
        assert not puede_modificar_fase("OTRO", fases_asignadas={3}, fase_id=3)
