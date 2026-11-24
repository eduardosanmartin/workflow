"""Bandeja de trabajo por fase (RF-17) y acciones sobre la atención actual del expediente:
atender, finalizar y derivar (RF-13, RF-14), abandono (RF-28) y flujos alternativos (RF-29).

Cada acción bloquea el expediente y verifica la versión de la atención que vio el usuario
(RNF-06), exige que el usuario pueda actuar sobre la fase actual (§4) y queda auditada en
la misma transacción.
"""

from peewee import fn

from app import models as m
from app.db import db
from app.services import auditoria, correo, llamados, recorridos, tiempo
from app.services import workflow as wf
from app.services.inicio import consultas_numeradas, numero_visible, opciones_de_flujo

RESULTADOS = ("completado", "observaciones")
ACCIONES = ("seguir", "derivar", "flujo", "devolver")


# ── Fases y permisos ────────────────────────────────────────────────

def fases_del_usuario(usuario, proceso) -> list:
    """Fases de la bandeja: todas para el Administrador y el Gestor; las asignadas para el
    Usuario de fase (§4)."""
    if usuario is None or proceso is None:
        return []
    consulta = m.Fase.select().where(m.Fase.proceso == proceso.id).order_by(m.Fase.orden)
    if usuario.rol in wf.ROLES_TODAS_LAS_FASES:
        return list(consulta)
    if usuario.rol != "USUARIO_FASE":
        return []
    return list(consulta.join(m.FaseResponsable).where(m.FaseResponsable.usuario == usuario.id))


def puede_ver(usuario, proceso) -> bool:
    return bool(fases_del_usuario(usuario, proceso))


def exigir_permiso(usuario, fase_id):
    asignadas = set()
    if usuario.rol == "USUARIO_FASE":
        asignadas = {fila.fase_id for fila in m.FaseResponsable.select().where(m.FaseResponsable.usuario == usuario.id)}
    if not wf.puede_actuar(usuario.rol, asignadas, fase_id):
        raise wf.ReglaVioladaError("Solo los responsables de la fase actual del postulante pueden actuar sobre ella", 403)


# ── Listas de la bandeja (RF-17) ────────────────────────────────────

def _expedientes(proceso, fecha, carrera):
    consulta = m.Expediente.select(m.Expediente.id).where(
        (m.Expediente.proceso == proceso.id) & (m.Expediente.estado == wf.EN_CURSO)
    )
    if fecha is not None:
        consulta = consulta.where(fn.timezone(tiempo.ZONA.key, m.Expediente.creado_en).cast("date") == fecha)
    if carrera:
        con_carrera = (m.PostulanteCarrera.select(m.PostulanteCarrera.postulante)
                       .join(m.Carrera).where(m.Carrera.codigo == carrera))
        consulta = consulta.where(m.Expediente.postulante.in_(con_carrera))
    return consulta


def listar(usuario, proceso, fase_id=None, fecha=None, carrera=None, ahora=None) -> dict:
    """En espera (por orden de llegada), en atención y próximos, en las fases del usuario.

    `fase_id` limita a una de sus fases; `fecha` (día de inicio, hora de Chile) y `carrera`
    (código, en cualquiera de las tres preferencias) filtran los expedientes.
    """
    ahora = ahora or m.ahora()
    listas = {"en_espera": [], "en_atencion": [], "proximos": []}
    propias = {f.id for f in fases_del_usuario(usuario, proceso)}
    ids = [fase_id] if fase_id in propias else ([] if fase_id is not None else list(propias))
    if not ids:
        return listas

    expedientes = _expedientes(proceso, fecha, carrera)
    activas = list(
        m.Atencion.select()
        .where(m.Atencion.fase.in_(ids) & m.Atencion.estado.in_(wf.ESTADOS_ACTIVOS)
               & m.Atencion.expediente.in_(expedientes))
        .order_by(m.Atencion.en_espera_desde, m.Atencion.id)
    )
    otra = m.Atencion.alias()
    primera_planificada = otra.select(fn.MIN(otra.secuencia)).where(
        (otra.expediente == m.Atencion.expediente) & (otra.estado == wf.PLANIFICADA)
    )
    proximas = list(
        m.Atencion.select()
        .where(m.Atencion.fase.in_(ids) & (m.Atencion.estado == wf.PLANIFICADA)
               & (m.Atencion.secuencia == primera_planificada) & m.Atencion.expediente.in_(expedientes))
    )
    involucrados = {a.expediente_id for a in activas + proximas}
    if not involucrados:
        return listas

    contexto = _Contexto(proceso, involucrados, ahora)
    contexto.llamados = llamados.abiertos([a.id for a in activas if a.estado == wf.EN_ESPERA])
    for atencion in activas:
        clave = "en_espera" if atencion.estado == wf.EN_ESPERA else "en_atencion"
        listas[clave].append(contexto.fila(atencion))
    listas["proximos"] = sorted((contexto.fila(atencion) for atencion in proximas), key=contexto.llegada)
    return listas


