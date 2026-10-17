"""Inicio de proceso por staff y por QR, y creación del expediente: RF-09, RF-10, RF-11, RF-19."""

import pytest

import seed_carreras
from app import create_app
from app import models as m
from app.config import TestConfig
from app.services import configuracion as cfg
from app.services import inicio, recorridos, seguimiento
from app.services import workflow as wf
from app.services.workflow import ReglaVioladaError


@pytest.fixture
def ctx(bd):
    with create_app(TestConfig).app_context():
        seed_carreras.sembrar()
        yield


@pytest.fixture
def admin(ctx):
    return m.Usuario.create(email="admin@unach.cl", nombre="Admin", rol="ADMIN", must_change_password=False)


def fase(proceso, identificador):
    return m.Fase.get((m.Fase.proceso == proceso.id) & (m.Fase.identificador == identificador))


def armar_proceso(admin, activar=True, codigo="MAT2027", **extra):
    proceso = cfg.crear_proceso({
        "codigo": codigo, "nombre": "Matrícula 2027", "fecha_inicio": "2027-01-10",
        "fecha_fin": "2027-03-31", "prefijo_atencion": "M", "enmascarar_datos_qr": "on", **extra,
    }, admin)
    cfg.agregar_fase(proceso.id, {"identificador": "BEC", "nombre": "Becas", "mostrar_en_qr": "on"}, admin)
    cfg.agregar_fase(proceso.id, {"identificador": "FIN", "nombre": "Finanzas", "mostrar_en_qr": ""}, admin)
    cfg.agregar_fase(proceso.id, {"identificador": "CAJA", "nombre": "Caja", "mostrar_en_qr": ""}, admin)
    cfg.crear_flujo(proceso.id, {"codigo": "EFE", "nombre": "Pago en efectivo",
                                 "fases": [fase(proceso, "FIN").id, fase(proceso, "CAJA").id]}, admin)
    responsable = m.Usuario.create(email=f"ini-{codigo}@unach.cl", nombre="Inés Inicio", rol="USUARIO_FASE",
                                   must_change_password=False)
    for f in m.Fase.select().where(m.Fase.proceso == proceso.id):
        m.FaseResponsable.create(fase=f, usuario=responsable)
    if activar:
        cfg.activar_proceso(proceso.id, admin)
    return m.Proceso.get_by_id(proceso.id)


@pytest.fixture
def proceso(admin):
    return armar_proceso(admin)


def datos(**cambios):
    base = {
        "rut": "12.345.678-5", "nombres": "Juan Andrés", "apellidos": "Pérez Soto",
        "telefono": "+56 9 1234 5678", "correo_personal": "juan.perez@gmail.com",
        "carrera1": "ENF", "carrera2": "", "carrera3": "",
    }
    base.update(cambios)
    return base


def ruta_de(expediente):
    _, recorrido = recorridos.cargar(expediente.id, bloquear=False)
    nombres = {f.id: f.identificador for f in m.Fase.select()}
    return [(nombres[a.fase_id], a.estado) for a in recorrido.ruta]


def auditorias(accion):
    return list(m.Auditoria.select().where(m.Auditoria.accion == accion))


def precargar(rut="12345678-5", **campos):
    valores = {"nombres": "Juan Andrés", "apellidos": "Pérez Soto", "telefono": "+56912345678",
               "correo_personal": "juan.perez@gmail.com", **campos}
    postulante = m.Postulante.create(rut=rut, origen="CARGA_MASIVA", **valores)
    m.PostulanteCarrera.create(postulante=postulante, preferencia=1,
                               carrera=m.Carrera.get(m.Carrera.codigo == "ENF"))
    return postulante


# ── Precondición: proceso ACTIVO ──

def test_sin_proceso_activo_ninguna_via_inicia(admin):
    armar_proceso(admin, activar=False)
    with pytest.raises(ReglaVioladaError) as error:
        inicio.iniciar_por_staff(datos(), {"consultas": []}, admin)
    assert error.value.codigo_http == 409 and error.value.mensaje == inicio.SIN_PROCESO_ACTIVO
    with pytest.raises(ReglaVioladaError):
        inicio.iniciar_por_qr(datos(), [])
    with pytest.raises(ReglaVioladaError):
        inicio.consultar_qr("12.345.678-5")
    assert m.Expediente.select().count() == 0 and m.Postulante.select().count() == 0


