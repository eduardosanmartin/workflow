"""Persistencia del recorrido: carga un expediente como `workflow.Recorrido`, ejecuta una
operación de dominio y guarda el resultado, todo en una transacción que bloquea la fila
del expediente (`SELECT … FOR UPDATE`) y verifica la versión de la atención (RNF-06).
"""

from app import models as m
from app.db import db
from app.services import workflow as wf

_CAMPOS = (
    "fase_id", "estado", "flujo_alternativo_id", "observacion", "detalle_cierre",
    "planificada_por", "en_espera_desde", "inicio_atencion", "fin_atencion", "atendido_por",
    "sin_tiempo_atencion",
)


def estructura_del_proceso(proceso_id) -> wf.Estructura:
    fases = [
        wf.Fase(f.id, f.nombre, f.tipo, f.mostrar_en_qr)
        for f in m.Fase.select().where(m.Fase.proceso == proceso_id).order_by(m.Fase.orden)
    ]
    secuencias = {}
    filas = (
        m.FlujoAlternativoFase.select()
        .join(m.FlujoAlternativo)
        .where(m.FlujoAlternativo.proceso == proceso_id)
        .order_by(m.FlujoAlternativoFase.posicion)
    )
    for fila in filas:
        secuencias.setdefault(fila.flujo_alternativo_id, []).append(fila.fase_id)
    flujos = [
        wf.FlujoAlternativo(f.id, f.nombre, tuple(secuencias.get(f.id, ())), f.activo)
        for f in m.FlujoAlternativo.select().where(m.FlujoAlternativo.proceso == proceso_id)
    ]
    return wf.Estructura(fases, flujos)


def _datos(atencion: wf.Atencion):
    datos = {campo: getattr(atencion, campo) for campo in _CAMPOS}
    datos["derivada_desde"] = atencion.derivada_desde.id if atencion.derivada_desde else None
    return datos


def cargar(expediente_id, bloquear=True):
    """Devuelve (expediente, recorrido). Con `bloquear`, debe llamarse dentro de una transacción."""
    consulta = m.Expediente.select().where(m.Expediente.id == expediente_id)
    if bloquear:
        consulta = consulta.for_update()
    expediente = consulta.first()
    if expediente is None:
        raise wf.ReglaVioladaError("El expediente no existe", 404)
    proceso_estado = m.Proceso.get_by_id(expediente.proceso_id).estado

    filas = list(
        m.Atencion.select().where(m.Atencion.expediente == expediente.id).order_by(m.Atencion.secuencia)
    )
    por_id, ruta = {}, []
    for fila in filas:
        atencion = wf.Atencion(
            fase_id=fila.fase_id,
            estado=fila.estado,
            flujo_alternativo_id=fila.flujo_alternativo_id,
            id=fila.id,
            planificada_por=fila.planificada_por_id,
            en_espera_desde=fila.en_espera_desde,
            inicio_atencion=fila.inicio_atencion,
            fin_atencion=fila.fin_atencion,
            atendido_por=fila.atendido_por_id,
            observacion=fila.observacion,
            detalle_cierre=fila.detalle_cierre,
            sin_tiempo_atencion=fila.sin_tiempo_atencion,
            version=fila.version,
        )
        por_id[fila.id] = atencion
        ruta.append(atencion)
    for fila, atencion in zip(filas, ruta):
        atencion.derivada_desde = por_id.get(fila.derivada_desde_id)

    recorrido = wf.Recorrido(
        estructura_del_proceso(expediente.proceso_id),
        ruta,
        estado=expediente.estado,
        flujo_alternativo_id=expediente.flujo_alternativo_id,
        tipo_cierre=expediente.tipo_cierre,
        fase_cierre_id=expediente.fase_cierre_id,
        matriculado_en=expediente.matriculado_en,
        fase_detenida_id=expediente.fase_detenida_id,
        proceso_estado=proceso_estado,
    )
    # Huella de cada fila al cargar, para actualizar solo lo que cambió
    recorrido.huellas = {a.id: (_datos(a), i) for i, a in enumerate(ruta, 1)}
    return expediente, recorrido


