"""Búsqueda por RUT y ficha del postulante (RF-15), reactivación tras "Abandonó" (RF-28) y
correcciones de la ruta y reapertura de atenciones (RF-27)."""

from peewee import JOIN

from app import models as m
from app.services import bandeja, recorridos, tiempo
from app.services import workflow as wf
from app.services.inicio import (
    buscar_postulante, consultas_numeradas, datos_de, nombres_de_carrera, normalizar_rut, numero_visible,
)
from app.services.postulantes import CARRERAS

ESTADO_ATENCION = {
    wf.PLANIFICADA: "Planificada", wf.EN_ESPERA: "En espera", wf.EN_ATENCION: "En atención",
    wf.COMPLETADO: "Completado", wf.COMPLETADO_CON_OBSERVACIONES: "Completado con observaciones",
    wf.DERIVADA: "Derivada", wf.ABANDONADA: "Abandonó",
}

# Movimientos del expediente que forman su historial (RF-15)
ACCIONES_HISTORIAL = {
    "FINALIZAR_ATENCION": "Finalizó y derivó",
    "DERIVAR_AHORA": "Derivó ahora",
    "DERIVAR_A_FLUJO": "Derivó a flujo alternativo",
    "DEVOLVER_A_PRINCIPAL": "Devolvió al flujo principal",
    "CIERRE_MANUAL": "Cerró la matrícula manualmente",
    "ABANDONO": "Marcó «Abandonó»",
    "REACTIVAR": "Reactivó",
    "CORREGIR_RUTA": "Corrigió la ruta",
    "REABRIR_ATENCION": "Reabrió una atención",
}


# ── Búsqueda y ficha ────────────────────────────────────────────────

def buscar(rut_ingresado):
    """Postulante con ese RUT, o None. Lanza 422 si el RUT es inválido."""
    return buscar_postulante(normalizar_rut(rut_ingresado))


def ver(postulante_id, usuario, ahora=None) -> dict:
    ahora = ahora or m.ahora()
    postulante = m.Postulante.get_or_none(m.Postulante.id == postulante_id)
    if postulante is None:
        raise wf.ReglaVioladaError("El postulante no existe", 404)
    datos = datos_de(postulante)
    expedientes = (m.Expediente.select().where(m.Expediente.postulante == postulante.id)
                   .order_by(m.Expediente.creado_en.desc()))
    return {
        "postulante": postulante,
        "carreras": nombres_de_carrera([datos[campo] for campo in CARRERAS]),
        "expedientes": [_vista_expediente(e, usuario, ahora) for e in expedientes],
    }


def _estado_global(rec) -> str:
    if rec.estado == wf.MATRICULADO:
        return "Matriculado con observaciones" if rec.con_observaciones else "Matriculado"
    if rec.estado == wf.PROCESO_PENDIENTE:
        return "Proceso pendiente (abandonó)" if rec.hubo_abandono else "Proceso pendiente"
    return {wf.EN_CURSO: "No matriculado aún", wf.ABANDONO: "Abandonó"}[rec.estado]