class _Contexto:
    """Datos de los expedientes listados, cargados con pocas consultas."""

    def __init__(self, proceso, expediente_ids, ahora):
        self.proceso = proceso
        self.ahora = ahora
        self.estructura = recorridos.estructura_del_proceso(proceso.id)
        self.expedientes = {
            e.id: e for e in m.Expediente.select(m.Expediente, m.Postulante)
            .join(m.Postulante).where(m.Expediente.id.in_(expediente_ids))
        }
        self.rutas = {}
        for atencion in (m.Atencion.select().where(m.Atencion.expediente.in_(expediente_ids))
                         .order_by(m.Atencion.secuencia)):
            self.rutas.setdefault(atencion.expediente_id, []).append(atencion)
        ids_usuario = {a.atendido_por_id for ruta in self.rutas.values() for a in ruta if a.atendido_por_id}
        self.usuarios = {u.id: u.nombre for u in m.Usuario.select().where(m.Usuario.id.in_(ids_usuario))} if ids_usuario else {}
        self.llamados = {}

    def _nombre_fase(self, fase_id):
        return self.estructura.fase(fase_id).nombre

    def _actual(self, expediente_id):
        return next((a for a in self.rutas[expediente_id] if a.estado in wf.ESTADOS_ACTIVOS), None)

    def llegada(self, fila):
        actual = self._actual(fila["expediente_id"])
        return (actual.en_espera_desde if actual else self.ahora), fila["expediente_id"]

    def fila(self, atencion) -> dict:
        expediente = self.expedientes[atencion.expediente_id]
        postulante = expediente.postulante
        ruta = self.rutas[expediente.id]
        previas = [a for a in ruta if a.secuencia < atencion.secuencia and a.estado != wf.PLANIFICADA]
        planificadas = [self._nombre_fase(a.fase_id) for a in ruta if a.estado == wf.PLANIFICADA]
        flujo = self.estructura.flujo(expediente.flujo_alternativo_id) if expediente.flujo_alternativo_id else None
        actual = self._actual(expediente.id)
        es_inicial = self.estructura.fase(atencion.fase_id).tipo == wf.INICIAL
        espera = ""
        if atencion.en_espera_desde is not None:
            espera = tiempo.duracion((self.ahora - atencion.en_espera_desde).total_seconds())
        return {
            "expediente_id": expediente.id,
            "numero": numero_visible(self.proceso, expediente.numero_atencion),
            "nombre": f"{postulante.nombres} {postulante.apellidos}",
            "fase": self._nombre_fase(atencion.fase_id),
            "estado": atencion.estado,
            "version": atencion.version,
            "viene_de": self._nombre_fase(previas[-1].fase_id) if previas else None,
            "espera": espera,
            "desde": tiempo.hora_local(atencion.inicio_atencion),
            "atendido_por": self.usuarios.get(atencion.atendido_por_id),
            "actual": self._nombre_fase(actual.fase_id) if actual else None,
            "flujo": flujo.nombre if flujo else None,
            "final_manual": flujo is not None and atencion.fase_id == flujo.fase_final_id,
            "via_qr": expediente.via_inicio == "QR",
            "ruta_propuesta": planificadas if es_inicial else [],
            "llamado": self._llamado(atencion),
        }

    def _llamado(self, atencion):
        llamado = self.llamados.get(atencion.id)
        if llamado is None:
            return None
        return {
            "modulo": llamado.modulo,
            "por": llamado.usuario.nombre if llamado.usuario_id else None,
            "usuario_id": llamado.usuario_id,
            "aviso": llamados.aviso(llamado, self.ahora),
            "avisos": llamados.MAX_AVISOS,
            "sin_respuesta": llamados.sin_respuesta(llamado, self.ahora),
            "desde": tiempo.hora_local(llamado.llamado_en),
        }


# ── Pantalla "Finalizar y derivar" (§10, 7b) ────────────────────────

