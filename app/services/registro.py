"""Carga manual de postulantes (RF-07) y edición de sus datos (Administrador y Gestor).

Solo registra o actualiza datos: no inicia procesos, no crea expedientes ni envía correos.
"""

from datetime import date

from app import models as m
from app.db import db
from app.services import auditoria, tiempo
from app.services import workflow as wf
from app.services.inicio import (
    CAMPOS_EDITABLES, DatosInvalidosError, _actualizar_postulante, _codigos_permitidos, _crear_postulante,
    buscar_postulante, datos_de, normalizar_rut, proceso_activo,
)
from app.services.postulantes import validar_postulante


class PostulanteExistenteError(wf.ReglaVioladaError):
    def __init__(self, postulante):
        super().__init__(f"El RUT {postulante.rut} ya está registrado", 409)
        self.postulante = postulante


def puede_registrar(usuario) -> bool:
    """§4: Administrador y Gestor; Usuario de fase solo con `puede_registrar_postulantes`."""
    if usuario is None or not usuario.activo:
        return False
    return usuario.rol in wf.ROLES_TODAS_LAS_FASES or bool(usuario.puede_registrar_postulantes)


def puede_editar(usuario) -> bool:
    return usuario is not None and usuario.activo and usuario.rol in wf.ROLES_TODAS_LAS_FASES


def eliminar_en_para_carga(hoy=None) -> date:
    """RF-26: la fecha de eliminación del proceso ACTIVO o, sin uno, el 30 de noviembre del año."""
    proceso = proceso_activo()
    if proceso is not None:
        return proceso.fecha_eliminacion
    hoy = hoy or m.ahora().astimezone(tiempo.ZONA).date()
    return date(hoy.year, 11, 30)


def registrar(datos: dict, por, hoy=None):
    if not puede_registrar(por):
        raise wf.ReglaVioladaError("No tiene permiso para registrar postulantes", 403)
    rut = normalizar_rut(datos.get("rut"))
    with db.atomic():
        existente = buscar_postulante(rut)
        if existente is not None:
            raise PostulanteExistenteError(existente)
        limpios, errores = validar_postulante({**datos, "rut": rut}, _codigos_permitidos({}))
        if errores:
            raise DatosInvalidosError(errores)
        postulante = _crear_postulante(limpios, "CARGA_MANUAL")
        postulante.eliminar_en = eliminar_en_para_carga(hoy)
        postulante.save()
        auditoria.registrar("CREAR_POSTULANTE", "postulante", postulante.id,
                            nuevo={**limpios, "origen": "CARGA_MANUAL"}, usuario=por)
    return postulante


def _obtener(postulante_id):
    postulante = m.Postulante.get_or_none(m.Postulante.id == postulante_id)
    if postulante is None:
        raise wf.ReglaVioladaError("El postulante no existe", 404)
    return postulante


def editar(postulante_id, datos: dict, por) -> dict:
    """Edita los datos de RF-06 salvo el RUT. Devuelve {campo: (antes, después)}."""
    if not puede_editar(por):
        raise wf.ReglaVioladaError("Solo el Administrador y el Gestor editan postulantes", 403)
    with db.atomic():
        postulante = _obtener(postulante_id)
        anteriores = datos_de(postulante)
        limpios, errores = validar_postulante({**datos, "rut": postulante.rut}, _codigos_permitidos(anteriores))
        if errores:
            raise DatosInvalidosError(errores)
        antes, despues = _actualizar_postulante(postulante, anteriores, limpios, CAMPOS_EDITABLES)
        if despues:
            auditoria.registrar("EDITAR_POSTULANTE", "postulante", postulante.id,
                                anterior=antes, nuevo=despues, usuario=por)
    return {campo: (antes[campo], despues[campo]) for campo in despues}


def cambiar_estado(postulante_id, activo: bool, por):
    """RF-06: un RUT mal ingresado no se corrige; el postulante se desactiva y se crea otro."""
    if not puede_editar(por):
        raise wf.ReglaVioladaError("Solo el Administrador y el Gestor editan postulantes", 403)
    with db.atomic():
        postulante = _obtener(postulante_id)
        if postulante.activo != activo:
            postulante.activo = activo
            postulante.actualizado_en = m.ahora()
            postulante.save()
            auditoria.registrar("CAMBIAR_ESTADO_POSTULANTE", "postulante", postulante.id,
                                {"activo": not activo}, {"activo": activo}, por)
    return postulante
