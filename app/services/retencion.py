"""Conservación limitada al período lectivo (RF-26): totales agregados, eliminación de los
datos personales de los procesos vencidos y anonimización de su auditoría.

La eliminación es irreversible. La ejecuta solo el comando `flask purgar-datos` (cron diario);
con `simular`, hace todo dentro de una transacción que se revierte y solo informa qué haría.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from peewee import JOIN, fn

from app import models as m
from app.db import db
from app.services import auditoria, tiempo
from app.services import workflow as wf

# Claves con datos personales que se quitan de valor_anterior y valor_nuevo
CLAVES_PERSONALES = {"rut", "nombres", "apellidos", "telefono", "correo_personal", "destinatario", "correo"}
_CORREO = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_RUT = re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]\b")
ELIMINADO = "[eliminado]"

ENTIDADES_DE_POSTULANTES = ("expediente", "postulante")


class _Simulacion(Exception):
    """Revierte la transacción de una simulación."""


# ── Totales agregados ───────────────────────────────────────────────

def calcular_resumen(proceso_id, ahora=None):
    """Reemplaza los totales del proceso (sin datos personales): en total, por carrera de
    primera preferencia y por fase (de cierre o en que quedó detenido)."""
    ahora = ahora or m.ahora()
    e = m.Expediente
    carrera1 = (m.PostulanteCarrera.select(m.PostulanteCarrera.carrera)
                .where((m.PostulanteCarrera.postulante == e.postulante) & (m.PostulanteCarrera.preferencia == 1)))
    expedientes = list(e.select(e.estado, e.tipo_cierre, e.fase_cierre, e.fase_detenida, carrera1.alias("carrera_id"))
                       .where(e.proceso == proceso_id).namedtuples())
    totales = {}

    def sumar(clave, expediente):
        fila = totales.setdefault(clave, {"matriculados": 0, "matriculados_cierre_manual": 0, "proceso_pendiente": 0})
        if expediente.estado == wf.MATRICULADO:
            fila["matriculados"] += 1
            fila["matriculados_cierre_manual"] += expediente.tipo_cierre == wf.MANUAL
        elif expediente.estado == wf.PROCESO_PENDIENTE:
            fila["proceso_pendiente"] += 1

    for expediente in expedientes:
        sumar(("TOTAL", None, None), expediente)
        sumar(("CARRERA", expediente.carrera_id, None), expediente)
        fase = expediente.fase_cierre if expediente.estado == wf.MATRICULADO else expediente.fase_detenida
        if fase:
            sumar(("FASE", None, fase), expediente)
    totales.setdefault(("TOTAL", None, None), {"matriculados": 0, "matriculados_cierre_manual": 0,
                                               "proceso_pendiente": 0})
    with db.atomic():
        m.ProcesoResumen.delete().where(m.ProcesoResumen.proceso == proceso_id).execute()
        m.ProcesoResumen.insert_many([
            {"proceso": proceso_id, "dimension": dimension, "carrera": carrera, "fase": fase, "calculado_en": ahora,
             **valores}
            for (dimension, carrera, fase), valores in totales.items()
        ]).execute()


def resumen(proceso_id) -> dict:
    """Totales conservados del proceso, para mostrarlos tras la eliminación."""
    filas = list(m.ProcesoResumen.select(m.ProcesoResumen, m.Carrera.nombre, m.Fase.nombre, m.Fase.orden)
                 .join(m.Carrera, on=(m.ProcesoResumen.carrera == m.Carrera.id), join_type=JOIN.LEFT_OUTER)
                 .switch(m.ProcesoResumen)
                 .join(m.Fase, on=(m.ProcesoResumen.fase == m.Fase.id), join_type=JOIN.LEFT_OUTER)
                 .where(m.ProcesoResumen.proceso == proceso_id))
    total = next((f for f in filas if f.dimension == "TOTAL"), None)
    return {
        "total": total,
        "carreras": sorted((f for f in filas if f.dimension == "CARRERA"),
                           key=lambda f: (f.carrera_id is None, f.carrera.nombre if f.carrera_id else "")),
        "fases": sorted((f for f in filas if f.dimension == "FASE"), key=lambda f: f.fase.orden),
        "calculado": tiempo.fecha_hora_local(total.calculado_en) if total else None,
    }


# ── Anonimización ───────────────────────────────────────────────────

def anonimizar(valor):
    """Quita las claves con datos personales y borra correos y RUT que aparezcan en textos."""
    if isinstance(valor, dict):
        return {k: anonimizar(v) for k, v in valor.items() if k not in CLAVES_PERSONALES}
    if isinstance(valor, list):
        return [anonimizar(v) for v in valor]
    if isinstance(valor, str):
        return _RUT.sub(ELIMINADO, _CORREO.sub(ELIMINADO, valor))
    return valor


def _anonimizar_auditoria(condicion) -> int:
    cambiados = 0
    registros = m.Auditoria.select(m.Auditoria.id, m.Auditoria.valor_anterior, m.Auditoria.valor_nuevo).where(condicion)
    for registro in registros:
        anterior, nuevo = anonimizar(registro.valor_anterior), anonimizar(registro.valor_nuevo)
        if (anterior, nuevo) != (registro.valor_anterior, registro.valor_nuevo):
            (m.Auditoria.update(valor_anterior=anterior, valor_nuevo=nuevo)
             .where(m.Auditoria.id == registro.id).execute())
            cambiados += 1
    return cambiados


# ── Eliminación ─────────────────────────────────────────────────────

@dataclass
class Resultado:
    proceso: str = None
    expedientes: int = 0
    atenciones: int = 0
    correos: int = 0
    postulantes: int = 0
    auditoria_anonimizada: int = 0

    def como_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "proceso"}


@dataclass
class Informe:
    procesos: list = field(default_factory=list)  # [Resultado]
    sin_proceso: Resultado = None  # postulantes cargados que nunca iniciaron
    alertas: list = field(default_factory=list)  # procesos activos con la fecha vencida
    simulado: bool = False


def _borrar_postulantes(ids) -> tuple[int, int]:
    """Borra los postulantes con sus carreras y anonimiza su auditoría: (borrados, anonimizados)."""
    if not ids:
        return 0, 0
    anonimizados = _anonimizar_auditoria((m.Auditoria.entidad == "postulante") & m.Auditoria.entidad_id.in_(ids))
    m.PostulanteCarrera.delete().where(m.PostulanteCarrera.postulante.in_(ids)).execute()
    borrados = m.Postulante.delete().where(m.Postulante.id.in_(ids)).execute()
    return borrados, anonimizados


def _purgar_proceso(proceso, ahora) -> Resultado:
    resultado = Resultado(proceso=proceso.nombre)
    calcular_resumen(proceso.id, ahora)  # recalculado justo antes, en la misma transacción
    expedientes = [e.id for e in m.Expediente.select(m.Expediente.id).where(m.Expediente.proceso == proceso.id)]
    postulantes = {e.postulante_id for e in
                   m.Expediente.select(m.Expediente.postulante).where(m.Expediente.proceso == proceso.id)}
    if expedientes:
        resultado.atenciones = m.Atencion.select().where(m.Atencion.expediente.in_(expedientes)).count()
        resultado.correos = m.EnvioCorreo.select().where(m.EnvioCorreo.expediente.in_(expedientes)).count()
        resultado.auditoria_anonimizada += _anonimizar_auditoria(
            (m.Auditoria.entidad == "expediente") & m.Auditoria.entidad_id.in_(expedientes))
        # Atenciones y correos se borran en cascada
        resultado.expedientes = m.Expediente.delete().where(m.Expediente.id.in_(expedientes)).execute()
    # Se conservan quienes tienen un expediente en otro proceso (más reciente)
    con_otro = {e.postulante_id for e in m.Expediente.select(m.Expediente.postulante)
                .where(m.Expediente.postulante.in_(list(postulantes)))} if postulantes else set()
    resultado.postulantes, anonimizados = _borrar_postulantes(list(postulantes - con_otro))
    resultado.auditoria_anonimizada += anonimizados
    # Consultas de RUT del formulario QR que no encontraron a nadie: hechas durante el proceso
    resultado.auditoria_anonimizada += _anonimizar_auditoria(
        (m.Auditoria.entidad == "postulante") & m.Auditoria.entidad_id.is_null()
        & (fn.timezone(tiempo.ZONA.key, m.Auditoria.fecha_hora).cast("date") <= proceso.fecha_eliminacion))
    proceso.datos_eliminados_en = ahora
    proceso.save()
    auditoria.registrar("PURGAR_DATOS", "proceso", proceso.id, actor="SISTEMA", usuario=None,
                        nuevo=resultado.como_dict())
    return resultado


def _purgar_sin_proceso(hoy, ahora) -> Resultado:
    resultado = Resultado(proceso=None)
    con_expediente = m.Expediente.select(m.Expediente.postulante)
    ids = [p.id for p in m.Postulante.select(m.Postulante.id)
           .where(m.Postulante.eliminar_en.is_null(False) & (m.Postulante.eliminar_en <= hoy)
                  & m.Postulante.id.not_in(con_expediente))]
    resultado.postulantes, resultado.auditoria_anonimizada = _borrar_postulantes(ids)
    if ids:
        auditoria.registrar("PURGAR_DATOS", "postulante", None, actor="SISTEMA", usuario=None,
                            nuevo=resultado.como_dict())
    return resultado


def vencidos(hoy: date):
    """Procesos cerrados con la fecha de eliminación cumplida y datos aún sin eliminar."""
    return list(m.Proceso.select().where((m.Proceso.estado == "CERRADO") & (m.Proceso.fecha_eliminacion <= hoy)
                                         & m.Proceso.datos_eliminados_en.is_null()).order_by(m.Proceso.fecha_eliminacion))


def activos_vencidos(hoy: date):
    """Procesos que siguen activos con la fecha de eliminación cumplida: no se borran (RF-26)."""
    return list(m.Proceso.select().where((m.Proceso.estado == "ACTIVO") & (m.Proceso.fecha_eliminacion <= hoy)))


def purgar(hoy: date = None, simular=False, ahora=None) -> Informe:
    ahora = ahora or m.ahora()
    hoy = hoy or ahora.astimezone(tiempo.ZONA).date()
    informe = Informe(alertas=activos_vencidos(hoy), simulado=simular)
    try:
        with db.atomic():
            for proceso in vencidos(hoy):
                with db.atomic():  # cada proceso, todo o nada
                    informe.procesos.append(_purgar_proceso(proceso, ahora))
            informe.sin_proceso = _purgar_sin_proceso(hoy, ahora)
            if simular:
                raise _Simulacion
    except _Simulacion:
        pass
    return informe


# ── Avisos en el panel ──────────────────────────────────────────────

DIAS_AVISO = 30


def avisos(usuario, hoy: date = None) -> list:
    """Avisos del panel de inicio (Administrador y Gestor): eliminación en los próximos 30 días,
    y, solo para el Administrador, procesos activos con la fecha ya cumplida."""
    if usuario is None or usuario.rol not in ("ADMIN", "GESTOR"):
        return []
    hoy = hoy or m.ahora().astimezone(tiempo.ZONA).date()
    mensajes = []
    if usuario.rol == "ADMIN":
        for proceso in activos_vencidos(hoy):
            mensajes.append(f"«{proceso.nombre}» sigue activo y su fecha de eliminación de datos "
                            f"({proceso.fecha_eliminacion:%d-%m-%Y}) ya pasó: ciérrelo o ajuste la fecha.")
    proximos = (m.Proceso.select()
                .where((m.Proceso.estado != "BORRADOR") & m.Proceso.datos_eliminados_en.is_null()
                       & m.Proceso.fecha_eliminacion.between(hoy + timedelta(days=1), hoy + timedelta(days=DIAS_AVISO)))
                .order_by(m.Proceso.fecha_eliminacion))
    for proceso in proximos:
        dias = (proceso.fecha_eliminacion - hoy).days
        mensajes.append(f"Los datos personales de «{proceso.nombre}» se eliminarán el "
                        f"{proceso.fecha_eliminacion:%d-%m-%Y} (en {dias} día{'s' if dias != 1 else ''}). "
                        "Exporte los reportes que necesite conservar.")
    return mensajes
