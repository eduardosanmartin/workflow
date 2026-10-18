"""Reportería (RF-18): un escenario con recorridos conocidos y lo que muestra cada reporte."""

import io
from datetime import timedelta

import openpyxl
import pytest

from app import models as m
from app.services import bandeja, ficha
from app.services import configuracion as cfg
from app.services import reportes as r
from app.services.reportes import Duracion
from tests.test_bandeja import T0, finalizar, iniciar, rut, usuario_de, version
from tests.test_inicio import admin, armar_proceso, auditorias, ctx, fase, precargar  # noqa: F401


def minutos(n):
    return T0 + timedelta(minutes=n)


def responsable(proceso, nombre, email, *identificadores):
    usuario = m.Usuario.create(email=email, nombre=nombre, rol="USUARIO_FASE", must_change_password=False)
    for identificador in identificadores:
        m.FaseResponsable.create(fase=fase(proceso, identificador), usuario=usuario)
    return usuario


@pytest.fixture
def proceso(admin):
    return armar_proceso(admin)


@pytest.fixture
def escenario(admin, proceso):
    """e1 directa; e2 con Becas y observaciones; e3 abandona; e4 abandona y se reactiva;
    e5 por el flujo «Pago en efectivo» con cierre manual; y un cargado sin iniciar."""
    becas = usuario_de(proceso, "BEC")
    marta = responsable(proceso, "Marta MNP", "mnp@unach.cl", "MNP")

    e1 = iniciar(admin, 11111111, ahora=minutos(0))
    bandeja.atender(e1.id, version(e1), marta, ahora=minutos(5))
    finalizar(e1, marta, ahora=minutos(15))

    e2 = iniciar(admin, 22222222, ["BEC"], ahora=minutos(1))
    bandeja.atender(e2.id, version(e2), becas, ahora=minutos(11))
    finalizar(e2, becas, ahora=minutos(21), resultado="observaciones", observacion="Falta firma")
    bandeja.atender(e2.id, version(e2), marta, ahora=minutos(31))
    finalizar(e2, marta, ahora=minutos(41))

    e3 = iniciar(admin, 33333333, ["BEC"], ahora=minutos(2))
    bandeja.marcar_abandono(e3.id, version(e3), becas, ahora=minutos(32))

    e4 = iniciar(admin, 44444444, ["BEC"], ahora=minutos(3))
    bandeja.marcar_abandono(e4.id, version(e4), becas, ahora=minutos(13))
    ficha.reactivar(e4.id, {}, admin, ahora=minutos(20))

    e5 = iniciar(admin, 55555555, ["BEC"], ahora=minutos(4))
    entrada = f"{m.FlujoAlternativo.get().id}:{fase(proceso, 'FIN').id}"
    finalizar(e5, admin, ahora=minutos(10), accion="flujo", entrada=entrada)
    finalizar(e5, admin, ahora=minutos(12), sin_atender="on")
    finalizar(e5, admin, ahora=minutos(20), sin_atender="on", detalle_cierre="Boleta 123", confirmar_cierre="on")

    precargar(rut=rut(66666666), nombres="Sofía", apellidos="Sin Iniciar")
    return {"e1": e1, "e2": e2, "e3": e3, "e4": e4, "e5": e5, "becas": becas, "marta": marta}


def filtros(proceso, **valores):
    return r.leer_filtros({"proceso": str(proceso.id), **valores})


def generar(slug, proceso, ahora=None, **valores):
    return r.generar(slug, filtros(proceso, **valores), ahora=ahora or minutos(60))


def tabla(resultado, titulo):
    return next(t for t in resultado["tablas"] if t["titulo"] == titulo)


def indicadores(resultado):
    return dict(resultado["indicadores"])


def textos(filas):
    return [[str(c) for c in fila] for fila in filas]


# ── Permisos y filtros ──

def test_solo_gestor_y_administrador(admin):
    assert r.puede_ver(admin)
    assert r.puede_ver(m.Usuario(rol="GESTOR"))
    assert not r.puede_ver(m.Usuario(rol="USUARIO_FASE")) and not r.puede_ver(None)


