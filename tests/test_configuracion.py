"""Reglas de configuración del Administrador: RF-01 a RF-05, RF-23, RF-29 y §4."""

from datetime import date

import pytest

from app import create_app
from app import models as m
from app.config import TestConfig
from app.security import verificar_password
from app.services import configuracion as cfg
from app.services import recorridos
from app.services import workflow as wf
from app.services.workflow import ReglaVioladaError


@pytest.fixture
def ctx(bd):
    with create_app(TestConfig).app_context():
        yield


@pytest.fixture
def admin(ctx):
    return m.Usuario.create(email="admin@unach.cl", nombre="Admin", rol="ADMIN", must_change_password=False)


def datos_proceso(**extra):
    return {
        "codigo": "mat2027", "nombre": "Matrícula 2027", "descripcion": "",
        "fecha_inicio": "2027-01-10", "fecha_fin": "2027-03-31", "prefijo_atencion": "m",
        "enmascarar_datos_qr": "on", **extra,
    }


def usuario(email, rol="USUARIO_FASE", activo=True):
    return m.Usuario.create(email=email, nombre=email.split("@")[0].title(), rol=rol, activo=activo)


def fases(proceso):
    return list(m.Fase.select().where(m.Fase.proceso == proceso.id).order_by(m.Fase.orden))


def auditorias(accion):
    return list(m.Auditoria.select().where(m.Auditoria.accion == accion))


@pytest.fixture
def proceso(admin):
    return cfg.crear_proceso(datos_proceso(), admin)


def sistema(proceso):
    ini, mnp = fases(proceso)[0], fases(proceso)[-1]
    return ini, mnp


def consulta(proceso, admin, identificador="BEC", nombre="Becas", **extra):
    return cfg.agregar_fase(proceso.id, {"identificador": identificador, "nombre": nombre,
                                         "mostrar_en_qr": "on", **extra}, admin)


def listo_para_activar(proceso, admin):
    """Asigna un responsable a cada fase."""
    responsable = usuario(f"resp{proceso.id}@unach.cl")
    for fase in fases(proceso):
        m.FaseResponsable.create(fase=fase, usuario=responsable)
    return responsable


def iniciar_expediente(proceso, numero=1):
    """Crea un expediente mínimo para que el proceso quede iniciado."""
    postulante = m.Postulante.create(
        rut=f"{10000000 + numero}-{numero % 10}", nombres="Pos", apellidos="Tulante",
        telefono="+56911111111", correo_personal=f"p{numero}@mail.cl", origen="CARGA_MANUAL",
    )
    expediente = m.Expediente.create(
        proceso=proceso, postulante=postulante, via_inicio="STAFF", numero_atencion=numero,
        token_seguimiento=f"tok-{proceso.id}-{numero}",
    )
    ini, mnp = sistema(proceso)
    ahora = m.ahora()
    m.Atencion.create(expediente=expediente, fase=ini, secuencia=1, estado=wf.COMPLETADO,
                      en_espera_desde=ahora, inicio_atencion=ahora, fin_atencion=ahora, version=1)
    m.Atencion.create(expediente=expediente, fase=mnp, secuencia=2, estado=wf.EN_ESPERA,
                      en_espera_desde=ahora, version=1)
    return expediente


# ── Procesos ──

def test_crear_proceso_en_borrador_con_sus_fases_de_sistema(proceso):
    assert proceso.estado == "BORRADOR"
    assert proceso.codigo == "MAT2027" and proceso.prefijo_atencion == "M"
    assert proceso.enmascarar_datos_qr is True
    assert proceso.fecha_eliminacion == date(2027, 11, 30)
    ini, mnp = fases(proceso)
    assert (ini.tipo, ini.nombre, ini.orden) == (wf.INICIAL, "Iniciado", 1)
    assert (mnp.tipo, mnp.identificador, mnp.orden) == (wf.CIERRE_DIGITAL, "MNP", 2)
    [registro] = auditorias("CREAR_PROCESO")
    assert registro.valor_nuevo["codigo"] == "MAT2027"


