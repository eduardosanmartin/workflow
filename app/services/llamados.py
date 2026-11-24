"""Llamados a módulo y pantalla del hall de espera.

El responsable de una fase llama a un postulante en espera a su módulo. La pantalla del
hall muestra el llamado en grande y lo anuncia con un sonido y una voz; si el postulante no
se presenta, el aviso se repite cada `INTERVALO` segundos, hasta `MAX_AVISOS` veces.

Las repeticiones no se guardan: se calculan desde `llamado_en`, así todas las pantallas
anuncian lo mismo sin depender de un proceso en segundo plano. El llamado se cierra cuando
el responsable atiende al postulante, cancela el llamado o hace cualquier otra acción sobre
el expediente (ver `bandeja.operar_auditado`).
"""

from datetime import timedelta

from peewee import JOIN

from app import models as m
from app.db import db
from app.services import auditoria, recorridos
from app.services import workflow as wf
from app.services.inicio import numero_visible

INTERVALO = 30  # segundos entre avisos si el postulante no se presenta
MAX_AVISOS = 3  # el primero y dos repeticiones
LARGO_MODULO = 20
EN_PANTALLA_ATENDIENDO = 12  # cuántos "en atención" caben en la pantalla


# ── Módulo del usuario ──────────────────────────────────────────────

def guardar_modulo(usuario, valor) -> str | None:
    """El usuario indica en qué módulo atiende hoy. Vacío lo borra."""
    modulo = " ".join((valor or "").split())
    if modulo.lower().startswith("módulo") or modulo.lower().startswith("modulo"):
        modulo = modulo[6:].strip()  # "Módulo 4" → "4": la pantalla ya antepone la palabra
    if len(modulo) > LARGO_MODULO:
        raise wf.ReglaVioladaError(f"El módulo admite hasta {LARGO_MODULO} caracteres", 422)
    usuario.modulo = modulo or None
    m.Usuario.update(modulo=usuario.modulo).where(m.Usuario.id == usuario.id).execute()
    return usuario.modulo


# ── Estado de un llamado ────────────────────────────────────────────