def test_filtros_por_defecto_y_validacion(admin, proceso):
    borrador = armar_proceso(admin, activar=False, codigo="BORR")
    f = r.leer_filtros({})
    assert f.proceso.id == proceso.id and not f.con_fechas and f.errores == []
    assert r.leer_filtros({"proceso": str(borrador.id)}).proceso.id == proceso.id  # los borradores no tienen reportes
    f = r.leer_filtros({"desde": "2027-02-01", "hasta": "2027-01-01", "carrera": "NOEXISTE", "fase": "999999",
                        "ruta": "OTRA"})
    assert f.desde is None and f.carrera is None and f.fase_id is None and f.ruta is None
    assert "posterior" in f.errores[0]
    assert "no es válida" in r.leer_filtros({"desde": "15-01-2027"}).errores[0]


def test_sin_procesos_no_hay_filtros(admin):
    assert r.leer_filtros({}) is None


# ── Reportes ──

def test_estado_de_los_postulantes(escenario, proceso):
    resultado = generar("estado", proceso)
    assert indicadores(resultado) == {"Expedientes": 5, "Pendientes": 2, "Matriculados": 3,
                                      "De ellos, con observaciones": 1}
    assert textos(tabla(resultado, "Pendientes")["filas"]) == [["En curso", "1"], ["Abandonó", "1"],
                                                               ["Proceso pendiente", "0"]]
    assert textos(tabla(resultado, "Matriculados por tipo de cierre")["filas"]) == [
        ["Cierre digital (MNP)", "2"], ["Cierre manual · Pago en efectivo", "1"]]
    filas = tabla(resultado, "Expedientes")["filas"]
    assert [f[4] for f in filas] == ["Matriculado", "Matriculado con observaciones", "Abandonó", "En curso", "Matriculado"]
    assert textos(filas)[4][:2] + textos(filas)[4][5:7] == ["M-0005", "Juan Andrés Pérez Soto", "Caja", "Pago en efectivo"]
    assert filas[0][1].postulante_id == escenario["e1"].postulante_id


def test_estado_al_cierre_muestra_la_fase_detenida(escenario, admin, proceso):
    cfg.cerrar_proceso(proceso.id, admin)
    resultado = generar("estado", proceso)
    assert textos(tabla(resultado, "Proceso pendiente por fase en que quedó detenido")["filas"]) == [["Becas", "2", "1"]]
    estados = [f[4] for f in tabla(resultado, "Expedientes")["filas"]]
    assert "Proceso pendiente (abandonó)" in estados and "Proceso pendiente" in estados


def test_filtros_del_estado(escenario, proceso):
    assert indicadores(generar("estado", proceso, ruta="DIRECTA"))["Expedientes"] == 1
    assert indicadores(generar("estado", proceso, ruta="FLUJO"))["Expedientes"] == 1
    assert indicadores(generar("estado", proceso, ruta="CONSULTAS"))["Expedientes"] == 3
    assert indicadores(generar("estado", proceso, fase=str(fase(proceso, "MNP").id)))["Expedientes"] == 2
    assert indicadores(generar("estado", proceso, carrera="ENF"))["Expedientes"] == 5
    otra = m.Carrera.select().where(m.Carrera.codigo != "ENF").first()
    assert indicadores(generar("estado", proceso, carrera=otra.codigo))["Expedientes"] == 0
    assert indicadores(generar("estado", proceso, desde="2027-01-16"))["Expedientes"] == 0
    assert indicadores(generar("estado", proceso, desde="2027-01-15", hasta="2027-01-15"))["Expedientes"] == 5


def test_postulantes_en_espera_en_tiempo_real(escenario, proceso):
    resultado = generar("espera", proceso)
    assert indicadores(resultado) == {"En espera": 1, "En atención": 0}
    assert textos(tabla(resultado, "Por fase")["filas"]) == [["Becas", "1", "0", "40 min"]]
    fila = tabla(resultado, "Postulantes en espera y en atención")["filas"][0]
    assert textos([fila])[0][:6] == ["Becas", "M-0004", "Juan Andrés Pérez Soto", "En espera", "10:20", "40 min"]


def test_espera_incluye_a_los_que_estan_en_atencion(escenario, proceso):
    e4 = escenario["e4"]
    bandeja.atender(e4.id, version(e4), escenario["becas"], ahora=minutos(50))
    fila = tabla(generar("espera", proceso), "Postulantes en espera y en atención")["filas"][0]
    assert textos([fila])[0][3:8] == ["En atención", "10:20", "30 min", "10 min", "Beatriz Becas"]