def detalle(expediente_id, usuario) -> dict:
    expediente, rec = recorridos.cargar(expediente_id, bloquear=False)
    actual = rec._exigir_actual()
    exigir_permiso(usuario, actual.fase_id)
    estructura = rec.estructura
    fase = estructura.fase(actual.fase_id)
    flujo = estructura.flujo(rec.flujo_alternativo_id) if rec.en_flujo_alternativo else None
    orden = {}
    for atencion in rec.ruta:
        if (atencion.estado == wf.PLANIFICADA and estructura.fase(atencion.fase_id).tipo == wf.CONSULTA
                and atencion.fase_id not in orden):
            orden[atencion.fase_id] = len(orden) + 1
    nombres = {f.id: f.nombre for f in estructura.fases}
    postulante = m.Postulante.get_by_id(expediente.postulante_id)
    siguiente = rec.fase_siguiente
    return {
        "expediente": expediente,
        "numero": numero_visible(m.Proceso.get_by_id(expediente.proceso_id), expediente.numero_atencion),
        "nombre": f"{postulante.nombres} {postulante.apellidos}",
        "fase": fase,
        "estado": actual.estado,
        "version": actual.version,
        "flujo": flujo.nombre if flujo else None,
        "final_manual": flujo is not None and actual.fase_id == flujo.fase_final_id,
        "siguiente": siguiente.nombre if siguiente else None,
        "es_inicial": fase.tipo == wf.INICIAL,
        "es_mnp": fase.tipo == wf.CIERRE_DIGITAL,
        "via_qr": expediente.via_inicio == "QR",
        "orden": orden,
        "ruta": [{"fase": nombres[a.fase_id], "estado": a.estado, "actual": a is actual} for a in rec.ruta],
        "consultas": [f for f in estructura.fases if f.tipo == wf.CONSULTA],
        "flujos": opciones_de_flujo(estructura, nombres),
        "mnp": estructura.mnp,
    }


# ── Acciones ────────────────────────────────────────────────────────

def _foto(rec) -> dict:
    estructura = rec.estructura
    actual = rec.actual
    return {
        "estado": rec.estado,
        "fase_actual": estructura.fase(actual.fase_id).nombre if actual else None,
        "estado_atencion": actual.estado if actual else None,
        "flujo": estructura.flujo(rec.flujo_alternativo_id).nombre if rec.flujo_alternativo_id else None,
        "ruta": [estructura.fase(a.fase_id).nombre for a in rec.ruta if a.estado == wf.PLANIFICADA],
    }


def leer_version(valor) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        raise wf.ReglaVioladaError("No se pudo identificar la atención. Recargue la página.") from None


def _entero(valor, mensaje) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        raise wf.ReglaVioladaError(mensaje, 422) from None


def _mensaje(expediente, rec) -> str:
    numero = numero_visible(m.Proceso.get_by_id(expediente.proceso_id), expediente.numero_atencion)
    if rec.estado == wf.MATRICULADO:
        return f"{numero} quedó Matriculado" + (" (cierre manual)" if rec.tipo_cierre == wf.MANUAL else "")
    if rec.estado == wf.ABANDONO:
        return f"{numero} quedó en «Abandonó» y salió de la fila"
    actual = rec.actual
    fase = rec.estructura.fase(actual.fase_id).nombre
    if actual.estado == wf.EN_ATENCION:
        return f"Atendiendo a {numero} en {fase}"
    texto = f"{numero} debe dirigirse a: {fase}"
    if rec.flujo_alternativo_id:
        texto += f" ({rec.estructura.flujo(rec.flujo_alternativo_id).nombre})"
    return texto


def operar_auditado(expediente_id, usuario, accion, cambio, version=None) -> str:
    """Aplica `cambio(recorrido)` con el expediente bloqueado y lo audita en la misma
    transacción, con la foto del recorrido antes y después.

    `cambio` valida el permiso y puede devolver datos extra para la auditoría; con la clave
    "accion" reemplaza la acción auditada (ej.: el cierre manual).
    """
    def operacion(rec):
        antes = _foto(rec)
        return antes, cambio(rec) or {}

    with db.atomic():
        expediente, rec, (antes, extra) = recorridos.ejecutar(expediente_id, operacion, version)
        accion = extra.pop("accion", accion)
        llamados.cerrar_del_expediente(expediente.id, m.ahora())  # sale de la pantalla del hall
        auditoria.registrar(accion, "expediente", expediente.id, antes, {**_foto(rec), **extra}, usuario)
    if antes["estado"] != wf.MATRICULADO and rec.estado == wf.MATRICULADO:
        correo.enviar_matricula(expediente)  # RF-21, después de confirmar la transacción
    return _mensaje(expediente, rec)


def _operar(expediente_id, version, usuario, accion, cambio) -> str:
    """Acción sobre la atención actual: exige la versión vista y ser responsable de la fase."""
    def con_permiso(rec):
        actual = rec._exigir_actual()
        exigir_permiso(usuario, actual.fase_id)
        origen = rec.estructura.fase(actual.fase_id).nombre
        return {"origen": origen, **(cambio(rec) or {})}

    return operar_auditado(expediente_id, usuario, accion, con_permiso, leer_version(version))


def atender(expediente_id, version, usuario, ahora=None) -> str:
    ahora = ahora or m.ahora()
    return _operar(expediente_id, version, usuario, "ATENDER", lambda rec: rec.atender(usuario.id, ahora))