def test_proceso_cerrado_no_admite_inicios(admin, proceso):
    cfg.cerrar_proceso(proceso.id, admin)
    with pytest.raises(ReglaVioladaError):
        inicio.iniciar_por_staff(datos(), {"consultas": []}, admin)


# ── Inicio por staff (RF-09, RF-11) ──

def test_staff_rut_nuevo_sin_dudas_va_directo_a_mnp(admin, proceso):
    expediente = inicio.iniciar_por_staff(datos(), {"consultas": []}, admin)

    postulante = m.Postulante.get_by_id(expediente.postulante_id)
    assert postulante.rut == "12345678-5" and postulante.origen == "INICIO_STAFF"
    assert postulante.telefono == "+56912345678" and postulante.eliminar_en is None
    assert inicio.datos_de(postulante)["carrera1"] == "ENF"
    assert expediente.via_inicio == "STAFF" and expediente.iniciado_por_id == admin.id
    assert expediente.numero_atencion == 1 and len(expediente.token_seguimiento) >= 43
    assert ruta_de(expediente) == [("INI", wf.COMPLETADO), ("MNP", wf.EN_ESPERA)]
    ini = m.Atencion.get((m.Atencion.expediente == expediente.id) & (m.Atencion.secuencia == 1))
    assert ini.atendido_por_id == admin.id
    assert len(auditorias("CREAR_POSTULANTE")) == 1
    registro = auditorias("INICIAR_PROCESO")[0]
    assert registro.valor_nuevo["via"] == "STAFF" and registro.valor_nuevo["numero_atencion"] == "M-0001"
    assert registro.usuario_id == admin.id and registro.actor == "USUARIO"


def test_staff_con_dudas_respeta_el_orden_y_deja_mnp_al_final(admin, proceso):
    estructura = recorridos.estructura_del_proceso(proceso.id)
    ruta = inicio.ruta_desde_formulario(
        {"dudas": "si", f"orden_{fase(proceso, 'FIN').id}": "1", f"orden_{fase(proceso, 'BEC').id}": "2"}, estructura)
    expediente = inicio.iniciar_por_staff(datos(), ruta, admin)
    assert ruta_de(expediente) == [
        ("INI", wf.COMPLETADO), ("FIN", wf.EN_ESPERA), ("BEC", wf.PLANIFICADA), ("MNP", wf.PLANIFICADA)]


def test_staff_puede_elegir_un_flujo_alternativo_desde_el_comienzo(admin, proceso):
    estructura = recorridos.estructura_del_proceso(proceso.id)
    flujo = m.FlujoAlternativo.get(m.FlujoAlternativo.codigo == "EFE")
    ruta = inicio.ruta_desde_formulario({"dudas": "flujo", "entrada": f"{flujo.id}:{fase(proceso, 'CAJA').id}"},
                                        estructura)
    expediente = inicio.iniciar_por_staff(datos(), ruta, admin)
    assert ruta_de(expediente) == [("INI", wf.COMPLETADO), ("CAJA", wf.EN_ESPERA)]
    assert m.Expediente.get_by_id(expediente.id).flujo_alternativo_id == flujo.id


@pytest.mark.parametrize("formulario, mensaje", [
    ({}, "Indique si el postulante tiene dudas"),
    ({"dudas": "tal vez"}, "Indique si el postulante tiene dudas"),
    ({"dudas": "si"}, "Elija al menos una fase"),
    ({"dudas": "si", "orden_BEC": "0"}, "número desde 1"),
    ({"dudas": "si", "orden_BEC": "x"}, "número desde 1"),
    ({"dudas": "si", "orden_BEC": "1", "orden_FIN": "1"}, "No repita"),
    ({"dudas": "flujo"}, "Elija el flujo"),
    ({"dudas": "flujo", "entrada": "abc"}, "Elija el flujo"),
])
def test_respuesta_a_tiene_dudas_invalida(admin, proceso, formulario, mensaje):
    estructura = recorridos.estructura_del_proceso(proceso.id)
    reemplazos = {f"orden_{c}": f"orden_{fase(proceso, c).id}" for c in ("BEC", "FIN")}
    formulario = {reemplazos.get(k, k): v for k, v in formulario.items()}
    with pytest.raises(ReglaVioladaError) as error:
        inicio.ruta_desde_formulario(formulario, estructura)
    assert mensaje in error.value.mensaje and error.value.codigo_http == 422