@pytest.mark.parametrize("extra, mensaje", [
    ({"nombre": " "}, "nombre es obligatorio"),
    ({"fecha_fin": "2026-12-31"}, "no puede ser anterior a la de inicio"),
    ({"fecha_inicio": "31-01-2027"}, "no es una fecha válida"),
    ({"prefijo_atencion": "M-"}, "solo admite letras y números"),
    ({"fecha_eliminacion": "2027-03-01"}, "eliminación de datos"),
])
def test_crear_proceso_valida_los_datos(admin, extra, mensaje):
    with pytest.raises(ReglaVioladaError, match=mensaje):
        cfg.crear_proceso(datos_proceso(**extra), admin)


def test_codigo_de_proceso_unico(proceso, admin):
    with pytest.raises(ReglaVioladaError, match="Ya existe un proceso con el código MAT2027"):
        cfg.crear_proceso(datos_proceso(codigo="Mat2027"), admin)


def test_editar_proceso_y_auditar(proceso, admin):
    cfg.editar_proceso(proceso.id, {"nombre": "Matrícula 2027 (1er sem.)", "enmascarar_datos_qr": ""}, admin)
    proceso = m.Proceso.get_by_id(proceso.id)
    assert proceso.nombre == "Matrícula 2027 (1er sem.)" and proceso.enmascarar_datos_qr is False
    [registro] = auditorias("EDITAR_PROCESO")
    assert registro.valor_anterior["enmascarar_datos_qr"] is True


def test_proceso_iniciado_no_cambia_codigo_prefijo_ni_inicio(proceso, admin):
    iniciar_expediente(proceso)
    for campo, valor in (("codigo", "OTRO"), ("prefijo_atencion", "X"), ("fecha_inicio", "2027-01-11")):
        with pytest.raises(ReglaVioladaError, match="ya tiene expedientes"):
            cfg.editar_proceso(proceso.id, {campo: valor}, admin)
    cfg.editar_proceso(proceso.id, {"descripcion": "Nueva descripción"}, admin)
    assert m.Proceso.get_by_id(proceso.id).descripcion == "Nueva descripción"


def test_activar_exige_responsables_en_todas_las_fases(proceso, admin):
    with pytest.raises(cfg.ConfiguracionInvalidaError) as error:
        cfg.activar_proceso(proceso.id, admin)
    assert error.value.errores == [
        "La fase «Iniciado» no tiene responsables", "La fase «MNP» no tiene responsables",
    ]
    listo_para_activar(proceso, admin)
    cfg.activar_proceso(proceso.id, admin)
    assert m.Proceso.get_by_id(proceso.id).estado == "ACTIVO"
    assert len(auditorias("ACTIVAR_PROCESO")) == 1


def test_responsable_desactivado_no_cuenta_para_activar(proceso, admin):
    responsable = listo_para_activar(proceso, admin)
    responsable.activo = False
    responsable.save()
    with pytest.raises(cfg.ConfiguracionInvalidaError):
        cfg.activar_proceso(proceso.id, admin)


def test_solo_un_proceso_activo(proceso, admin):
    listo_para_activar(proceso, admin)
    cfg.activar_proceso(proceso.id, admin)
    otro = cfg.crear_proceso(datos_proceso(codigo="MAT2028"), admin)
    listo_para_activar(otro, admin)
    with pytest.raises(ReglaVioladaError, match="Ya hay un proceso activo"):
        cfg.activar_proceso(otro.id, admin)


def test_activar_ignora_flujos_inactivos_y_valida_los_activos(proceso, admin):
    listo_para_activar(proceso, admin)
    caja = consulta(proceso, admin, "CAJA", "Caja", mostrar_en_qr="")
    m.FaseResponsable.create(fase=caja, usuario=usuario("caja@unach.cl"))
    flujo = cfg.crear_flujo(proceso.id, {"codigo": "EFE", "nombre": "Efectivo", "fases": [caja.id]}, admin)
    cfg.editar_flujo(flujo.id, {"activo": False}, admin)
    cfg.activar_proceso(proceso.id, admin)


