"""Reglas del workflow: orden secuencial (RF-14), cambio y reversión de estado (RF-13),
estado global (RF-16), bloqueo de estructura (RF-05) y permisos sobre fases (§4).

Son funciones puras sobre `FaseEstado`; la capa de datos las invoca dentro de la
transacción que bloquea el expediente (RNF-06).
"""

from dataclasses import dataclass

PENDIENTE = "PENDIENTE"
COMPLETADO = "COMPLETADO"
COMPLETADO_CON_OBSERVACIONES = "COMPLETADO_CON_OBSERVACIONES"
ESTADOS = (PENDIENTE, COMPLETADO, COMPLETADO_CON_OBSERVACIONES)
ESTADOS_COMPLETOS = (COMPLETADO, COMPLETADO_CON_OBSERVACIONES)

MATRICULADO = "MATRICULADO"
NO_MATRICULADO = "NO_MATRICULADO"
PROCESO_PENDIENTE = "PROCESO_PENDIENTE"

ROLES_TODAS_LAS_FASES = ("ADMIN", "GESTOR")


@dataclass(frozen=True)
class FaseEstado:
    fase_id: int
    orden: int
    nombre: str
    estado: str
    es_sistema: bool = False

    @property
    def completa(self) -> bool:
        return self.estado in ESTADOS_COMPLETOS


class ReglaVioladaError(Exception):
    def __init__(self, mensaje: str, codigo_http: int = 409):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.codigo_http = codigo_http


def _ordenadas(fases):
    return sorted(fases, key=lambda f: f.orden)


def fase_actual(fases):
    """Primera fase pendiente, o None si todas están completas."""
    return next((f for f in _ordenadas(fases) if not f.completa), None)


def fase_bloqueante(fases, fase_id):
    """Primera fase anterior a `fase_id` que sigue pendiente, o None si la fase está habilitada."""
    objetivo = _buscar(fases, fase_id)
    return next(
        (f for f in _ordenadas(fases) if f.orden < objetivo.orden and not f.completa), None
    )


def _buscar(fases, fase_id):
    for fase in fases:
        if fase.fase_id == fase_id:
            return fase
    raise ReglaVioladaError("La fase no pertenece al expediente", 404)


def validar_cambio_estado(
    fases, fase_id, nuevo_estado, observacion=None, motivo=None, proceso_estado="ACTIVO"
):
    """Lanza ReglaVioladaError si el cambio no está permitido."""
    if proceso_estado != "ACTIVO":
        raise ReglaVioladaError("El proceso no está activo: el expediente es de solo lectura")
    if nuevo_estado not in ESTADOS:
        raise ReglaVioladaError(f"Estado desconocido: {nuevo_estado}", 400)

    fase = _buscar(fases, fase_id)
    if fase.es_sistema:
        raise ReglaVioladaError(f"La fase «{fase.nombre}» no se puede modificar")
    if fase.estado == nuevo_estado:
        raise ReglaVioladaError(f"La fase «{fase.nombre}» ya tiene ese estado")

    bloqueante = fase_bloqueante(fases, fase_id)
    if bloqueante is not None:
        raise ReglaVioladaError(f"Requiere completar: «{bloqueante.nombre}»")

    if nuevo_estado == COMPLETADO_CON_OBSERVACIONES and not (observacion or "").strip():
        raise ReglaVioladaError("Debe ingresar una observación", 400)

    if nuevo_estado == PENDIENTE:
        if not (motivo or "").strip():
            raise ReglaVioladaError("Debe ingresar el motivo de la reversión", 400)
        posterior = next(
            (f for f in _ordenadas(fases) if f.orden > fase.orden and f.completa), None
        )
        if posterior is not None:
            raise ReglaVioladaError(f"Primero debe revertir la fase «{posterior.nombre}»")


def estado_global(fases, proceso_estado):
    if all(f.completa for f in fases):
        return MATRICULADO
    if proceso_estado == "CERRADO":
        return PROCESO_PENDIENTE
    return NO_MATRICULADO


def validar_cambio_estructura(proceso_iniciado: bool):
    """Agregar, eliminar o reordenar fases solo se permite si el proceso no tiene expedientes (RF-05)."""
    if proceso_iniciado:
        raise ReglaVioladaError(
            "El proceso ya tiene expedientes: no se pueden agregar, eliminar ni reordenar fases"
        )


def puede_modificar_fase(rol: str, fases_asignadas: set, fase_id: int) -> bool:
    if rol in ROLES_TODAS_LAS_FASES:
        return True
    if rol == "USUARIO_FASE":
        return fase_id in fases_asignadas
    return False