def test_staff_corrige_datos_existentes_y_cada_cambio_queda_auditado(admin, proceso):
    postulante = precargar(telefono=None, eliminar_en="2027-11-30")
    expediente = inicio.iniciar_por_staff(
        datos(rut="12345678-5", telefono="+56987654321", carrera1="KIN", carrera2="ENF",
              correo_personal="juan.perez@gmail.com"),
        {"consultas": []}, admin)

    postulante = m.Postulante.get_by_id(postulante.id)
    assert expediente.postulante_id == postulante.id
    assert postulante.origen == "CARGA_MASIVA"  # el origen es el del registro, no cambia
    assert postulante.telefono == "+56987654321" and postulante.eliminar_en is None
    actuales = inicio.datos_de(postulante)
    assert (actuales["carrera1"], actuales["carrera2"]) == ("KIN", "ENF")
    registro = auditorias("EDITAR_POSTULANTE")[0]
    assert registro.valor_anterior == {"telefono": None, "carrera1": "ENF", "carrera2": None}
    assert registro.valor_nuevo == {"telefono": "+56987654321", "carrera1": "KIN", "carrera2": "ENF"}
    assert auditorias("CREAR_POSTULANTE") == []


def test_staff_sin_cambios_no_audita_edicion(admin, proceso):
    precargar()
    inicio.iniciar_por_staff(datos(), {"consultas": []}, admin)
    assert auditorias("EDITAR_POSTULANTE") == []


def test_staff_no_crea_un_segundo_expediente(admin, proceso):
    primero = inicio.iniciar_por_staff(datos(), {"consultas": []}, admin)
    with pytest.raises(inicio.ExpedienteExistenteError) as error:
        inicio.iniciar_por_staff(datos(rut="12345678-5"), {"consultas": []}, admin)
    assert error.value.expediente.id == primero.id
    assert m.Expediente.select().count() == 1


def test_staff_datos_invalidos_no_crean_nada(admin, proceso):
    with pytest.raises(inicio.DatosInvalidosError) as error:
        inicio.iniciar_por_staff(datos(correo_personal="", carrera1="XXX"), {"consultas": []}, admin)
    assert set(error.value.campos) == {"correo_personal", "carrera1"}
    assert m.Postulante.select().count() == 0 and m.Expediente.select().count() == 0
    assert m.Proceso.get_by_id(proceso.id).ultimo_numero_atencion == 0


def test_rut_invalido_es_rechazado(admin, proceso):
    with pytest.raises(inicio.DatosInvalidosError) as error:
        inicio.iniciar_por_staff(datos(rut="12.345.678-9"), {"consultas": []}, admin)
    assert "rut" in error.value.campos


def test_numero_de_atencion_correlativo_entre_vias(admin, proceso):
    a = inicio.iniciar_por_staff(datos(), {"consultas": []}, admin)
    b = inicio.iniciar_por_qr(datos(rut="11.111.111-1"), [])
    assert (a.numero_atencion, b.numero_atencion) == (1, 2)
    assert inicio.numero_visible(proceso, b.numero_atencion) == "M-0002"
    assert a.token_seguimiento != b.token_seguimiento


def test_usuario_de_fase_sin_iniciado_no_puede_iniciar(admin, proceso):
    becas = m.Usuario.create(email="bec@unach.cl", nombre="Becas", rol="USUARIO_FASE", puede_registrar_postulantes=True)
    m.FaseResponsable.create(fase=fase(proceso, "BEC"), usuario=becas)
    assert not inicio.puede_iniciar(becas)
    with pytest.raises(ReglaVioladaError) as error:
        inicio.iniciar_por_staff(datos(), {"consultas": []}, becas)
    assert error.value.codigo_http == 403
    assert m.Expediente.select().count() == 0