def test_cerrar_proceso_deja_pendientes_y_cierra_expedientes(proceso, admin):
    listo_para_activar(proceso, admin)
    cfg.activar_proceso(proceso.id, admin)
    expediente = iniciar_expediente(proceso)
    assert cfg.pendientes_al_cerrar(proceso.id) == 1
    assert cfg.cerrar_proceso(proceso.id, admin) == 1
    proceso = m.Proceso.get_by_id(proceso.id)
    assert proceso.estado == "CERRADO" and proceso.fecha_cierre is not None
    expediente = m.Expediente.get_by_id(expediente.id)
    assert expediente.estado == wf.PROCESO_PENDIENTE and expediente.cerrado_en is not None
    assert expediente.fase_detenida_id == sistema(proceso)[1].id
    [registro] = auditorias("CERRAR_PROCESO")
    assert registro.valor_nuevo == {"estado": "CERRADO", "proceso_pendiente": 1}


def test_proceso_cerrado_es_de_solo_lectura(proceso, admin):
    listo_para_activar(proceso, admin)
    cfg.activar_proceso(proceso.id, admin)
    cfg.cerrar_proceso(proceso.id, admin)
    ini, _ = sistema(proceso)
    with pytest.raises(ReglaVioladaError, match="cerrado"):
        cfg.editar_proceso(proceso.id, {"descripcion": "x"}, admin)
    with pytest.raises(ReglaVioladaError, match="cerrado"):
        cfg.editar_fase(ini.id, {"nombre": "Otro"}, admin)
    with pytest.raises(ReglaVioladaError, match="Solo se puede activar"):
        cfg.activar_proceso(proceso.id, admin)
    with pytest.raises(ReglaVioladaError, match="Solo se puede cerrar"):
        cfg.cerrar_proceso(proceso.id, admin)


def test_proceso_inexistente_da_404(admin):
    with pytest.raises(ReglaVioladaError) as error:
        cfg.editar_proceso(9999, {}, admin)
    assert error.value.codigo_http == 404


# ── Fases ──

def test_agregar_consulta_queda_antes_de_mnp(proceso, admin):
    becas = consulta(proceso, admin)
    finanzas = consulta(proceso, admin, "fin", "Finanzas")
    assert [f.identificador for f in fases(proceso)] == ["INI", "BEC", "FIN", "MNP"]
    assert becas.tipo == wf.CONSULTA and becas.mostrar_en_qr
    assert finanzas.identificador == "FIN"
    assert len(auditorias("CREAR_FASE")) == 2


def test_identificador_de_fase_unico_y_con_formato(proceso, admin):
    consulta(proceso, admin)
    with pytest.raises(ReglaVioladaError, match="Ya existe una fase con el identificador BEC"):
        consulta(proceso, admin, "bec", "Becas 2")
    with pytest.raises(ReglaVioladaError, match="solo admite letras"):
        consulta(proceso, admin, "BE C", "Becas 3")


def test_editar_fase_de_sistema_no_la_ofrece_en_qr(proceso, admin):
    ini, mnp = sistema(proceso)
    cfg.editar_fase(mnp.id, {"nombre": "Matrícula No Presencial", "mostrar_en_qr": "on"}, admin)
    mnp = m.Fase.get_by_id(mnp.id)
    assert mnp.nombre == "Matrícula No Presencial" and not mnp.mostrar_en_qr


def test_eliminar_fase(proceso, admin):
    becas = consulta(proceso, admin)
    cfg.eliminar_fase(becas.id, admin)
    assert [f.identificador for f in fases(proceso)] == ["INI", "MNP"]
    assert len(auditorias("ELIMINAR_FASE")) == 1


def test_fases_de_sistema_no_se_eliminan(proceso, admin):
    for fase in sistema(proceso):
        with pytest.raises(ReglaVioladaError, match="no se pueden eliminar"):
            cfg.eliminar_fase(fase.id, admin)