def test_atencion_por_fase_y_responsable(escenario, proceso):
    resultado = generar("atencion", proceso)
    por_fase = {f[0]: f for f in tabla(resultado, "Por fase")["filas"]}
    assert por_fase["MNP"][1:] == [2, 0, Duracion(450), Duracion(450), Duracion(600),
                                   Duracion(600), Duracion(600), Duracion(600)]
    assert por_fase["Caja"][1:3] == [1, 1] and por_fase["Caja"][6] == "—"  # sin tiempo de atención
    por_responsable = {f[0]: f[1] for f in tabla(resultado, "Por responsable")["filas"]}
    assert por_responsable["Marta MNP"] == 2 and por_responsable["Beatriz Becas"] == 1
    assert textos(tabla(resultado, "Por hora del día")["filas"])[0][0] == "10:00 a 10:59"
    assert indicadores(generar("atencion", proceso, fase=str(fase(proceso, "MNP").id)))["Atenciones"] == 2


def test_abandonos(escenario, proceso):
    resultado = generar("abandonos", proceso)
    assert indicadores(resultado) == {"Abandonos": 2, "Reactivados": 1, "Espera promedio antes de abandonar": Duracion(1200)}
    assert textos(tabla(resultado, "Por fase")["filas"]) == [["Becas", "2", "1", "20 min", "20 min", "30 min"]]
    assert textos(tabla(resultado, "Por día")["filas"])[0][:3] == ["15-01-2027", "2", "1"]
    detalle = textos(tabla(resultado, "Detalle")["filas"])
    assert [(f[1], f[5], f[6]) for f in detalle] == [("M-0003", "No", "Abandonó"), ("M-0004", "Sí", "En curso")]


def test_tiempo_total_del_recorrido(escenario, proceso):
    filas = textos(tabla(generar("recorrido", proceso), "Tiempo total del recorrido por tipo de ruta")["filas"])
    assert filas == [["Directa", "1", "15 min", "15 min", "15 min"],
                     ["Con consultas", "1", "40 min", "40 min", "40 min"],
                     ["Pago en efectivo", "1", "16 min", "16 min", "16 min"]]


def test_rutas_y_derivaciones(escenario, proceso):
    resultado = generar("rutas", proceso)
    assert indicadores(resultado) == {"Expedientes": 5, "Con ruta directa": "20 %"}
    assert textos(tabla(resultado, "Fases de consulta más visitadas")["filas"]) == [
        ["Becas", "1", "1"], ["Caja", "1", "1"], ["Finanzas", "1", "1"]]
    assert textos(tabla(resultado, "Derivaciones entre fases")["filas"]) == [
        ["Iniciado", "Becas", "4"], ["Becas", "Finanzas", "1"], ["Becas", "MNP", "1"],
        ["Finanzas", "Caja", "1"], ["Iniciado", "MNP", "1"]]


def test_flujos_alternativos(escenario, proceso):
    resultado = generar("flujos", proceso)
    assert textos(tabla(resultado, "Por flujo alternativo")["filas"]) == [["Pago en efectivo", "1", "0", "1"]]
    assert textos(tabla(resultado, "Entradas a los flujos")["filas"]) == [["Pago en efectivo", "Becas", "Finanzas", "1"]]
    assert textos(tabla(resultado, "Cierres manuales por responsable y día")["filas"]) == [["Admin", "15-01-2027", "1"]]
    detalle = textos(tabla(resultado, "Detalle de los cierres manuales")["filas"])[0]
    assert detalle[1:] == ["M-0005", "Juan Andrés Pérez Soto", "Pago en efectivo", "Caja", "Admin", "Boleta 123"]


def test_devolucion_al_flujo_principal(escenario, admin, proceso):
    e = iniciar(admin, 77777777, ["BEC"], ahora=minutos(30))
    entrada = f"{m.FlujoAlternativo.get().id}:{fase(proceso, 'FIN').id}"
    finalizar(e, admin, ahora=minutos(31), accion="flujo", entrada=entrada)
    finalizar(e, admin, ahora=minutos(32), accion="devolver", sin_atender="on", motivo="Paga con tarjeta")
    filas = textos(tabla(generar("flujos", proceso), "Por flujo alternativo")["filas"])
    assert filas == [["Pago en efectivo", "2", "1", "1"]]


