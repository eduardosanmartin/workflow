"""Reglas del recorrido dinámico (SPEC v0.8): ruta del expediente (RF-12), estados de una
atención (RF-13), derivación (RF-14), estado global (RF-16), correcciones (RF-27),
abandono (RF-28) y flujos alternativos manuales (RF-29).

Son objetos de dominio en memoria, sin acceso a datos. La capa de datos carga el
expediente con sus atenciones, invoca una operación de `Recorrido` dentro de la
transacción que bloquea el expediente (RNF-06) y luego persiste el resultado:
`ruta` (en orden, la posición es la `secuencia`) y `quitadas` (atenciones
planificadas que salieron de la ruta, para borrarlas y auditarlas).
"""

from dataclasses import dataclass, field
from datetime import datetime

# Tipos de fase (RF-02)
INICIAL = "INICIAL"
CONSULTA = "CONSULTA"
CIERRE_DIGITAL = "CIERRE_DIGITAL"

# Estados de una atención (RF-13)
PLANIFICADA = "PLANIFICADA"
EN_ESPERA = "EN_ESPERA"
EN_ATENCION = "EN_ATENCION"
COMPLETADO = "COMPLETADO"
COMPLETADO_CON_OBSERVACIONES = "COMPLETADO_CON_OBSERVACIONES"
DERIVADA = "DERIVADA"
ABANDONADA = "ABANDONADA"
ESTADOS_ACTIVOS = (EN_ESPERA, EN_ATENCION)
ESTADOS_COMPLETOS = (COMPLETADO, COMPLETADO_CON_OBSERVACIONES)

# Estado global del expediente (RF-16)
EN_CURSO = "EN_CURSO"
ABANDONO = "ABANDONO"
MATRICULADO = "MATRICULADO"
PROCESO_PENDIENTE = "PROCESO_PENDIENTE"

# Tipo de cierre (RF-16, RF-29)
DIGITAL = "DIGITAL"
MANUAL = "MANUAL"

ROLES_TODAS_LAS_FASES = ("ADMIN", "GESTOR")


class ReglaVioladaError(Exception):
    def __init__(self, mensaje: str, codigo_http: int = 409):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.codigo_http = codigo_http


def _texto(valor) -> str:
    return (valor or "").strip()


# ── Estructura del proceso ──────────────────────────────────────────


@dataclass(frozen=True)
class Fase:
    id: int
    nombre: str
    tipo: str
    mostrar_en_qr: bool = True


@dataclass(frozen=True)
class FlujoAlternativo:
    id: int
    nombre: str
    fases: tuple  # ids de fase en orden; la última es la fase final manual

    @property
    def fase_final_id(self):
        return self.fases[-1] if self.fases else None


class Estructura:
    """Fases y flujos alternativos de un proceso."""

    def __init__(self, fases, flujos=()):
        self.fases = list(fases)
        self.flujos = list(flujos)
        self._fases = {f.id: f for f in self.fases}
        self._flujos = {f.id: f for f in self.flujos}

    def _unica(self, tipo):
        candidatas = [f for f in self.fases if f.tipo == tipo]
        return candidatas[0] if len(candidatas) == 1 else None

    @property
    def inicial(self):
        return self._unica(INICIAL)

    @property
    def mnp(self):
        return self._unica(CIERRE_DIGITAL)

    def fase(self, fase_id):
        try:
            return self._fases[fase_id]
        except KeyError:
            raise ReglaVioladaError("La fase no pertenece al proceso", 404) from None

    def flujo(self, flujo_id):
        try:
            return self._flujos[flujo_id]
        except KeyError:
            raise ReglaVioladaError("El flujo alternativo no pertenece al proceso", 404) from None

    def consultas_qr(self):
        return [f for f in self.fases if f.tipo == CONSULTA and f.mostrar_en_qr]


def validar_flujo(estructura: Estructura, flujo: FlujoAlternativo):
    """Errores de configuración de un flujo alternativo (RF-29)."""
    errores = []
    if not flujo.fases:
        errores.append(f"El flujo alternativo «{flujo.nombre}» no tiene fases")
    if len(set(flujo.fases)) != len(flujo.fases):
        errores.append(f"En el flujo alternativo «{flujo.nombre}» se repite una fase")
    for fase_id in flujo.fases:
        fase = estructura._fases.get(fase_id)
        if fase is None or fase.tipo != CONSULTA:
            errores.append(f"El flujo alternativo «{flujo.nombre}» solo puede tener fases de consulta")
            break
    return errores