def test_no_se_elimina_una_fase_usada_en_un_flujo(proceso, admin):
    caja = consulta(proceso, admin, "CAJA", "Caja")
    cfg.crear_flujo(proceso.id, {"codigo": "EFE", "nombre": "Efectivo", "fases": [caja.id]}, admin)
    with pytest.raises(ReglaVioladaError, match="se usa en los flujos alternativos: Efectivo"):
        cfg.eliminar_fase(caja.id, admin)


def test_no_se_elimina_la_ultima_fase_de_un_usuario(proceso, admin):
    becas = consulta(proceso, admin)
    ana = usuario("ana@unach.cl")
    m.FaseResponsable.create(fase=becas, usuario=ana)
    with pytest.raises(ReglaVioladaError, match="Quedarían sin fases: Ana"):
        cfg.eliminar_fase(becas.id, admin)
    assert m.Fase.get_or_none(m.Fase.id == becas.id) is not None


def test_proceso_iniciado_bloquea_la_estructura(proceso, admin):
    becas = consulta(proceso, admin)
    iniciar_expediente(proceso)
    with pytest.raises(ReglaVioladaError, match="ya tiene expedientes"):
        consulta(proceso, admin, "FIN", "Finanzas")
    with pytest.raises(ReglaVioladaError, match="ya tiene expedientes"):
        cfg.eliminar_fase(becas.id, admin)
    with pytest.raises(ReglaVioladaError, match="identificador"):
        cfg.editar_fase(becas.id, {"identificador": "BECA"}, admin)
    # Lo que no altera las rutas sí se puede (RF-05)
    cfg.editar_fase(becas.id, {"nombre": "Becas y créditos", "mostrar_en_qr": ""}, admin)
    becas = m.Fase.get_by_id(becas.id)
    assert becas.nombre == "Becas y créditos" and not becas.mostrar_en_qr


def test_reordenar_consultas_sin_mover_las_de_sistema(proceso, admin):
    becas = consulta(proceso, admin)
    finanzas = consulta(proceso, admin, "FIN", "Finanzas")
    iniciar_expediente(proceso)  # el orden de presentación se puede cambiar igual
    cfg.mover_fase(finanzas.id, -1, admin)
    assert [f.identificador for f in fases(proceso)] == ["INI", "FIN", "BEC", "MNP"]
    cfg.mover_fase(finanzas.id, -1, admin)  # ya es la primera consulta: no pasa de Iniciado
    assert [f.identificador for f in fases(proceso)] == ["INI", "FIN", "BEC", "MNP"]
    cfg.mover_fase(becas.id, +1, admin)
    assert [f.identificador for f in fases(proceso)] == ["INI", "FIN", "BEC", "MNP"]
    with pytest.raises(ReglaVioladaError, match="no se reordenan"):
        cfg.mover_fase(sistema(proceso)[0].id, +1, admin)
    assert len(auditorias("REORDENAR_FASE")) == 1


# ── Flujos alternativos ──

@pytest.fixture
def consultas(proceso, admin):
    return [consulta(proceso, admin, ident, nombre) for ident, nombre in
            (("FIN", "Finanzas"), ("CAJA", "Caja"), ("FIRMA", "Firma de contrato"))]


def test_crear_flujo_con_su_secuencia(proceso, admin, consultas):
    ids = [f.id for f in consultas]
    flujo = cfg.crear_flujo(proceso.id, {"codigo": "efe", "nombre": "Pago en efectivo", "fases": ids}, admin)
    assert flujo.codigo == "EFE" and flujo.activo
    assert cfg.fases_del_flujo(flujo.id) == ids
    estructura = recorridos.estructura_del_proceso(proceso.id)
    assert estructura.flujo(flujo.id).fase_final_id == ids[-1]
    [registro] = auditorias("CREAR_FLUJO")
    assert registro.valor_nuevo["fases"] == ids