def test_responsable_de_iniciado_inicia_un_rut_nuevo_sin_permiso_de_carga(admin, proceso):
    responsable = m.Usuario.get(m.Usuario.email == "ini-MAT2027@unach.cl")
    assert not responsable.puede_registrar_postulantes and inicio.puede_iniciar(responsable)
    expediente = inicio.iniciar_por_staff(datos(), {"consultas": []}, responsable)
    assert expediente.iniciado_por_id == responsable.id


def test_responsable_de_iniciado_de_otro_proceso_no_puede(admin, proceso):
    otro = armar_proceso(admin, activar=False, codigo="MAT2028")
    ajeno = m.Usuario.create(email="ajeno@unach.cl", nombre="Ajeno", rol="USUARIO_FASE")
    m.FaseResponsable.create(fase=fase(otro, "INI"), usuario=ajeno)
    assert not inicio.puede_iniciar(ajeno)


def test_gestor_siempre_puede_iniciar(admin, proceso):
    gestor = m.Usuario.create(email="g@unach.cl", nombre="Gestor", rol="GESTOR")
    assert inicio.puede_iniciar(gestor)


def test_carrera_inactiva_se_rechaza_para_nuevos_pero_se_conserva_en_existentes(admin, proceso):
    m.Carrera.update(activo=False).where(m.Carrera.codigo == "ENF").execute()
    with pytest.raises(inicio.DatosInvalidosError):
        inicio.iniciar_por_staff(datos(rut="11.111.111-1"), {"consultas": []}, admin)
    precargar()  # ya tenía ENF
    expediente = inicio.iniciar_por_staff(datos(), {"consultas": []}, admin)
    assert inicio.datos_de(m.Postulante.get_by_id(expediente.postulante_id))["carrera1"] == "ENF"
    assert "ENF" not in [c.codigo for c in inicio.opciones_de_carrera()]
    assert "ENF" in [c.codigo for c in inicio.opciones_de_carrera(["ENF"])]


def test_postulante_desactivado_no_inicia(admin, proceso):
    precargar(activo=False)
    with pytest.raises(ReglaVioladaError, match="desactivado"):
        inicio.iniciar_por_staff(datos(), {"consultas": []}, admin)
    with pytest.raises(ReglaVioladaError, match="staff"):
        inicio.iniciar_por_qr(datos(), [])


def test_primer_expediente_bloquea_la_estructura(admin, proceso):
    inicio.iniciar_por_staff(datos(), {"consultas": []}, admin)
    with pytest.raises(ReglaVioladaError):
        cfg.agregar_fase(proceso.id, {"identificador": "OTRA", "nombre": "Otra"}, admin)


# ── Inicio por QR (RF-10, RF-11) ──

def test_qr_consulta_de_rut_nuevo(proceso):
    consulta = inicio.consultar_qr("12.345.678-5")
    assert consulta["rut"] == "12345678-5" and not consulta["existe"]
    assert consulta["pendientes"] == ["nombres", "apellidos", "telefono", "correo_personal",
                                      "carrera1", "carrera2", "carrera3"]
    assert [f.nombre for f in consulta["consultas"]] == ["Becas"]  # solo mostrar_en_qr
    assert auditorias("CONSULTA_RUT_QR") == []


def test_qr_consulta_de_rut_existente_enmascara_y_audita(proceso):
    postulante = precargar(telefono=None)
    consulta = inicio.consultar_qr("12345678-5")
    assert consulta["vista"]["nombres"] == "Ju*** An***"
    assert consulta["vista"]["correo_personal"] == "j***@gmail.com"
    assert consulta["vista"]["carrera1"] == "ENF"
    assert consulta["pendientes"] == ["telefono", "carrera2", "carrera3"]
    registro = auditorias("CONSULTA_RUT_QR")[0]
    assert registro.actor == "PUBLICO" and registro.usuario_id is None and registro.entidad_id == postulante.id


def test_qr_sin_enmascarar_muestra_datos_completos(admin):
    armar_proceso(admin, enmascarar_datos_qr="")
    precargar()
    assert inicio.consultar_qr("12345678-5")["vista"]["nombres"] == "Juan Andrés"


