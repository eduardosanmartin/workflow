"""Criterios de aceptación de la §11 que no tenían una prueba directa en otros archivos."""

import io
from datetime import timedelta

import openpyxl
import pytest

from app import models as m
from app.services import bandeja, correo, inicio
from app.services import carga_masiva as cm
from app.services import configuracion as cfg
from app.services.inicio import DatosInvalidosError
from tests.test_admin import entrar, texto
from tests.test_bandeja import T0, finalizar, iniciar, version
from tests.test_carga_masiva import ENCABEZADO
from tests.test_inicio import armar_proceso, datos, fase
from tests.test_inicio_pantallas import admin, app  # noqa: F401


@pytest.fixture
def proceso(app, admin):
    with app.app_context():
        return armar_proceso(admin)


@pytest.fixture(autouse=True)
def carpeta_de_cargas(app, tmp_path):
    app.config["CARGAS_DIR"] = str(tmp_path)


def ver_seguimiento(app, expediente):
    return texto(app.test_client().get(f"/seguimiento/{expediente.token_seguimiento}"))


# ── Recorrido y derivación ──

def test_abandono_deja_la_atencion_en_pausa_en_el_seguimiento(app, admin, proceso):
    """«...su vista de seguimiento muestra que la atención quedó en pausa»."""
    with app.app_context():
        expediente = iniciar(admin, consultas=["BEC"])
        bandeja.marcar_abandono(expediente.id, version(expediente), admin, ahora=T0)
    html = ver_seguimiento(app, expediente)
    assert "Tu atención quedó en pausa" in html and "Dirígete a" not in html


def test_matriculado_con_observaciones_el_postulante_ve_matriculado(app, admin, proceso):
    """«...aparece como "Matriculado con observaciones" en los reportes, y el postulante ve "Matriculado"»."""
    with app.app_context():
        expediente = iniciar(admin, consultas=["BEC"])
        finalizar(expediente, admin, sin_atender="on", resultado="observaciones", observacion="Falta firma")
        finalizar(expediente, admin, sin_atender="on")
        assert m.Expediente.get_by_id(expediente.id).con_observaciones
    html = ver_seguimiento(app, expediente)
    assert "Matriculado" in html and "observaci" not in html.lower() and "Falta firma" not in html


# ── Flujos alternativos ──

def test_una_fase_puede_estar_en_el_principal_y_en_varios_flujos(app, admin):
    """«Una fase de consulta puede estar en el flujo principal y en varios flujos alternativos»."""
    with app.app_context():
        proceso = armar_proceso(admin, activar=False)
        caja = fase(proceso, "CAJA").id
        cfg.crear_flujo(proceso.id, {"codigo": "TRF", "nombre": "Transferencia", "fases": [caja]}, admin)
        assert m.FlujoAlternativo.select().where(m.FlujoAlternativo.proceso == proceso.id).count() == 2
        usos = m.FlujoAlternativoFase.select().where(m.FlujoAlternativoFase.fase == caja).count()
        assert usos == 2
        cfg.activar_proceso(proceso.id, admin)
        expediente = iniciar(admin, consultas=["CAJA"])  # y también en el flujo principal
        assert m.Atencion.select().where((m.Atencion.expediente == expediente.id)
                                         & (m.Atencion.fase == caja)).exists()


# ── Correo y seguimiento ──

def test_el_enlace_del_correo_y_el_del_qr_son_el_mismo(app, admin, proceso):
    publico = app.test_client()
    publico.post("/qr/rut", data={"rut": datos()["rut"]})  # el QR exige pasar antes por el RUT
    respuesta = publico.post("/qr/iniciar", data=datos())
    with app.app_context():
        expediente = m.Expediente.get()
        cuerpo = correo.construir(expediente, m.Postulante.get_by_id(expediente.postulante_id))
        texto_plano = cuerpo.get_body(("plain",)).get_content()
    ruta = respuesta.headers["Location"]
    assert ruta == f"/seguimiento/{expediente.token_seguimiento}"
    assert f"{app.config['APP_BASE_URL']}{ruta}" in texto_plano


# ── Datos y carga masiva ──

@pytest.mark.parametrize("vacio", ["correo_personal", "carrera1"])
def test_sin_correo_o_sin_carrera_1_no_se_registra_por_ninguna_via(app, admin, proceso, vacio):
    """Staff y carga manual lo prueban test_inicio y test_registro; aquí, QR y carga masiva."""
    with app.app_context():
        with pytest.raises(DatosInvalidosError) as error:
            inicio.iniciar_por_qr(datos(**{vacio: ""}), [])
        assert vacio in error.value.campos
        fila = "11.111.111-1;Ana;Paz;+56911111111;{};{};;".format(
            "" if vacio == "correo_personal" else "a@b.cl", "" if vacio == "carrera1" else "ENF")
        token = cm.analizar_archivo("\n".join((ENCABEZADO, fila)).encode(), "c.csv", admin)
        previa = cm.vista_previa(token, admin)
        assert previa["nuevos"] == [] and len(previa["errores"]) == 1 and previa["errores"][0]["fila"] == 2
        assert m.Postulante.select().count() == 0


def test_rut_con_k_y_con_ceros_a_la_izquierda_en_excel(app, admin):
    libro = openpyxl.Workbook()
    libro.active.append(ENCABEZADO.split(";"))
    libro.active.append(["010.000.013-k", "Ana", "Paz", "+56911111111", "a@b.cl", "ENF", None, None])
    libro.active.append(["07654321-6", "Luis", "Soto", "+56922222222", "l@s.cl", "ENF", None, None])
    salida = io.BytesIO()
    libro.save(salida)
    with app.app_context():
        token = cm.analizar_archivo(salida.getvalue(), "c.xlsx", admin)
        previa = cm.vista_previa(token, admin)
    assert previa["errores"] == [] and [n["rut"] for n in previa["nuevos"]] == ["10000013-K", "7654321-6"]


def test_la_carga_manual_no_crea_expedientes_ni_envia_correos(app, admin, proceso):
    staff = entrar(app.test_client(), admin)
    assert staff.post("/postulantes/nuevo", data=datos()).status_code == 302
    assert m.Postulante.select().count() == 1
    assert m.Expediente.select().count() == 0 and m.EnvioCorreo.select().count() == 0


# ── Auditoría ──

def test_cada_accion_genera_exactamente_un_registro(app, admin, proceso):
    """«Cada acción de la §5.9 genera exactamente un registro de auditoría» (las principales)."""
    def cuantos(accion):
        return m.Auditoria.select().where(m.Auditoria.accion == accion).count()

    with app.app_context():
        expediente = iniciar(admin, consultas=["BEC"])
        assert (cuantos("INICIAR_PROCESO"), cuantos("CREAR_POSTULANTE"), cuantos("ENVIAR_CORREO")) == (1, 1, 1)
        bandeja.atender(expediente.id, version(expediente), admin, ahora=T0 + timedelta(minutes=1))
        assert cuantos("ATENDER") == 1
        finalizar(expediente, admin, ahora=T0 + timedelta(minutes=2))
        assert cuantos("FINALIZAR_ATENCION") == 1
    cliente = app.test_client()
    cliente.post("/login", data={"email": "admin@unach.cl", "password": "incorrecta-123"})
    assert cuantos("LOGIN_FALLIDO") == 1