@pytest.mark.parametrize("fases_flujo, mensaje", [
    (lambda c, s: [], "no tiene fases"),
    (lambda c, s: [c[0].id, c[0].id], "se repite una fase"),
    (lambda c, s: [c[0].id, s[1].id], "solo puede tener fases de consulta"),
])
def test_flujo_invalido(proceso, admin, consultas, fases_flujo, mensaje):
    with pytest.raises(ReglaVioladaError, match=mensaje):
        cfg.crear_flujo(proceso.id, {"codigo": "X", "nombre": "X",
                                     "fases": fases_flujo(consultas, sistema(proceso))}, admin)


def test_editar_secuencia_del_flujo(proceso, admin, consultas):
    fin, caja, firma = (f.id for f in consultas)
    flujo = cfg.crear_flujo(proceso.id, {"codigo": "EFE", "nombre": "Efectivo", "fases": [fin, caja]}, admin)
    cfg.editar_flujo(flujo.id, {"fases": [caja, fin, firma]}, admin)
    assert cfg.fases_del_flujo(flujo.id) == [caja, fin, firma]


def test_flujo_con_proceso_iniciado_solo_cambia_nombre_descripcion_y_activo(proceso, admin, consultas):
    fin, caja, firma = (f.id for f in consultas)
    flujo = cfg.crear_flujo(proceso.id, {"codigo": "EFE", "nombre": "Efectivo", "fases": [fin, caja]}, admin)
    iniciar_expediente(proceso)
    with pytest.raises(ReglaVioladaError, match="ya tiene expedientes"):
        cfg.editar_flujo(flujo.id, {"fases": [fin, caja, firma]}, admin)
    with pytest.raises(ReglaVioladaError, match="ya tiene expedientes"):
        cfg.editar_flujo(flujo.id, {"codigo": "EFE2"}, admin)
    with pytest.raises(ReglaVioladaError, match="ya tiene expedientes"):
        cfg.crear_flujo(proceso.id, {"codigo": "OTRO", "nombre": "Otro", "fases": [fin]}, admin)
    with pytest.raises(ReglaVioladaError, match="ya tiene expedientes"):
        cfg.eliminar_flujo(flujo.id, admin)
    cfg.editar_flujo(flujo.id, {"nombre": "Pago en efectivo", "descripcion": "Caja", "activo": False}, admin)
    flujo = m.FlujoAlternativo.get_by_id(flujo.id)
    assert (flujo.nombre, flujo.activo) == ("Pago en efectivo", False)


def test_eliminar_flujo(proceso, admin, consultas):
    flujo = cfg.crear_flujo(proceso.id, {"codigo": "EFE", "nombre": "Efectivo", "fases": [consultas[0].id]}, admin)
    cfg.eliminar_flujo(flujo.id, admin)
    assert m.FlujoAlternativo.select().count() == 0
    assert m.FlujoAlternativoFase.select().count() == 0


# ── Responsables ──

def test_asignar_responsables(proceso, admin):
    ini, _ = sistema(proceso)
    ana, beto = usuario("ana@unach.cl"), usuario("beto@unach.cl", rol="GESTOR")
    cfg.asignar_responsables(ini.id, [ana.id, beto.id], admin)
    assert cfg.responsables_de(ini.id) == {ana.id, beto.id}
    [registro] = auditorias("ASIGNAR_RESPONSABLES")
    assert registro.valor_nuevo == {"usuarios": sorted([ana.id, beto.id])}


def test_no_se_asignan_usuarios_inactivos(proceso, admin):
    with pytest.raises(ReglaVioladaError, match="usuarios activos"):
        cfg.asignar_responsables(sistema(proceso)[0].id, [usuario("x@unach.cl", activo=False).id], admin)


def test_no_se_quita_la_ultima_fase_de_un_usuario_de_fase(proceso, admin):
    ini, mnp = sistema(proceso)
    ana, beto = usuario("ana@unach.cl"), usuario("beto@unach.cl")
    cfg.asignar_responsables(ini.id, [ana.id, beto.id], admin)
    cfg.asignar_responsables(mnp.id, [beto.id], admin)
    cfg.asignar_responsables(ini.id, [ana.id], admin)  # Beto conserva MNP
    with pytest.raises(ReglaVioladaError, match="Quedarían sin fases: Ana"):
        cfg.asignar_responsables(ini.id, [beto.id], admin)