def test_qr_rut_nuevo_queda_en_espera_en_iniciado_con_ruta_propuesta(proceso):
    becas = fase(proceso, "BEC")
    expediente = inicio.iniciar_por_qr(datos(), [str(becas.id)])
    postulante = m.Postulante.get_by_id(expediente.postulante_id)
    assert postulante.origen == "INICIO_QR"
    assert expediente.via_inicio == "QR" and expediente.iniciado_por_id is None
    assert ruta_de(expediente) == [("INI", wf.EN_ESPERA), ("BEC", wf.PLANIFICADA), ("MNP", wf.PLANIFICADA)]
    registro = auditorias("INICIAR_PROCESO")[0]
    assert registro.actor == "PUBLICO" and registro.valor_nuevo["via"] == "QR"


def test_qr_solo_completa_pendientes_y_no_cambia_datos_registrados(proceso):
    postulante = precargar(telefono=None)
    inicio.iniciar_por_qr(datos(rut="12345678-5", nombres="Otro Nombre", correo_personal="otro@mail.com",
                                telefono="+56911112222", carrera1="DER", carrera2="PSI"), [])
    postulante = m.Postulante.get_by_id(postulante.id)
    assert postulante.nombres == "Juan Andrés" and postulante.correo_personal == "juan.perez@gmail.com"
    assert postulante.telefono == "+56911112222"
    actuales = inicio.datos_de(postulante)
    assert (actuales["carrera1"], actuales["carrera2"]) == ("ENF", "PSI")
    registro = auditorias("COMPLETAR_POSTULANTE")[0]
    assert registro.actor == "PUBLICO"
    assert registro.valor_nuevo == {"telefono": "+56911112222", "carrera2": "PSI"}


def test_qr_exige_los_pendientes_obligatorios(proceso):
    precargar(telefono=None)
    with pytest.raises(inicio.DatosInvalidosError) as error:
        inicio.iniciar_por_qr(datos(rut="12345678-5", telefono=""), [])
    assert set(error.value.campos) == {"telefono"}
    assert m.Expediente.select().count() == 0


def test_qr_no_acepta_temas_no_ofrecidos(proceso):
    with pytest.raises(ReglaVioladaError) as error:
        inicio.iniciar_por_qr(datos(), [str(fase(proceso, "FIN").id)])
    assert error.value.codigo_http == 400
    with pytest.raises(ReglaVioladaError):
        inicio.iniciar_por_qr(datos(), ["abc"])
    assert m.Expediente.select().count() == 0 and m.Postulante.select().count() == 0


def test_qr_no_crea_un_segundo_expediente(admin, proceso):
    primero = inicio.iniciar_por_staff(datos(), {"consultas": []}, admin)
    with pytest.raises(inicio.ExpedienteExistenteError) as error:
        inicio.iniciar_por_qr(datos(), [])
    assert error.value.expediente.id == primero.id
    assert inicio.consultar_qr("12345678-5")["expediente"].id == primero.id
    assert inicio.enmascarar_correo_de(primero) == "j***@gmail.com"


# ── Seguimiento (RF-20) ──

def test_vista_de_seguimiento_no_expone_datos_personales(admin, proceso):
    expediente = inicio.iniciar_por_staff(datos(carrera2="KIN"), {"consultas": [fase(proceso, "BEC").id]}, admin)
    vista = seguimiento.vista(seguimiento.por_token(expediente.token_seguimiento))
    assert vista["numero"] == "M-0001" and vista["primer_nombre"] == "Juan"
    assert vista["instruccion"] == "Dirígete a: Becas"
    assert vista["carreras"] == ["Enfermería", "Kinesiología"]
    assert [(p["fase"], p["estado"]) for p in vista["pasos"]] == [
        ("Iniciado", "hecho"), ("Becas", "actual"), ("MNP", "pendiente")]
    assert not vista["matriculado"]
    texto = repr(vista)
    for dato in ("12345678-5", "juan.perez@gmail.com", "56912345678", "Pérez"):
        assert dato not in texto


def test_seguimiento_de_proceso_cerrado_y_token_invalido(admin, proceso):
    expediente = inicio.iniciar_por_staff(datos(), {"consultas": []}, admin)
    assert seguimiento.por_token("no-existe") is None
    assert seguimiento.por_token("x" * 200) is None
    cfg.cerrar_proceso(proceso.id, admin)
    assert seguimiento.vista(m.Expediente.get_by_id(expediente.id)) == {"finalizado": True}
