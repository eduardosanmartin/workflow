"""Consulta de la auditoría (§10, pantalla 12; RF-24): filtros, alcance del Gestor,
detalle de cambios, paginación y exportación."""

import io
from datetime import datetime, timedelta, timezone

import openpyxl
import pytest

from app import models as m
from app.services import auditoria
from app.services import consulta_auditoria as ca
from tests.test_admin import entrar, texto, usuario
from tests.test_inicio import precargar
from tests.test_inicio_pantallas import admin, app  # noqa: F401

T0 = datetime(2027, 1, 15, 13, 0, tzinfo=timezone.utc)  # 10:00 en Chile


@pytest.fixture
def registros(app, admin):
    """Un registro de cada tipo de entidad, con fechas conocidas."""
    with app.app_context():
        gestor = usuario("g@unach.cl", rol="GESTOR")
        postulante = precargar()
        filas = [
            ("LOGIN", "usuario", admin.id, None, None, admin, None, T0),
            ("EDITAR_PROCESO", "proceso", 1, {"nombre": "A"}, {"nombre": "B"}, admin, None, T0 + timedelta(minutes=1)),
            ("EDITAR_POSTULANTE", "postulante", postulante.id, {"telefono": "+56911111111", "nombres": "Juan"},
             {"telefono": "+56922222222", "nombres": "Juan"}, gestor, None, T0 + timedelta(days=1)),
            ("CONSULTA_RUT_QR", "postulante", postulante.id, None, {"rut": "12345678-5"}, None, None,
             T0 + timedelta(days=2)),
            ("ENVIAR_CORREO", "expediente", 999999, None, {"tipo": "INICIO", "estado": "ERROR"}, None, "SISTEMA",
             T0 + timedelta(days=2, minutes=1)),
        ]
        for accion, entidad, entidad_id, anterior, nuevo, quien, actor, fecha in filas:
            registro = auditoria.registrar(accion, entidad, entidad_id, anterior, nuevo, usuario=quien, actor=actor)
            m.Auditoria.update(fecha_hora=fecha).where(m.Auditoria.id == registro.id).execute()
        return {"gestor": gestor, "postulante": postulante}


def buscar(quien, **args):
    return ca.buscar(ca.leer_filtros(args, quien), quien)


def acciones(resultado):
    return [f["codigo_accion"] for f in resultado["filas"]]


# ── Alcance por rol ──

def test_administrador_ve_todo_lo_mas_reciente_primero(app, admin, registros):
    with app.app_context():
        assert acciones(buscar(admin)) == ["ENVIAR_CORREO", "CONSULTA_RUT_QR", "EDITAR_POSTULANTE",
                                           "EDITAR_PROCESO", "LOGIN"]


def test_gestor_solo_ve_expedientes_postulantes_y_cargas(app, registros):
    gestor = registros["gestor"]
    with app.app_context():
        assert acciones(buscar(gestor)) == ["ENVIAR_CORREO", "CONSULTA_RUT_QR", "EDITAR_POSTULANTE"]
        assert acciones(buscar(gestor, entidad="usuario")) == acciones(buscar(gestor))  # se ignora
        assert acciones(buscar(gestor, accion="LOGIN")) == []
        opciones = ca.opciones(gestor)
        assert [c for c, _ in opciones["entidades"]] == ["expediente", "postulante", "carga_masiva"]
        assert "LOGIN" not in dict(opciones["acciones"]) and "EDITAR_POSTULANTE" in dict(opciones["acciones"])
    assert not ca.puede_ver(m.Usuario(rol="USUARIO_FASE")) and not ca.puede_ver(None)


def test_todas_las_acciones_auditadas_tienen_nombre():
    """Cada acción que el código registra aparece con su nombre en el filtro de la pantalla."""
    import pathlib
    import re
    codigo = "".join(p.read_text(encoding="utf-8") for p in pathlib.Path("app").rglob("*.py"))
    registradas = set(re.findall(r'registrar(?:_lote)?\(\s*"([A-Z_]+)"', codigo))
    registradas |= set(re.findall(r'"accion": "([A-Z_]+)"', codigo))
    for par in re.findall(r'registrar\(\s*"([A-Z_]+)" if \w+ else "([A-Z_]+)"', codigo):
        registradas |= set(par)
    registradas |= set(re.findall(r'(?:_operar|operar_auditado)\([^"]*"([A-Z_]+)"', codigo))
    assert registradas and registradas <= set(ca.ACCIONES), registradas - set(ca.ACCIONES)
    assert set(ca.ACCIONES_GESTOR) <= set(ca.ACCIONES)


# ── Filtros ──

def test_filtros(app, admin, registros):
    with app.app_context():
        assert acciones(buscar(admin, desde="2027-01-16", hasta="2027-01-16")) == ["EDITAR_POSTULANTE"]
        assert acciones(buscar(admin, usuario=str(registros["gestor"].id))) == ["EDITAR_POSTULANTE"]
        assert acciones(buscar(admin, usuario="PUBLICO")) == ["CONSULTA_RUT_QR"]
        assert acciones(buscar(admin, usuario="SISTEMA")) == ["ENVIAR_CORREO"]
        assert acciones(buscar(admin, accion="EDITAR_PROCESO")) == ["EDITAR_PROCESO"]
        assert acciones(buscar(admin, entidad="postulante", entidad_id=str(registros["postulante"].id))) == [
            "CONSULTA_RUT_QR", "EDITAR_POSTULANTE"]
        filtros = ca.leer_filtros({"desde": "2027-02-01", "hasta": "2027-01-01", "entidad_id": "abc",
                                   "accion": "INVENTADA", "usuario": "x"}, admin)
        assert filtros.desde is None and filtros.accion is None and filtros.usuario is None
        assert len(filtros.errores) == 2