def _vista_expediente(expediente, usuario, ahora) -> dict:
    _, rec = recorridos.cargar(expediente.id, bloquear=False)
    proceso = m.Proceso.get_by_id(expediente.proceso_id)
    estructura = rec.estructura
    ids_usuario = {a.atendido_por for a in rec.ruta if a.atendido_por}
    nombres = {u.id: u.nombre for u in m.Usuario.select().where(m.Usuario.id.in_(ids_usuario))} if ids_usuario else {}

    actual = rec.actual
    atenciones = []
    for atencion in rec.ruta:
        espera = wf.tiempo_espera(atencion)
        duracion = wf.tiempo_atencion(atencion)
        atenciones.append({
            "fase": estructura.fase(atencion.fase_id).nombre,
            "estado": ESTADO_ATENCION[atencion.estado],
            "actual": atencion is actual,
            "en_espera_desde": tiempo.fecha_hora_corta(atencion.en_espera_desde),
            "inicio": tiempo.fecha_hora_corta(atencion.inicio_atencion),
            "fin": tiempo.fecha_hora_corta(atencion.fin_atencion),
            "espera": tiempo.duracion(espera.total_seconds()) if espera is not None else "",
            "duracion": tiempo.duracion(duracion.total_seconds()) if duracion is not None else "",
            "responsable": nombres.get(atencion.atendido_por),
            "observacion": atencion.observacion,
            "detalle_cierre": atencion.detalle_cierre,
            "sin_tiempo_atencion": atencion.sin_tiempo_atencion,
        })

    vista_actual = None
    if actual is not None:
        desde = actual.inicio_atencion if actual.estado == wf.EN_ATENCION else actual.en_espera_desde
        vista_actual = {
            "fase": estructura.fase(actual.fase_id).nombre,
            "estado": ESTADO_ATENCION[actual.estado],
            "transcurrido": tiempo.duracion((ahora - desde).total_seconds()) if desde else "",
        }

    activo = rec.proceso_estado == "ACTIVO"
    gestiona = usuario.rol in wf.ROLES_TODAS_LAS_FASES
    a_reabrir = _a_reabrir(rec) if activo and gestiona else None
    siguiente = rec.fase_siguiente
    return {
        "expediente": expediente,
        "proceso": proceso,
        "numero": numero_visible(proceso, expediente.numero_atencion),
        "estado": _estado_global(rec),
        "estado_codigo": rec.estado,
        "flujo": estructura.flujo(rec.flujo_alternativo_id).nombre if rec.flujo_alternativo_id else None,
        "actual": vista_actual,
        "version": actual.version if actual else None,
        "siguiente": siguiente.nombre if siguiente else None,
        "atenciones": atenciones,
        "historial": _historial(expediente, estructura),
        "puede_actuar": activo and actual is not None and _puede(usuario, actual.fase_id),
        "final_manual": (actual is not None and rec.en_flujo_alternativo
                         and actual.fase_id == estructura.flujo(rec.flujo_alternativo_id).fase_final_id),
        "puede_reactivar": activo and rec.estado == wf.ABANDONO and _puede_reactivar(usuario, rec),
        "puede_corregir": (activo and gestiona and actual is not None and not rec.en_flujo_alternativo
                           and rec.estado == wf.EN_CURSO),
        "orden": _orden_planificado(rec),
        "consultas": [f for f in estructura.fases if f.tipo == wf.CONSULTA],
        "es_mnp": actual is not None and estructura.fase(actual.fase_id).tipo == wf.CIERRE_DIGITAL,
        "puede_reabrir": a_reabrir is not None,
        "a_reabrir": ({"id": a_reabrir.id, "fase": estructura.fase(a_reabrir.fase_id).nombre}
                      if a_reabrir is not None else None),
    }


def _orden_planificado(rec) -> dict:
    orden = {}
    for atencion in rec.ruta:
        if (atencion.estado == wf.PLANIFICADA and rec.estructura.fase(atencion.fase_id).tipo == wf.CONSULTA
                and atencion.fase_id not in orden):
            orden[atencion.fase_id] = len(orden) + 1
    return orden


def _historial(expediente, estructura) -> list:
    registros = (
        m.Auditoria.select(m.Auditoria, m.Usuario)
        .join(m.Usuario, join_type=JOIN.LEFT_OUTER, on=(m.Auditoria.usuario == m.Usuario.id))
        .where((m.Auditoria.entidad == "expediente") & (m.Auditoria.entidad_id == expediente.id)
               & m.Auditoria.accion.in_(["INICIAR_PROCESO", *ACCIONES_HISTORIAL]))
        .order_by(m.Auditoria.fecha_hora, m.Auditoria.id)
    )
    historial = []
    for registro in registros:
        nuevo = registro.valor_nuevo or {}
        if registro.accion == "INICIAR_PROCESO":
            via = nuevo.get("via")
            accion = f"Inicio de proceso ({'QR' if via == 'QR' else 'staff'})"
            desde = estructura.inicial.nombre if via != "QR" else None
            ruta = nuevo.get("ruta") or nuevo.get("ruta_propuesta") or []
            hacia = next((p["fase"] for p in ruta if p["estado"] in wf.ESTADOS_ACTIVOS), None)
        else:
            accion = ACCIONES_HISTORIAL[registro.accion]
            desde = nuevo.get("origen") or (registro.valor_anterior or {}).get("fase_actual")
            hacia = nuevo.get("fase_actual")
        detalle = nuevo.get("motivo") or nuevo.get("detalle_cierre") or nuevo.get("observacion")
        if nuevo.get("flujo") and registro.accion == "DERIVAR_A_FLUJO":
            detalle = nuevo["flujo"]
        historial.append({
            "fecha": tiempo.fecha_hora_local(registro.fecha_hora),
            "accion": accion,
            "desde": desde,
            "hacia": hacia,
            "usuario": registro.usuario.nombre if registro.usuario_id else "Postulante (QR)",
            "detalle": detalle,
        })
    return historial


# ── Permisos ────────────────────────────────────────────────────────

def _asignadas(usuario) -> set:
    return {f.fase_id for f in m.FaseResponsable.select().where(m.FaseResponsable.usuario == usuario.id)}