def marcar_abandono(expediente_id, version, usuario, ahora=None) -> str:
    ahora = ahora or m.ahora()
    return _operar(expediente_id, version, usuario, "ABANDONO", lambda rec: rec.marcar_abandono(usuario.id, ahora))


def finalizar(expediente_id, form, usuario, ahora=None) -> str:
    """Pantalla 7b. `accion`:

    - `seguir`: termina la atención con su `resultado` y pasa a la fase siguiente. En el flujo
      principal, las fases de consulta numeradas (`orden_<id>`) reemplazan la ruta planificada
      (sin ninguna, va directo a MNP). En la fase final de un flujo alternativo, exige
      `detalle_cierre` y `confirmar_cierre`.
    - `derivar`: «Derivar ahora» a la fase de consulta `destino`, interrumpiendo la atención.
    - `flujo`: deriva al flujo alternativo `entrada=<flujo_id>:<fase_id>`.
    - `devolver`: devuelve al flujo principal con `motivo` y la nueva ruta.
    """
    ahora = ahora or m.ahora()
    accion = (form.get("accion") or "seguir").strip()
    if accion not in ACCIONES:
        raise wf.ReglaVioladaError("Acción no válida", 400)
    resultado = (form.get("resultado") or "").strip()
    observacion = form.get("observacion")
    sin_atender = form.get("sin_atender") == "on"
    interrumpir = form.get("interrumpir") == "on"

    def con_observaciones():
        if resultado not in RESULTADOS:
            raise wf.ReglaVioladaError("Indique el resultado de la atención", 422)
        return resultado == "observaciones"

    def resultado_auditado(rec, atencion):
        return {"resultado": atencion.estado, "observacion": atencion.observacion,
                "sin_tiempo_atencion": atencion.sin_tiempo_atencion}

    def cambio(rec):
        actual = rec.actual
        estructura = rec.estructura
        if accion == "seguir":
            obs = con_observaciones()
            flujo = estructura.flujo(rec.flujo_alternativo_id) if rec.en_flujo_alternativo else None
            if flujo is not None and actual.fase_id == flujo.fase_final_id:
                detalle_cierre = (form.get("detalle_cierre") or "").strip()
                if not detalle_cierre:
                    raise wf.ReglaVioladaError("Debe ingresar el detalle del cierre manual", 422)
                if form.get("confirmar_cierre") != "on":
                    raise wf.ReglaVioladaError("Confirme el cierre manual de la matrícula", 422)
                rec.finalizar(usuario.id, ahora, con_observaciones=obs, observacion=observacion,
                              detalle_cierre=detalle_cierre, confirmar_sin_atender=sin_atender)
                return {"accion": "CIERRE_MANUAL", "detalle_cierre": detalle_cierre,
                        **resultado_auditado(rec, actual)}
            consultas = None
            if flujo is None and estructura.fase(actual.fase_id).tipo != wf.CIERRE_DIGITAL:
                consultas = consultas_numeradas(form, estructura)
            rec.finalizar(usuario.id, ahora, consultas=consultas, con_observaciones=obs,
                          observacion=observacion, confirmar_sin_atender=sin_atender)
            return resultado_auditado(rec, actual)

        if accion == "derivar":
            destino = _entero(form.get("destino"), "Elija la fase de destino")
            rec.derivar_ahora(destino, usuario.id, ahora)
            return {"accion": "DERIVAR_AHORA"}

        if accion == "flujo":
            try:
                flujo_id, fase_id = (int(parte) for parte in (form.get("entrada") or "").split(":"))
            except ValueError:
                raise wf.ReglaVioladaError("Elija el flujo alternativo y su fase de entrada", 422) from None
            obs = False if interrumpir or actual.estado == wf.EN_ESPERA else con_observaciones()
            rec.derivar_a_flujo(flujo_id, fase_id, usuario.id, ahora, interrumpir=interrumpir,
                                con_observaciones=obs, observacion=observacion)
            return {"accion": "DERIVAR_A_FLUJO", "flujo": estructura.flujo(flujo_id).nombre,
                    "fase_entrada": estructura.fase(fase_id).nombre,
                    "quitadas": [estructura.fase(a.fase_id).nombre for a in rec.quitadas],
                    **resultado_auditado(rec, actual)}

        motivo = (form.get("motivo") or "").strip()
        rec.devolver_a_principal(consultas_numeradas(form, estructura), motivo, usuario.id, ahora,
                                 interrumpir=interrumpir)
        return {"accion": "DEVOLVER_A_PRINCIPAL", "motivo": motivo,
                "quitadas": [estructura.fase(a.fase_id).nombre for a in rec.quitadas]}

    return _operar(expediente_id, form.get("version"), usuario, "FINALIZAR_ATENCION", cambio)