def test_dia_segun_hora_de_chile(app, admin):
    with app.app_context():
        registro = auditoria.registrar("LOGIN", "usuario", admin.id, usuario=admin)
        # 23:30 del 15 en Chile son las 02:30 UTC del 16
        m.Auditoria.update(fecha_hora=datetime(2027, 1, 16, 2, 30, tzinfo=timezone.utc)).where(
            m.Auditoria.id == registro.id).execute()
        assert buscar(admin, desde="2027-01-15", hasta="2027-01-15")["total"] == 1
        assert buscar(admin, desde="2027-01-16")["total"] == 0


# ── Presentación ──

def test_detalle_marca_los_campos_que_cambiaron_y_enlaza_la_ficha(app, admin, registros):
    with app.app_context():
        fila = buscar(admin, accion="EDITAR_POSTULANTE")["filas"][0]
    assert fila["quien"] == registros["gestor"].nombre and fila["rol"] == "GESTOR" and fila["accion"] == "Edición de postulante"
    assert fila["fecha"] == "16-01-2027 10:00" and fila["ficha"] == registros["postulante"].id
    assert fila["campos"] == [
        {"campo": "Teléfono", "anterior": "+56911111111", "nuevo": "+56922222222", "cambio": True},
        {"campo": "Nombres", "anterior": "Juan", "nuevo": "Juan", "cambio": False},
    ]
    with app.app_context():
        correo = buscar(admin, accion="ENVIAR_CORREO")["filas"][0]
    assert correo["quien"] == "Sistema" and correo["ficha"] is None  # el expediente ya no existe
    assert [c["cambio"] for c in correo["campos"]] == [False, False]  # sin valor anterior: no hay cambios


def test_paginacion(app, admin):
    with app.app_context():
        auditoria.registrar_lote("LOGIN", "usuario", [(admin.id, None, None)] * (ca.POR_PAGINA + 5), usuario=admin)
        primera = buscar(admin)
        assert primera["total"] == ca.POR_PAGINA + 5 and primera["paginas"] == 2
        assert len(primera["filas"]) == ca.POR_PAGINA
        assert len(buscar(admin, pagina="2")["filas"]) == 5
        assert buscar(admin, pagina="99")["pagina"] == 2


# ── Exportación ──

def test_exportar_respeta_filtros_y_alcance_y_queda_auditado(app, admin, registros):
    gestor = registros["gestor"]
    with app.app_context():
        contenido, tipo, nombre = ca.exportar(ca.leer_filtros({}, gestor), "csv", gestor)
        texto_csv = contenido.decode("utf-8-sig")
        assert tipo == "text/csv" and nombre.startswith("auditoria_")
        assert "Edición de postulante" in texto_csv and "Inicio de sesión" not in texto_csv
        contenido, tipo, _ = ca.exportar(ca.leer_filtros({"accion": "LOGIN"}, admin), "xlsx", admin)
        filas = list(openpyxl.load_workbook(io.BytesIO(contenido)).active.iter_rows(values_only=True))
        assert filas[0][0] == "Fecha" and [f[3] for f in filas[1:]] == ["Inicio de sesión"]
        exportaciones = list(m.Auditoria.select().where(m.Auditoria.accion == "EXPORTAR_AUDITORIA")
                             .order_by(m.Auditoria.id))
        assert [e.valor_nuevo["registros"] for e in exportaciones] == [3, 1]
        assert exportaciones[1].valor_nuevo["filtros"]["accion"] == "LOGIN"


def test_valores_legibles():
    assert ca._texto_valor("EN_ESPERA") == "En espera" and ca._texto_valor(True) == "Sí"
    assert ca._texto_valor(["Becas", "MNP"]) == "Becas, MNP" and ca._texto_valor("") == "(vacío)"
    assert ca._texto_valor({"a": 1}) == '{"a": 1}'


def test_celdas_peligrosas_se_neutralizan():
    assert ca._celda("=HYPERLINK(1)") == "'=HYPERLINK(1)" and ca._celda("Juan") == "Juan" and ca._celda(5) == 5


# ── Pantalla ──

def test_pantalla_y_permisos(app, admin, registros):
    staff = entrar(app.test_client(), admin)
    assert 'href="/auditoria/"' in texto(staff.get("/"))
    respuesta = staff.get("/auditoria/?usuario=SISTEMA")
    html = texto(respuesta)
    assert respuesta.status_code == 200 and respuesta.headers["Cache-Control"] == "no-store"
    assert "1 registro" in html and "Envío de correo" in html and "Inicio de sesión</td>" not in html
    assert "Quitar filtros" in html and "/auditoria/exportar?usuario=SISTEMA&amp;formato=xlsx" in html

    html = texto(staff.get("/auditoria/?accion=EDITAR_POSTULANTE"))
    assert f'href="/ficha/{registros["postulante"].id}"' in html and "+56922222222" in html

    gestor = entrar(app.test_client(), registros["gestor"])
    html = texto(gestor.get("/auditoria/"))
    assert "Solo lectura" in html and "Edición de proceso" not in html
    assert gestor.get("/auditoria/exportar?formato=csv").status_code == 200

    fase = entrar(app.test_client(), usuario("f@unach.cl", rol="USUARIO_FASE"))
    assert 'href="/auditoria/"' not in texto(fase.get("/"))
    assert fase.get("/auditoria/").status_code == 403
    assert fase.get("/auditoria/exportar?formato=csv").status_code == 403
    assert staff.get("/auditoria/exportar?formato=pdf").status_code == 404