def validar_activacion(estructura: Estructura, fases_con_responsable: set):
    """Errores que impiden activar el proceso (RF-01, RF-04, RF-29). Vacío si es válido."""
    errores = []
    if estructura.inicial is None:
        errores.append("El proceso debe tener exactamente una fase inicial")
    if estructura.mnp is None:
        errores.append("El proceso debe tener exactamente una fase de cierre digital (MNP)")
    for fase in estructura.fases:
        if fase.id not in fases_con_responsable:
            errores.append(f"La fase «{fase.nombre}» no tiene responsables")
    for flujo in estructura.flujos:
        errores.extend(validar_flujo(estructura, flujo))
    return errores


def validar_cambio_estructura(proceso_iniciado: bool):
    """Fases y flujos alternativos solo se agregan o eliminan sin expedientes (RF-05)."""
    if proceso_iniciado:
        raise ReglaVioladaError(
            "El proceso ya tiene expedientes: no se pueden agregar ni eliminar fases "
            "o flujos alternativos, ni cambiar su tipo o sus fases"
        )


def puede_actuar(rol: str, fases_asignadas: set, fase_id: int) -> bool:
    """Permiso para atender, finalizar y derivar en una fase (§4)."""
    if rol in ROLES_TODAS_LAS_FASES:
        return True
    if rol == "USUARIO_FASE":
        return fase_id in fases_asignadas
    return False


# ── Atenciones ──────────────────────────────────────────────────────


@dataclass(eq=False)
class Atencion:
    fase_id: int
    estado: str
    flujo_alternativo_id: int = None
    id: int = None
    planificada_por: str = None
    derivada_desde: "Atencion" = None
    en_espera_desde: datetime = None
    inicio_atencion: datetime = None
    fin_atencion: datetime = None
    atendido_por: str = None
    observacion: str = None
    detalle_cierre: str = None
    sin_tiempo_atencion: bool = False


def tiempo_espera(atencion: Atencion):
    if atencion.inicio_atencion is None or atencion.en_espera_desde is None:
        return None
    return atencion.inicio_atencion - atencion.en_espera_desde


def tiempo_atencion(atencion: Atencion):
    if atencion.fin_atencion is None or atencion.inicio_atencion is None:
        return None
    return atencion.fin_atencion - atencion.inicio_atencion


# ── Recorrido del expediente ────────────────────────────────────────