def guardar(expediente, recorrido: wf.Recorrido):
    """Persiste la ruta y el estado global. Lanza 409 si una atención cambió de versión."""
    huellas = getattr(recorrido, "huellas", {})
    with db.atomic():
        db.execute_sql("SET CONSTRAINTS atencion_secuencia_unica DEFERRED")
        quitadas = [a.id for a in recorrido.quitadas if a.id is not None]
        if quitadas:
            m.Atencion.delete().where(m.Atencion.id.in_(quitadas)).execute()

        # Primero las que no quedan activas: el índice "una atención activa por expediente" no
        # es diferible, así que la que deja de estar activa debe guardarse antes (ej.: reabrir)
        pendientes = sorted(enumerate(recorrido.ruta, 1), key=lambda par: par[1].estado in wf.ESTADOS_ACTIVOS)
        for secuencia, atencion in pendientes:
            datos = _datos(atencion)
            if atencion.id is None:
                atencion.id = m.Atencion.insert(
                    expediente=expediente.id, secuencia=secuencia, version=1,
                    fase=datos.pop("fase_id"), flujo_alternativo=datos.pop("flujo_alternativo_id"),
                    **datos,
                ).execute()
                atencion.version = 1
                continue
            if huellas.get(atencion.id) == (datos, secuencia):
                continue
            actualizadas = (
                m.Atencion.update(
                    secuencia=secuencia, version=atencion.version + 1,
                    fase=datos.pop("fase_id"), flujo_alternativo=datos.pop("flujo_alternativo_id"),
                    **datos,
                )
                .where((m.Atencion.id == atencion.id) & (m.Atencion.version == atencion.version))
                .execute()
            )
            if actualizadas != 1:
                raise wf.ReglaVioladaError("Otro usuario modificó esta atención. Recargue la página.")
            atencion.version += 1

        # Fuerza la verificación de la secuencia antes de seguir (atrapa errores aquí, no al confirmar)
        db.execute_sql("SET CONSTRAINTS atencion_secuencia_unica IMMEDIATE")

        expediente.estado = recorrido.estado
        expediente.con_observaciones = recorrido.con_observaciones
        expediente.matriculado_en = recorrido.matriculado_en
        expediente.flujo_alternativo = recorrido.flujo_alternativo_id
        expediente.tipo_cierre = recorrido.tipo_cierre
        expediente.fase_cierre = recorrido.fase_cierre_id
        expediente.fase_detenida = recorrido.fase_detenida_id
        expediente.save()

    recorrido.quitadas.clear()
    recorrido.huellas = {a.id: (_datos(a), i) for i, a in enumerate(recorrido.ruta, 1)}


def ejecutar(expediente_id, operacion, version=None):
    """Carga con bloqueo, aplica `operacion(recorrido)` y guarda, en una sola transacción.

    `version` es la de la atención actual que vio el usuario; si no coincide, responde 409.
    """
    with db.atomic():
        expediente, recorrido = cargar(expediente_id)
        if version is not None:
            actual = recorrido.actual
            if actual is None or actual.version != version:
                raise wf.ReglaVioladaError("El expediente cambió desde que lo abrió. Recargue la página.")
        resultado = operacion(recorrido)
        guardar(expediente, recorrido)
    return expediente, recorrido, resultado


def siguiente_numero_atencion(proceso_id) -> int:
    """Correlativo por proceso, bloqueando el contador (RF-19). Llamar dentro de una transacción."""
    return (
        m.Proceso.update(ultimo_numero_atencion=m.Proceso.ultimo_numero_atencion + 1)
        .where(m.Proceso.id == proceso_id)
        .returning(m.Proceso.ultimo_numero_atencion)
        .execute()[0]
        .ultimo_numero_atencion
    )