def _puede(usuario, fase_id) -> bool:
    asignadas = _asignadas(usuario) if usuario.rol == "USUARIO_FASE" else set()
    return wf.puede_actuar(usuario.rol, asignadas, fase_id)


def _puede_reactivar(usuario, rec) -> bool:
    """RF-28: responsable de la fase en que abandonó o de "Iniciado", el Gestor o el Administrador."""
    if usuario.rol in wf.ROLES_TODAS_LAS_FASES:
        return True
    if usuario.rol != "USUARIO_FASE":
        return False
    ultima = rec._ultima_cerrada()
    candidatas = {rec.estructura.inicial.id, ultima.fase_id if ultima else None}
    return bool(_asignadas(usuario) & candidatas)


def _exigir_gestion(usuario):
    if usuario.rol not in wf.ROLES_TODAS_LAS_FASES:
        raise wf.ReglaVioladaError("Solo el Gestor y el Administrador corrigen la ruta o reabren atenciones", 403)


def _a_reabrir(rec):
    """La atención que reabriría `Recorrido.reabrir`, o None si no se puede."""
    if rec.estado not in (wf.EN_CURSO, wf.MATRICULADO):
        return None
    actual = rec.actual
    if actual is not None and actual.estado != wf.EN_ESPERA:
        return None
    posicion = rec.ruta.index(actual) if actual else rec._posicion_activa()
    anterior = rec.ruta[posicion - 1] if posicion > 0 else None
    if anterior is None or anterior.estado not in wf.ESTADOS_COMPLETOS:
        return None
    if rec.estructura.fase(anterior.fase_id).tipo == wf.INICIAL:
        return None
    return anterior


# ── Acciones ────────────────────────────────────────────────────────

def opciones_reactivar(expediente_id, usuario) -> dict:
    _, rec = recorridos.cargar(expediente_id, bloquear=False)
    if rec.estado != wf.ABANDONO or rec.proceso_estado != "ACTIVO":
        raise wf.ReglaVioladaError("Solo se reactiva un expediente en «Abandonó»")
    if not _puede_reactivar(usuario, rec):
        raise wf.ReglaVioladaError("No puede reactivar este expediente", 403)
    estructura = rec.estructura
    if rec.en_flujo_alternativo:
        secuencia = estructura.flujo(rec.flujo_alternativo_id).fases
        fases = [estructura.fase(fase_id) for fase_id in secuencia]
    else:
        fases = [f for f in estructura.fases if f.tipo != wf.INICIAL]
    return {"fases": fases, "fase_id": rec._ultima_cerrada().fase_id}


def reactivar(expediente_id, form, usuario, ahora=None) -> str:
    """Nueva atención EN_ESPERA en la fase en que abandonó, o en `fase` si se indica."""
    ahora = ahora or m.ahora()
    fase = (form.get("fase") or "").strip()

    def cambio(rec):
        rec._exigir_proceso_activo()
        if rec.estado == wf.ABANDONO and not _puede_reactivar(usuario, rec):
            raise wf.ReglaVioladaError("No puede reactivar este expediente", 403)
        rec.reactivar(usuario.id, ahora, int(fase) if fase.isdigit() else None)

    return bandeja.operar_auditado(expediente_id, usuario, "REACTIVAR", cambio)


def corregir_ruta(expediente_id, form, usuario, ahora=None) -> str:
    ahora = ahora or m.ahora()
    _exigir_gestion(usuario)
    motivo = (form.get("motivo") or "").strip()

    def cambio(rec):
        rec.corregir_ruta(consultas_numeradas(form, rec.estructura), motivo, usuario.id, ahora)
        return {"motivo": motivo}

    return bandeja.operar_auditado(expediente_id, usuario, "CORREGIR_RUTA", cambio,
                                   bandeja.leer_version(form.get("version")))


def reabrir(expediente_id, form, usuario, ahora=None) -> str:
    """Reabre la última atención completada. `atencion` es la que vio el usuario: si ya no es
    la que se reabriría (por ejemplo, por un doble clic), responde 409."""
    ahora = ahora or m.ahora()
    _exigir_gestion(usuario)
    motivo = (form.get("motivo") or "").strip()
    vista = (form.get("atencion") or "").strip()

    def cambio(rec):
        objetivo = _a_reabrir(rec)
        if objetivo is None or str(objetivo.id) != vista:
            raise wf.ReglaVioladaError("El expediente cambió desde que lo abrió. Recargue la página.")
        reabierta = rec.estructura.fase(objetivo.fase_id).nombre
        rec.reabrir(motivo, usuario.id, ahora)
        return {"motivo": motivo, "reabierta": reabierta}

    return bandeja.operar_auditado(expediente_id, usuario, "REABRIR_ATENCION", cambio)