def aviso(llamado, ahora) -> int:
    """Número de aviso vigente (1 = el llamado inicial)."""
    transcurrido = max(0.0, (ahora - llamado.llamado_en).total_seconds())
    return min(MAX_AVISOS, 1 + int(transcurrido // INTERVALO))


def sin_respuesta(llamado, ahora) -> bool:
    """Pasó el último aviso y su intervalo sin que el postulante se presentara."""
    return (ahora - llamado.llamado_en).total_seconds() >= MAX_AVISOS * INTERVALO


def abiertos(atencion_ids) -> dict:
    """{atencion_id: llamado abierto}."""
    if not atencion_ids:
        return {}
    return {
        ll.atencion_id: ll for ll in m.Llamado.select(m.Llamado, m.Usuario)
        .join(m.Usuario, on=(m.Llamado.usuario == m.Usuario.id), join_type=JOIN.LEFT_OUTER)
        .where(m.Llamado.atencion.in_(list(atencion_ids)) & m.Llamado.cerrado_en.is_null())
    }


def cerrar_del_expediente(expediente_id, ahora):
    """Cierra los llamados abiertos del expediente. Llamar dentro de la transacción de la acción."""
    de_expediente = m.Atencion.select(m.Atencion.id).where(m.Atencion.expediente == expediente_id)
    return (m.Llamado.update(cerrado_en=ahora)
            .where(m.Llamado.atencion.in_(de_expediente) & m.Llamado.cerrado_en.is_null())
            .execute())


# ── Acciones del responsable ────────────────────────────────────────

def _atencion_en_espera(expediente_id, usuario):
    """Bloquea el expediente y devuelve (expediente, fila de la atención en espera)."""
    from app.services.bandeja import exigir_permiso  # bandeja importa este módulo

    expediente, rec = recorridos.cargar(expediente_id)
    if rec.estado != wf.EN_CURSO:
        raise wf.ReglaVioladaError("El postulante ya no está en la fila")
    actual = rec.actual
    if actual is None:
        raise wf.ReglaVioladaError("El postulante no tiene una atención en curso")
    exigir_permiso(usuario, actual.fase_id)
    if actual.estado != wf.EN_ESPERA:
        raise wf.ReglaVioladaError("Solo se puede llamar a un postulante en espera")
    return expediente, m.Atencion.get_by_id(actual.id)


def llamar(expediente_id, usuario, ahora=None) -> str:
    """Llama al postulante en espera al módulo del usuario. Si ya estaba llamado por él (o
    su llamado anterior quedó sin respuesta), lo vuelve a llamar desde el primer aviso."""
    ahora = ahora or m.ahora()
    if not usuario.modulo:
        raise wf.ReglaVioladaError("Indique su módulo antes de llamar a un postulante", 422)
    with db.atomic():
        expediente, atencion = _atencion_en_espera(expediente_id, usuario)
        previo = abiertos([atencion.id]).get(atencion.id)
        numero = numero_visible(m.Proceso.get_by_id(expediente.proceso_id), expediente.numero_atencion)
        if previo is not None and previo.usuario_id != usuario.id and not sin_respuesta(previo, ahora):
            quien = previo.usuario.nombre if previo.usuario_id else "otro responsable"
            raise wf.ReglaVioladaError(f"{numero} ya fue llamado al módulo {previo.modulo} por {quien}")
        if previo is not None:
            m.Llamado.update(usuario=usuario.id, modulo=usuario.modulo, llamado_en=ahora).where(
                m.Llamado.id == previo.id).execute()
            accion = "VOLVER_A_LLAMAR"
        else:
            m.Llamado.create(atencion=atencion.id, usuario=usuario.id, modulo=usuario.modulo, llamado_en=ahora)
            accion = "LLAMAR"
        fase = m.Fase.get_by_id(atencion.fase_id).nombre
        auditoria.registrar(accion, "expediente", expediente.id, None,
                            {"fase": fase, "modulo": usuario.modulo}, usuario)
    return f"Llamando a {numero} al módulo {usuario.modulo}"


def cancelar(expediente_id, usuario, ahora=None) -> str:
    """Quita el llamado de la pantalla; el postulante sigue en espera en su lugar de la fila."""
    ahora = ahora or m.ahora()
    with db.atomic():
        expediente, atencion = _atencion_en_espera(expediente_id, usuario)
        previo = abiertos([atencion.id]).get(atencion.id)
        if previo is None:
            raise wf.ReglaVioladaError("El postulante no tiene un llamado pendiente")
        m.Llamado.update(cerrado_en=ahora).where(m.Llamado.id == previo.id).execute()
        auditoria.registrar("CANCELAR_LLAMADO", "expediente", expediente.id,
                            {"modulo": previo.modulo}, None, usuario)
    numero = numero_visible(m.Proceso.get_by_id(expediente.proceso_id), expediente.numero_atencion)
    return f"Se canceló el llamado de {numero}; sigue en espera"


# ── Pantalla del hall ───────────────────────────────────────────────

def _voz(proceso, numero, fase, modulo) -> str:
    """Texto que lee la pantalla. "M 16" se pronuncia mejor que "M-0016"."""
    return f"Número {proceso.prefijo_atencion} {numero}. Diríjase a {fase}, módulo {modulo}."


def pantalla(proceso, ahora=None) -> dict:
    """Llamados vigentes (el más reciente primero) y postulantes en atención, sin datos personales."""
    ahora = ahora or m.ahora()
    if proceso is None:
        return {"proceso": None, "llamando": [], "atendiendo": [], "intervalo": INTERVALO}
    fases = {f.id: f.nombre for f in m.Fase.select().where(m.Fase.proceso == proceso.id)}

    llamando = []
    vigentes = (m.Llamado.select(m.Llamado, m.Atencion, m.Expediente)
                .join(m.Atencion).join(m.Expediente)
                .where(m.Llamado.cerrado_en.is_null() & (m.Atencion.estado == wf.EN_ESPERA)
                       & (m.Expediente.proceso == proceso.id) & (m.Expediente.estado == wf.EN_CURSO)
                       & (m.Llamado.llamado_en > ahora - timedelta(seconds=MAX_AVISOS * INTERVALO)))
                .order_by(m.Llamado.llamado_en.desc(), m.Llamado.id.desc()))
    for ll in vigentes:
        expediente = ll.atencion.expediente
        n = aviso(ll, ahora)
        aviso_en = ll.llamado_en + timedelta(seconds=(n - 1) * INTERVALO)
        fase = fases[ll.atencion.fase_id]
        llamando.append({
            "numero": numero_visible(proceso, expediente.numero_atencion),
            "fase": fase,
            "modulo": ll.modulo,
            "aviso": n,
            "avisos": MAX_AVISOS,
            # Identifica cada aviso: la pantalla anuncia solo los que no ha anunciado
            "clave": f"{ll.id}-{int(ll.llamado_en.timestamp())}-{n}",
            "hace": max(0, int((ahora - aviso_en).total_seconds())),
            "voz": _voz(proceso, expediente.numero_atencion, fase, ll.modulo),
        })

    en_atencion = list(
        m.Atencion.select(m.Atencion, m.Expediente, m.Usuario)
        .join(m.Expediente)
        .switch(m.Atencion).join(m.Usuario, on=(m.Atencion.atendido_por == m.Usuario.id), join_type=JOIN.LEFT_OUTER)
        .where((m.Atencion.estado == wf.EN_ATENCION) & (m.Expediente.proceso == proceso.id))
        .order_by(m.Atencion.inicio_atencion.desc(), m.Atencion.id.desc())
        .limit(EN_PANTALLA_ATENDIENDO)
    )
    # El módulo de un postulante en atención es el del último llamado; si lo atendieron sin
    # llamarlo, el módulo que tiene hoy quien lo atiende
    ultimo_modulo = {}
    if en_atencion:
        for ll in (m.Llamado.select().where(m.Llamado.atencion.in_([a.id for a in en_atencion]))
                   .order_by(m.Llamado.llamado_en)):
            ultimo_modulo[ll.atencion_id] = ll.modulo
    atendiendo = []
    for atencion in en_atencion:
        atiende = atencion.atendido_por if atencion.atendido_por_id else None
        atendiendo.append({
            "numero": numero_visible(proceso, atencion.expediente.numero_atencion),
            "fase": fases[atencion.fase_id],
            "modulo": ultimo_modulo.get(atencion.id) or (atiende.modulo if atiende else None),
        })
    return {"proceso": proceso.nombre, "llamando": llamando, "atendiendo": atendiendo, "intervalo": INTERVALO}