def test_por_carrera_inicios_y_productividad(escenario, proceso):
    assert textos(tabla(generar("carreras", proceso), "Por carrera de primera preferencia")["filas"]) == [
        ["Enfermería", "5", "3", "1", "2", "60 %"]]
    inicios = generar("inicios", proceso)
    assert indicadores(inicios) == {"Inicios": 5, "Por QR": 0, "Por staff": 5}
    assert textos(tabla(inicios, "Por día")["filas"]) == [["15-01-2027", "0", "5", "5"]]
    productividad = {f[0]: f[1:] for f in tabla(generar("productividad", proceso), "Por responsable")["filas"]}
    assert productividad == {"Admin": [2, 1, 5], "Beatriz Becas": [1, 1, 0], "Marta MNP": [2, 0, 0]}


def test_cargados_sin_iniciar(escenario, proceso):
    filas = textos(tabla(generar("sin-iniciar", proceso), "Postulantes cargados que aún no inician el proceso")["filas"])
    assert filas == [["66666666-6", "Sofía Sin Iniciar", "Enfermería", "Carga masiva", filas[0][4]]]


def test_correos_fallidos_muestra_el_ultimo_envio_si_fallo(escenario, proceso):
    resultado = generar("correos", proceso)
    filas = tabla(resultado, "Correos con envío fallido")["filas"]
    assert len(filas) == 5  # en las pruebas no hay SMTP: todos fallan
    tipos = {str(f[1]): f[4] for f in filas}
    assert tipos["M-0001"] == "Matrícula completada" and tipos["M-0003"] == "Inicio"
    m.EnvioCorreo.create(expediente=escenario["e1"].id, tipo="REENVIO", destinatario="x@y.cl", estado="ENVIADO")
    assert indicadores(generar("correos", proceso))["Envíos fallidos"] == 4


def test_otro_proceso_no_se_mezcla(escenario, admin, proceso):
    cfg.cerrar_proceso(proceso.id, admin)
    nuevo = armar_proceso(admin, codigo="MAT2028")
    assert r.leer_filtros({}).proceso.id == nuevo.id  # el activo, por defecto
    assert indicadores(generar("estado", nuevo))["Expedientes"] == 0
    assert indicadores(generar("estado", proceso))["Expedientes"] == 5


# ── Exportación ──

def test_exportar_csv_con_minutos_y_sin_formulas(escenario, admin, proceso):
    m.Postulante.update(nombres="=HYPERLINK(\"x\")").where(m.Postulante.id == escenario["e1"].postulante_id).execute()
    contenido, tipo, nombre = r.exportar("atencion", filtros(proceso), "csv", admin, ahora=minutos(60))
    texto = contenido.decode("utf-8-sig")
    assert tipo == "text/csv" and nombre.startswith("reporte_atencion_MAT2027_") and nombre.endswith(".csv")
    assert "Atención por fase" in texto and "Proceso: Matrícula 2027" in texto
    assert "Espera promedio (min)" in texto and "MNP;2;0;7.5;7.5;10.0;10.0;10.0;10.0" in texto
    contenido, _tipo, _nombre = r.exportar("estado", filtros(proceso), "csv", admin)
    assert "'=HYPERLINK" in contenido.decode("utf-8-sig")
    registro = auditorias("EXPORTAR_REPORTE")[0]
    assert registro.valor_nuevo["reporte"] == "atencion" and registro.valor_nuevo["formato"] == "csv"


def test_exportar_excel(escenario, admin, proceso):
    contenido, tipo, nombre = r.exportar("estado", filtros(proceso, ruta="DIRECTA"), "xlsx", admin)
    assert nombre.endswith(".xlsx") and "spreadsheetml" in tipo
    libro = openpyxl.load_workbook(io.BytesIO(contenido))
    assert libro.sheetnames == ["Resumen", "Pendientes", "Matriculados por tipo de cierre", "Expedientes"]
    resumen = [fila for fila in libro["Resumen"].iter_rows(values_only=True)]
    assert resumen[0][0] == "Estado de los postulantes" and ("Ruta: Directa",) in [f[:1] for f in resumen]
    expedientes = list(libro["Expedientes"].iter_rows(values_only=True))
    assert expedientes[0][0] == "Número" and expedientes[1][0] == "M-0001" and len(expedientes) == 2


def test_todos_los_reportes_se_generan_sin_datos(admin, proceso):
    for slug in r.REPORTES:
        resultado = r.generar(slug, filtros(proceso))
        assert resultado["tablas"] and resultado["reporte"].slug == slug