def test_en_borrador_una_fase_puede_quedar_sin_responsables(proceso, admin):
    ini, _ = sistema(proceso)
    gestor = usuario("g@unach.cl", rol="GESTOR")
    cfg.asignar_responsables(ini.id, [gestor.id], admin)
    cfg.asignar_responsables(ini.id, [], admin)
    assert cfg.responsables_de(ini.id) == set()


def test_proceso_activo_conserva_al_menos_un_responsable(proceso, admin):
    responsable = listo_para_activar(proceso, admin)
    cfg.activar_proceso(proceso.id, admin)
    ini, _ = sistema(proceso)
    gestor = usuario("g@unach.cl", rol="GESTOR")
    cfg.asignar_responsables(ini.id, [responsable.id, gestor.id], admin)
    cfg.asignar_responsables(ini.id, [gestor.id], admin)  # el responsable conserva MNP
    with pytest.raises(ReglaVioladaError, match="quedaría sin responsables"):
        cfg.asignar_responsables(ini.id, [], admin)


# ── Usuarios ──

def test_crear_usuario_con_clave_temporal(proceso, admin):
    ini, _ = sistema(proceso)
    nuevo, clave = cfg.crear_usuario({
        "email": " Ana@UNACH.cl ", "nombre": "Ana Pérez", "rol": "USUARIO_FASE",
        "fases": [str(ini.id)], "puede_registrar_postulantes": "on",
    }, admin)
    assert nuevo.email == "ana@unach.cl" and nuevo.must_change_password
    assert len(clave) == 12 and verificar_password(clave, nuevo.password_hash)
    assert nuevo.puede_registrar_postulantes
    assert cfg.fases_abiertas_de(nuevo.id) == {ini.id}
    [registro] = auditorias("CREAR_USUARIO")
    assert registro.valor_nuevo["fases"] == [ini.id] and registro.usuario_id == admin.id
    assert clave not in str(registro.valor_nuevo)


def test_usuario_de_fase_exige_al_menos_una_fase(admin):
    with pytest.raises(ReglaVioladaError, match="al menos una fase"):
        cfg.crear_usuario({"email": "a@unach.cl", "nombre": "A", "rol": "USUARIO_FASE"}, admin)
    assert m.Usuario.select().where(m.Usuario.email == "a@unach.cl").count() == 0


def test_permiso_de_registro_solo_para_usuario_de_fase(admin):
    gestor, _ = cfg.crear_usuario({"email": "g@unach.cl", "nombre": "G", "rol": "GESTOR",
                                   "puede_registrar_postulantes": "on"}, admin)
    assert not gestor.puede_registrar_postulantes


@pytest.mark.parametrize("datos, mensaje", [
    ({"email": "no-es-correo"}, "formato válido"),
    ({"email": "ADMIN@unach.cl"}, "Ya existe un usuario"),
    ({"rol": "SUPER"}, "Rol inválido"),
    ({"nombre": ""}, "nombre es obligatorio"),
])
def test_crear_usuario_valida(admin, datos, mensaje):
    base = {"email": "g@unach.cl", "nombre": "G", "rol": "GESTOR"}
    with pytest.raises(ReglaVioladaError, match=mensaje):
        cfg.crear_usuario({**base, **datos}, admin)


def test_editar_usuario_y_auditar_el_permiso(proceso, admin):
    ini, mnp = sistema(proceso)
    ana, _ = cfg.crear_usuario({"email": "ana@unach.cl", "nombre": "Ana", "rol": "USUARIO_FASE",
                                "fases": [ini.id]}, admin)
    cfg.editar_usuario(ana.id, {"fases": [mnp.id], "puede_registrar_postulantes": "on"}, admin)
    assert cfg.fases_abiertas_de(ana.id) == {mnp.id}
    assert len(auditorias("EDITAR_USUARIO")) == 1
    [permiso] = auditorias("CAMBIO_PERMISO_REGISTRO")
    assert permiso.valor_nuevo == {"puede_registrar_postulantes": True}


