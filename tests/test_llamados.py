"""Llamados a módulo y pantalla del hall de espera."""

from datetime import timedelta

import pytest

import seed_carreras
from app import create_app
from app import models as m
from app.config import TestConfig
from app.services import bandeja, llamados
from app.services import workflow as wf
from app.services.workflow import ReglaVioladaError
from tests.test_admin import entrar, texto
from tests.test_bandeja import T0, iniciar, usuario_de, version
from tests.test_inicio import admin, armar_proceso, auditorias, ctx  # noqa: F401


@pytest.fixture
def proceso(admin):
    return armar_proceso(admin)


@pytest.fixture
def becas(proceso):
    usuario = usuario_de(proceso, "BEC")
    llamados.guardar_modulo(usuario, "4")
    return usuario


def en(segundos):
    return T0 + timedelta(seconds=segundos)


# ── Módulo del usuario ──

def test_guardar_modulo_normaliza_y_valida(becas):
    assert llamados.guardar_modulo(becas, "  Módulo   7 ") == "7"
    assert m.Usuario.get_by_id(becas.id).modulo == "7"
    assert llamados.guardar_modulo(becas, "") is None
    with pytest.raises(ReglaVioladaError):
        llamados.guardar_modulo(becas, "x" * 21)


def test_sin_modulo_no_puede_llamar(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    sin_modulo = usuario_de(proceso, "BEC")
    with pytest.raises(ReglaVioladaError) as error:
        llamados.llamar(expediente.id, sin_modulo, ahora=T0)
    assert "módulo" in error.value.mensaje


# ── Llamar ──

def test_llamar_aparece_en_pantalla_y_se_audita(admin, proceso, becas):
    expediente = iniciar(admin, consultas=["BEC"])
    assert llamados.llamar(expediente.id, becas, ahora=T0) == "Llamando a M-0001 al módulo 4"
    estado = llamados.pantalla(proceso, ahora=en(2))
    assert [(ll["numero"], ll["fase"], ll["modulo"], ll["aviso"]) for ll in estado["llamando"]] == [
        ("M-0001", "Becas", "4", 1)]
    assert estado["llamando"][0]["voz"] == "Número M 1. Diríjase a Becas, módulo 4."
    assert auditorias("LLAMAR")[0].valor_nuevo == {"fase": "Becas", "modulo": "4"}


def test_el_aviso_se_repite_cada_30_segundos_hasta_tres_veces(admin, proceso, becas):
    expediente = iniciar(admin, consultas=["BEC"])
    llamados.llamar(expediente.id, becas, ahora=T0)

    def aviso(segundos):
        llamando = llamados.pantalla(proceso, ahora=en(segundos))["llamando"]
        return (llamando[0]["aviso"], llamando[0]["clave"], llamando[0]["hace"]) if llamando else None

    primero, segundo, tercero = aviso(29), aviso(30), aviso(65)
    assert (primero[0], segundo[0], tercero[0]) == (1, 2, 3)
    assert len({primero[1], segundo[1], tercero[1]}) == 3  # cada aviso se anuncia una vez
    assert segundo[2] == 0 and tercero[2] == 5
    assert aviso(89)[0] == 3
    assert aviso(90) is None  # sin respuesta: deja de mostrarse en la pantalla


def test_bandeja_muestra_el_llamado_y_cuando_no_se_presento(admin, proceso, becas):
    expediente = iniciar(admin, consultas=["BEC"])
    llamados.llamar(expediente.id, becas, ahora=T0)
    fila = bandeja.listar(becas, proceso, ahora=en(31))["en_espera"][0]
    assert fila["llamado"]["modulo"] == "4" and fila["llamado"]["aviso"] == 2
    assert not fila["llamado"]["sin_respuesta"]
    fila = bandeja.listar(becas, proceso, ahora=en(95))["en_espera"][0]
    assert fila["llamado"]["sin_respuesta"]


def test_volver_a_llamar_reinicia_los_avisos(admin, proceso, becas):
    expediente = iniciar(admin, consultas=["BEC"])
    llamados.llamar(expediente.id, becas, ahora=T0)
    llamados.llamar(expediente.id, becas, ahora=en(120))
    llamando = llamados.pantalla(proceso, ahora=en(121))["llamando"]
    assert len(llamando) == 1 and llamando[0]["aviso"] == 1
    assert m.Llamado.select().count() == 1
    assert len(auditorias("VOLVER_A_LLAMAR")) == 1


def test_otro_responsable_no_puede_llamar_a_quien_ya_fue_llamado(admin, proceso, becas):
    expediente = iniciar(admin, consultas=["BEC"])
    llamados.llamar(expediente.id, becas, ahora=T0)
    otro = usuario_de(proceso, "BEC", email="otro@unach.cl")
    llamados.guardar_modulo(otro, "5")
    with pytest.raises(ReglaVioladaError) as error:
        llamados.llamar(expediente.id, otro, ahora=en(10))
    assert "ya fue llamado al módulo 4" in error.value.mensaje
    # Si no se presentó, otro responsable puede llamarlo a su módulo
    llamados.llamar(expediente.id, otro, ahora=en(100))
    assert llamados.pantalla(proceso, ahora=en(101))["llamando"][0]["modulo"] == "5"


def test_solo_el_responsable_de_la_fase_actual_puede_llamar(admin, proceso):
    expediente = iniciar(admin, consultas=["BEC"])
    finanzas = usuario_de(proceso, "FIN", email="fin@unach.cl")
    llamados.guardar_modulo(finanzas, "2")
    with pytest.raises(ReglaVioladaError) as error:
        llamados.llamar(expediente.id, finanzas, ahora=T0)
    assert error.value.codigo_http == 403


def test_no_se_llama_a_quien_ya_esta_en_atencion(admin, proceso, becas):
    expediente = iniciar(admin, consultas=["BEC"])
    bandeja.atender(expediente.id, version(expediente), becas, ahora=T0)
    with pytest.raises(ReglaVioladaError):
        llamados.llamar(expediente.id, becas, ahora=T0)


# ── Cierre del llamado ──

def test_atender_cierra_el_llamado_y_pasa_a_en_atencion_con_su_modulo(admin, proceso, becas):
    expediente = iniciar(admin, consultas=["BEC"])
    llamados.llamar(expediente.id, becas, ahora=T0)
    llamados.guardar_modulo(becas, "9")  # cambió de módulo después de llamar: vale el del llamado
    bandeja.atender(expediente.id, version(expediente), becas, ahora=en(10))
    estado = llamados.pantalla(proceso, ahora=en(11))
    assert estado["llamando"] == []
    assert estado["atendiendo"] == [{"numero": "M-0001", "fase": "Becas", "modulo": "4"}]
    assert m.Llamado.get().cerrado_en is not None


def test_atendido_sin_llamar_muestra_el_modulo_de_quien_atiende(admin, proceso, becas):
    expediente = iniciar(admin, consultas=["BEC"])
    bandeja.atender(expediente.id, version(expediente), becas, ahora=T0)
    assert llamados.pantalla(proceso, ahora=T0)["atendiendo"][0]["modulo"] == "4"


def test_cancelar_deja_al_postulante_en_espera(admin, proceso, becas):
    expediente = iniciar(admin, consultas=["BEC"])
    llamados.llamar(expediente.id, becas, ahora=T0)
    assert "sigue en espera" in llamados.cancelar(expediente.id, becas, ahora=en(5))
    assert llamados.pantalla(proceso, ahora=en(6))["llamando"] == []
    assert bandeja.listar(becas, proceso, ahora=en(6))["en_espera"][0]["llamado"] is None
    assert len(auditorias("CANCELAR_LLAMADO")) == 1
    with pytest.raises(ReglaVioladaError):
        llamados.cancelar(expediente.id, becas, ahora=en(7))


def test_abandono_cierra_el_llamado(admin, proceso, becas):
    expediente = iniciar(admin, consultas=["BEC"])
    llamados.llamar(expediente.id, becas, ahora=T0)
    bandeja.marcar_abandono(expediente.id, version(expediente), becas, ahora=en(100))
    assert m.Llamado.get().cerrado_en is not None
    assert llamados.pantalla(proceso, ahora=en(101))["llamando"] == []


def test_varios_responsables_de_una_fase_llaman_a_modulos_distintos(admin, proceso, becas):
    uno = iniciar(admin, cuerpo=11111111, consultas=["BEC"])
    dos = iniciar(admin, cuerpo=22222222, consultas=["BEC"])
    otro = usuario_de(proceso, "BEC", email="otro@unach.cl")
    llamados.guardar_modulo(otro, "5")
    llamados.llamar(uno.id, becas, ahora=T0)
    llamados.llamar(dos.id, otro, ahora=en(3))
    llamando = llamados.pantalla(proceso, ahora=en(4))["llamando"]
    assert [(ll["numero"], ll["modulo"]) for ll in llamando] == [("M-0002", "5"), ("M-0001", "4")]


def test_pantalla_sin_proceso_activo():
    assert llamados.pantalla(None) == {"proceso": None, "llamando": [], "atendiendo": [],
                                       "intervalo": llamados.INTERVALO}


# ── Pantallas ──

@pytest.fixture
def app(bd):
    aplicacion = create_app(TestConfig)
    with aplicacion.app_context():
        seed_carreras.sembrar()
    return aplicacion


def test_pantalla_es_publica_y_sin_datos_personales(app):
    with app.app_context():
        jefe = m.Usuario.create(email="jefe@unach.cl", nombre="Jefe", rol="ADMIN", must_change_password=False)
        proceso = armar_proceso(jefe)
        expediente = iniciar(jefe, consultas=["BEC"])
        becas = usuario_de(proceso, "BEC")
        llamados.guardar_modulo(becas, "4")
        llamados.llamar(expediente.id, becas)
    cliente = app.test_client()
    pagina = cliente.get("/pantalla/")
    assert pagina.status_code == 200 and "Activar sonido" in texto(pagina)
    estado = cliente.get("/pantalla/estado")
    assert estado.status_code == 200 and estado.headers["Cache-Control"] == "no-store"
    assert estado.json["llamando"][0]["numero"] == "M-0001"
    assert "Pérez" not in texto(estado) and "Juan" not in texto(estado) and "Pérez" not in texto(pagina)


def test_bandeja_llamar_cancelar_y_modulo(app):
    with app.app_context():
        jefe = m.Usuario.create(email="jefe@unach.cl", nombre="Jefe", rol="ADMIN", must_change_password=False)
        proceso = armar_proceso(jefe)
        expediente = iniciar(jefe, consultas=["BEC"])
        becas = usuario_de(proceso, "BEC")
    cliente = entrar(app.test_client(), becas)

    respuesta = cliente.post(f"/bandeja/{expediente.id}/llamar", follow_redirects=True)
    assert "Indique su módulo" in texto(respuesta)
    respuesta = cliente.post("/bandeja/modulo", data={"modulo": "4"}, follow_redirects=True)
    assert "Su módulo es el 4" in texto(respuesta)

    respuesta = cliente.post(f"/bandeja/{expediente.id}/llamar", follow_redirects=True)
    pagina = texto(respuesta)
    assert "Llamando a M-0001 al módulo 4" in pagina and "Llamado 1 de 3" in pagina and "Cancelar llamado" in pagina

    respuesta = cliente.post(f"/bandeja/{expediente.id}/cancelar-llamado", follow_redirects=True)
    assert "sigue en espera" in texto(respuesta)
    with app.app_context():
        assert m.Llamado.get().cerrado_en is not None
        assert m.Atencion.get_by_id(m.Llamado.get().atencion_id).estado == wf.EN_ESPERA


# ── Seguimiento del postulante ──

def instruccion(expediente):
    from app.services import seguimiento
    return seguimiento.vista(m.Expediente.get_by_id(expediente.id))["instruccion"]


def test_el_movil_indica_el_modulo_al_ser_llamado(admin, proceso, becas):
    from app.services import seguimiento
    expediente = iniciar(admin, consultas=["BEC"])
    token = expediente.token_seguimiento
    assert instruccion(expediente) == "Dirígete a: Becas"  # espera en el hall
    antes = seguimiento.firma(token)
    llamados.llamar(expediente.id, becas, ahora=T0)
    assert seguimiento.firma(token) != antes  # el sondeo de 10 s lo nota
    assert instruccion(expediente) == "Dirígete a: Becas, Módulo 4"
    llamados.cancelar(expediente.id, becas, ahora=en(5))
    assert instruccion(expediente) == "Dirígete a: Becas"


def test_el_movil_indica_el_modulo_en_atencion(admin, proceso, becas):
    expediente = iniciar(admin, consultas=["BEC"])
    llamados.llamar(expediente.id, becas, ahora=T0)
    llamados.guardar_modulo(becas, "9")  # vale el módulo del llamado
    bandeja.atender(expediente.id, version(expediente), becas, ahora=en(10))
    assert instruccion(expediente) == "Estás siendo atendido en: Becas, Módulo 4"


def test_atendido_sin_llamar_el_movil_indica_el_modulo_de_quien_atiende(admin, proceso, becas):
    expediente = iniciar(admin, consultas=["BEC"])
    bandeja.atender(expediente.id, version(expediente), becas, ahora=T0)
    assert instruccion(expediente) == "Estás siendo atendido en: Becas, Módulo 4"
