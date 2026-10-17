"""Registro de auditoría de solo inserción (RF-24)."""

from flask import g, has_app_context, has_request_context, request

from app import models as m


def _fila(accion, entidad, entidad_id, anterior, nuevo, usuario, actor) -> dict:
    if usuario is None and has_app_context():
        usuario = g.get("usuario")
    if actor is None:
        actor = "USUARIO" if usuario is not None else "PUBLICO"
    ip = agente = None
    if has_request_context():
        ip = request.remote_addr
        agente = (request.user_agent.string or "")[:500] or None
    return {
        "usuario": usuario.id if usuario is not None else None,
        "rol": usuario.rol if usuario is not None else None,
        "actor": actor,
        "accion": accion,
        "entidad": entidad,
        "entidad_id": entidad_id,
        "valor_anterior": anterior,
        "valor_nuevo": nuevo,
        "ip": ip,
        "user_agent": agente,
    }


def registrar(accion, entidad, entidad_id=None, anterior=None, nuevo=None, usuario=None, actor=None):
    return m.Auditoria.create(**_fila(accion, entidad, entidad_id, anterior, nuevo, usuario, actor))


def registrar_lote(accion, entidad, cambios, usuario=None, actor=None):
    """Varios registros de una vez (ej.: una carga masiva). `cambios`: [(entidad_id, anterior, nuevo)]."""
    filas = [_fila(accion, entidad, entidad_id, anterior, nuevo, usuario, actor)
             for entidad_id, anterior, nuevo in cambios]
    for inicio in range(0, len(filas), 1000):
        m.Auditoria.insert_many(filas[inicio:inicio + 1000]).execute()