def test_editar_usuario_no_deja_fase_activa_sin_responsable(proceso, admin):
    responsable = listo_para_activar(proceso, admin)
    cfg.activar_proceso(proceso.id, admin)
    with pytest.raises(ReglaVioladaError, match="quedaría sin responsables"):
        cfg.editar_usuario(responsable.id, {"rol": "GESTOR", "fases": []}, admin)


def test_las_fases_de_procesos_cerrados_se_conservan(proceso, admin):
    responsable = listo_para_activar(proceso, admin)
    cfg.activar_proceso(proceso.id, admin)
    cfg.cerrar_proceso(proceso.id, admin)
    nuevo = cfg.crear_proceso(datos_proceso(codigo="MAT2028"), admin)
    ini_nuevo, _ = sistema(nuevo)
    with pytest.raises(ReglaVioladaError, match="no cerrados"):
        cfg.editar_usuario(responsable.id, {"fases": [sistema(proceso)[0].id]}, admin)
    cfg.editar_usuario(responsable.id, {"fases": [ini_nuevo.id]}, admin)
    assert cfg.fases_abiertas_de(responsable.id) == {ini_nuevo.id}
    assert m.FaseResponsable.select().where(m.FaseResponsable.usuario == responsable.id).count() == 3


def test_debe_quedar_un_administrador_activo(admin):
    with pytest.raises(ReglaVioladaError, match="al menos un Administrador"):
        cfg.editar_usuario(admin.id, {"rol": "GESTOR"}, admin)
    otro = usuario("otro@unach.cl", rol="ADMIN")
    cfg.cambiar_estado_usuario(otro.id, False, admin)  # queda admin
    with pytest.raises(ReglaVioladaError, match="al menos un Administrador"):
        cfg.editar_usuario(admin.id, {"rol": "GESTOR"}, admin)


def test_desactivar_y_reactivar_usuario(proceso, admin):
    ini, _ = sistema(proceso)
    gestor = usuario("g@unach.cl", rol="GESTOR")
    cfg.cambiar_estado_usuario(gestor.id, False, admin)
    assert not m.Usuario.get_by_id(gestor.id).activo
    cfg.cambiar_estado_usuario(gestor.id, True, admin)
    assert m.Usuario.get_by_id(gestor.id).activo
    assert [a.accion for a in m.Auditoria.select().order_by(m.Auditoria.id)][-2:] == [
        "DESACTIVAR_USUARIO", "ACTIVAR_USUARIO"]


def test_no_puede_desactivarse_a_si_mismo(admin):
    usuario("otro@unach.cl", rol="ADMIN")
    with pytest.raises(ReglaVioladaError, match="propia cuenta"):
        cfg.cambiar_estado_usuario(admin.id, False, admin)


def test_no_se_desactiva_al_unico_responsable_de_un_proceso_activo(proceso, admin):
    responsable = listo_para_activar(proceso, admin)
    cfg.activar_proceso(proceso.id, admin)
    with pytest.raises(ReglaVioladaError, match="quedaría sin responsables"):
        cfg.cambiar_estado_usuario(responsable.id, False, admin)


def test_restablecer_password_desbloquea_y_obliga_a_cambiarla(admin):
    gestor = usuario("g@unach.cl", rol="GESTOR")
    gestor.intentos_fallidos, gestor.bloqueado_hasta, gestor.must_change_password = 3, m.ahora(), False
    gestor.save()
    clave = cfg.restablecer_password(gestor.id, admin)
    gestor = m.Usuario.get_by_id(gestor.id)
    assert verificar_password(clave, gestor.password_hash) and gestor.must_change_password
    assert gestor.intentos_fallidos == 0 and gestor.bloqueado_hasta is None
    assert len(auditorias("RESTABLECER_PASSWORD")) == 1