@dataclass(eq=False)
class Recorrido:
    estructura: Estructura
    ruta: list
    estado: str = EN_CURSO
    flujo_alternativo_id: int = None
    tipo_cierre: str = None
    fase_cierre_id: int = None
    matriculado_en: datetime = None
    fase_detenida_id: int = None
    proceso_estado: str = "ACTIVO"
    quitadas: list = field(default_factory=list)

    # ── Inicio (RF-11) ──

    @classmethod
    def iniciar_por_staff(cls, estructura, usuario, ahora, consultas=(), flujo_id=None, fase_entrada_id=None):
        iniciado = Atencion(
            estructura.inicial.id, COMPLETADO, en_espera_desde=ahora,
            inicio_atencion=ahora, fin_atencion=ahora, atendido_por=usuario,
        )
        rec = cls(estructura, [iniciado])
        if flujo_id is not None:
            rec._entrar_a_flujo(flujo_id, fase_entrada_id, usuario, ahora)
        else:
            rec._planificar_principal(consultas, usuario)
            rec._activar_siguiente(ahora)
        return rec

    @classmethod
    def iniciar_por_qr(cls, estructura, ahora, consultas=()):
        ofrecidas = {f.id for f in estructura.consultas_qr()}
        if any(c not in ofrecidas for c in consultas):
            raise ReglaVioladaError("Tema de duda no disponible en el formulario", 400)
        iniciado = Atencion(estructura.inicial.id, EN_ESPERA, en_espera_desde=ahora)
        rec = cls(estructura, [iniciado])
        rec._planificar_principal(consultas, "PUBLICO")
        return rec

    # ── Consultas ──

    @property
    def actual(self):
        return next((a for a in self.ruta if a.estado in ESTADOS_ACTIVOS), None)

    @property
    def fase_actual(self):
        actual = self.actual
        return self.estructura.fase(actual.fase_id) if actual else None

    @property
    def fase_siguiente(self):
        if self.actual is None:
            return None
        siguiente = next((a for a in self.ruta if a.estado == PLANIFICADA), None)
        return self.estructura.fase(siguiente.fase_id) if siguiente else None

    @property
    def en_flujo_alternativo(self):
        return self.flujo_alternativo_id is not None

    @property
    def con_observaciones(self):
        return any(a.estado == COMPLETADO_CON_OBSERVACIONES for a in self.ruta)

    @property
    def hubo_abandono(self):
        ultima = self._ultima_cerrada()
        return ultima is not None and ultima.estado == ABANDONADA

    # ── Acciones de la bandeja (RF-13, RF-14, RF-17) ──

    def atender(self, usuario, ahora):
        actual = self._exigir_actual()
        if actual.estado != EN_ESPERA:
            raise ReglaVioladaError("El postulante ya está siendo atendido")
        actual.estado = EN_ATENCION
        actual.inicio_atencion = ahora
        actual.atendido_por = usuario

    def finalizar(self, usuario, ahora, consultas=None, con_observaciones=False,
                  observacion=None, detalle_cierre=None, confirmar_sin_atender=False):
        """Termina la atención actual y pasa a la siguiente fase de la ruta.

        En el flujo principal, `consultas` (si se indica) reemplaza las fases de consulta
        que quedaban planificadas; MNP queda siempre al final. En la fase final de un
        flujo alternativo, `detalle_cierre` es obligatorio y registra el cierre manual.
        """
        actual = self._exigir_actual()
        fase = self.estructura.fase(actual.fase_id)
        es_final_manual = self.en_flujo_alternativo and actual.fase_id == self._flujo().fase_final_id

        if self.en_flujo_alternativo and consultas is not None:
            raise ReglaVioladaError("En un flujo alternativo la secuencia de fases es fija")
        if fase.tipo == CIERRE_DIGITAL and consultas:
            raise ReglaVioladaError("Para enviarlo a una consulta desde MNP use «Derivar ahora»")
        if es_final_manual and not _texto(detalle_cierre):
            raise ReglaVioladaError("Debe ingresar el detalle del cierre manual", 400)
        if detalle_cierre and not es_final_manual:
            raise ReglaVioladaError("El detalle del cierre solo se registra en la fase final de un flujo alternativo", 400)
        if consultas is not None:
            self._validar_consultas(consultas)

        self._terminar(actual, usuario, ahora, con_observaciones, observacion, confirmar_sin_atender)

        if fase.tipo == CIERRE_DIGITAL:
            self._matricular(DIGITAL, fase.id, ahora)
            return
        if es_final_manual:
            actual.detalle_cierre = _texto(detalle_cierre)
            self._matricular(MANUAL, fase.id, ahora)
            return
        if consultas is not None:
            self._quitar_planificadas()
            self._planificar_principal(consultas, usuario)
        self._activar_siguiente(ahora)

    def derivar_ahora(self, destino_id, usuario, ahora):
        """Deriva a una consulta antes de terminar; la fase interrumpida se replanifica después."""
        actual = self._exigir_actual()
        self._exigir_principal()
        if self.estructura.fase(actual.fase_id).tipo == INICIAL:
            raise ReglaVioladaError("En la fase inicial se define la ruta al finalizar")
        self._validar_consultas([destino_id])
        self._interrumpir(actual, ahora)
        nueva = Atencion(destino_id, EN_ESPERA, en_espera_desde=ahora,
                         planificada_por=usuario, derivada_desde=actual)
        replanificada = Atencion(actual.fase_id, PLANIFICADA, planificada_por=usuario)
        posicion = self._posicion_activa()
        self.ruta[posicion:posicion] = [nueva, replanificada]

    def marcar_abandono(self, usuario, ahora):
        actual = self._exigir_actual()
        if actual.estado != EN_ESPERA:
            raise ReglaVioladaError("Solo se marca «Abandonó» a un postulante en espera")
        actual.estado = ABANDONADA
        actual.fin_atencion = ahora
        actual.atendido_por = usuario
        self.estado = ABANDONO

    def reactivar(self, usuario, ahora, fase_id=None):
        self._exigir_proceso_activo()
        if self.estado != ABANDONO:
            raise ReglaVioladaError("Solo se reactiva un expediente en «Abandonó»")
        fase_id = fase_id if fase_id is not None else self._ultima_cerrada().fase_id
        if self.en_flujo_alternativo:
            if fase_id not in self._flujo().fases:
                raise ReglaVioladaError("En un flujo alternativo solo se reactiva en una fase del flujo")
        elif self.estructura.fase(fase_id).tipo == INICIAL:
            raise ReglaVioladaError("No se reactiva en la fase inicial")
        nueva = Atencion(fase_id, EN_ESPERA, flujo_alternativo_id=self.flujo_alternativo_id,
                         en_espera_desde=ahora, planificada_por=usuario)
        self.ruta.insert(self._posicion_activa(), nueva)
        if not self.en_flujo_alternativo:
            self._asegurar_mnp_al_final(usuario)
        self.estado = EN_CURSO

    # ── Flujos alternativos (RF-29) ──

    def derivar_a_flujo(self, flujo_id, fase_entrada_id, usuario, ahora, interrumpir=False,
                        con_observaciones=False, observacion=None):
        actual = self._exigir_actual()
        self._exigir_principal()
        flujo = self.estructura.flujo(flujo_id)
        if fase_entrada_id not in flujo.fases:
            raise ReglaVioladaError(f"La fase de entrada no pertenece al flujo «{flujo.nombre}»", 400)
        self._cerrar_para_cambiar_de_flujo(actual, usuario, ahora, interrumpir, con_observaciones, observacion)
        self._quitar_planificadas()
        self._entrar_a_flujo(flujo_id, fase_entrada_id, usuario, ahora)

    def devolver_a_principal(self, consultas, motivo, usuario, ahora, interrumpir=False):
        actual = self._exigir_actual()
        if not self.en_flujo_alternativo:
            raise ReglaVioladaError("El expediente ya está en el flujo principal")
        if not _texto(motivo):
            raise ReglaVioladaError("Debe ingresar el motivo", 400)
        self._validar_consultas(consultas)
        self._cerrar_para_cambiar_de_flujo(actual, usuario, ahora, interrumpir, False, None)
        self._quitar_planificadas()
        self.flujo_alternativo_id = None
        self._planificar_principal(consultas, usuario)
        self._activar_siguiente(ahora)

    # ── Correcciones (RF-27, Gestor y Administrador) ──

    def corregir_ruta(self, consultas, motivo, usuario, ahora):
        self._exigir_actual()
        self._exigir_principal()
        if not _texto(motivo):
            raise ReglaVioladaError("Debe ingresar el motivo", 400)
        if self.fase_actual.tipo == CIERRE_DIGITAL and consultas:
            raise ReglaVioladaError("Para enviarlo a una consulta desde MNP use «Derivar ahora»")
        self._validar_consultas(consultas)
        self._quitar_planificadas()
        self._planificar_principal(consultas, usuario)

    def reabrir(self, motivo, usuario, ahora):
        """Vuelve a EN_ESPERA la última atención completada, si la siguiente no comenzó."""
        self._exigir_proceso_activo()
        if not _texto(motivo):
            raise ReglaVioladaError("Debe ingresar el motivo", 400)
        if self.estado not in (EN_CURSO, MATRICULADO):
            raise ReglaVioladaError("El expediente no admite reapertura en su estado actual")
        actual = self.actual
        if actual is not None and actual.estado != EN_ESPERA:
            raise ReglaVioladaError("La atención siguiente ya comenzó")
        posicion = self.ruta.index(actual) if actual else self._posicion_activa()
        anterior = self.ruta[posicion - 1] if posicion > 0 else None
        if anterior is None or anterior.estado not in ESTADOS_COMPLETOS:
            raise ReglaVioladaError("No hay una atención completada inmediatamente anterior")
        if self.estructura.fase(anterior.fase_id).tipo == INICIAL:
            raise ReglaVioladaError("La atención de la fase inicial no se reabre")

        if actual is not None:
            actual.estado = PLANIFICADA
            actual.en_espera_desde = None
        anterior.estado = EN_ESPERA
        anterior.en_espera_desde = ahora
        anterior.inicio_atencion = anterior.fin_atencion = anterior.atendido_por = None
        anterior.observacion = anterior.detalle_cierre = None
        anterior.sin_tiempo_atencion = False
        if self.estado == MATRICULADO:
            self.estado = EN_CURSO
            self.tipo_cierre = self.fase_cierre_id = self.matriculado_en = None

    # ── Cierre del proceso (RF-01, RF-16) ──

    def cerrar_proceso(self):
        if self.estado in (EN_CURSO, ABANDONO):
            detenida = self.actual or self._ultima_cerrada()
            self.fase_detenida_id = detenida.fase_id if detenida else None
            self.estado = PROCESO_PENDIENTE
        self.proceso_estado = "CERRADO"

    # ── Internos ──

    def _exigir_proceso_activo(self):
        if self.proceso_estado != "ACTIVO":
            raise ReglaVioladaError("El proceso no está activo: el expediente es de solo lectura")

    def _exigir_actual(self):
        self._exigir_proceso_activo()
        if self.estado == MATRICULADO:
            raise ReglaVioladaError("El postulante ya está matriculado")
        actual = self.actual
        if actual is None:
            raise ReglaVioladaError("El expediente no tiene una atención en curso")
        return actual

    def _exigir_principal(self):
        if self.en_flujo_alternativo:
            raise ReglaVioladaError("En un flujo alternativo la secuencia de fases es fija")

    def _flujo(self):
        return self.estructura.flujo(self.flujo_alternativo_id)

    def _validar_consultas(self, consultas):
        for fase_id in consultas:
            if self.estructura.fase(fase_id).tipo != CONSULTA:
                raise ReglaVioladaError("La ruta solo puede incluir fases de consulta", 400)

    def _posicion_activa(self):
        """Posición donde va la próxima atención activa: antes de las planificadas."""
        return sum(1 for a in self.ruta if a.estado != PLANIFICADA)

    def _ultima_cerrada(self):
        cerradas = [a for a in self.ruta if a.estado != PLANIFICADA]
        return cerradas[-1] if cerradas else None

    def _planificar_principal(self, consultas, usuario):
        self._validar_consultas(consultas)
        for fase_id in consultas:
            self.ruta.append(Atencion(fase_id, PLANIFICADA, planificada_por=usuario))
        self._asegurar_mnp_al_final(usuario)

    def _asegurar_mnp_al_final(self, usuario):
        mnp_id = self.estructura.mnp.id
        ultima = self.ruta[-1]
        if not (ultima.fase_id == mnp_id and ultima.estado in (PLANIFICADA, EN_ESPERA, EN_ATENCION)):
            self.ruta.append(Atencion(mnp_id, PLANIFICADA, planificada_por=usuario))

    def _quitar_planificadas(self):
        planificadas = [a for a in self.ruta if a.estado == PLANIFICADA]
        self.ruta = [a for a in self.ruta if a.estado != PLANIFICADA]
        self.quitadas.extend(planificadas)

    def _activar_siguiente(self, ahora):
        siguiente = next((a for a in self.ruta if a.estado == PLANIFICADA), None)
        if siguiente is not None:
            siguiente.estado = EN_ESPERA
            siguiente.en_espera_desde = ahora

    def _entrar_a_flujo(self, flujo_id, fase_entrada_id, usuario, ahora):
        flujo = self.estructura.flujo(flujo_id)
        if fase_entrada_id is None:
            fase_entrada_id = flujo.fases[0]
        if fase_entrada_id not in flujo.fases:
            raise ReglaVioladaError(f"La fase de entrada no pertenece al flujo «{flujo.nombre}»", 400)
        desde = flujo.fases.index(fase_entrada_id)
        for fase_id in flujo.fases[desde:]:
            self.ruta.append(Atencion(fase_id, PLANIFICADA, flujo_alternativo_id=flujo_id,
                                      planificada_por=usuario))
        self.flujo_alternativo_id = flujo_id
        self._activar_siguiente(ahora)

    def _terminar(self, atencion, usuario, ahora, con_observaciones, observacion, confirmar_sin_atender):
        if con_observaciones and not _texto(observacion):
            raise ReglaVioladaError("Debe ingresar una observación", 400)
        if atencion.estado == EN_ESPERA:
            if not confirmar_sin_atender:
                raise ReglaVioladaError("No se presionó «Atender»: confirme para finalizar sin tiempo de atención")
            atencion.inicio_atencion = ahora
            atencion.atendido_por = usuario
            atencion.sin_tiempo_atencion = True
        atencion.fin_atencion = ahora
        if con_observaciones:
            atencion.estado = COMPLETADO_CON_OBSERVACIONES
            atencion.observacion = _texto(observacion)
        else:
            atencion.estado = COMPLETADO

    def _interrumpir(self, atencion, ahora):
        atencion.estado = DERIVADA
        atencion.fin_atencion = ahora

    def _cerrar_para_cambiar_de_flujo(self, atencion, usuario, ahora, interrumpir, con_observaciones, observacion):
        if interrumpir or atencion.estado == EN_ESPERA:
            self._interrumpir(atencion, ahora)
        else:
            self._terminar(atencion, usuario, ahora, con_observaciones, observacion, False)

    def _matricular(self, tipo_cierre, fase_id, ahora):
        self.estado = MATRICULADO
        self.tipo_cierre = tipo_cierre
        self.fase_cierre_id = fase_id
        self.matriculado_en = ahora
