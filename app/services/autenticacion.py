"""Reglas de autenticación (RF-22): login con bloqueo, cambio de contraseña y super admin."""

from datetime import timedelta

from peewee import fn

from app import models as m
from app.db import db
from app.security import error_politica, hash_password, igualar_tiempo, verificar_password
from app.services import auditoria

MAX_INTENTOS = 5
DURACION_BLOQUEO = timedelta(minutes=15)
MENSAJE_INCORRECTO = "Correo o contraseña incorrectos."


class AutenticacionError(Exception):
    pass


def normalizar_email(email: str) -> str:
    return (email or "").strip().lower()


def _buscar(email):
    return m.Usuario.select().where(fn.lower(m.Usuario.email) == email)


def autenticar(email, clave, ahora):
    """Devuelve el usuario si las credenciales son válidas; si no, lanza AutenticacionError.

    Los intentos fallidos se cuentan bloqueando la fila del usuario, para que dos intentos
    simultáneos no se pisen el contador. El error se lanza después de confirmar la
    transacción, para que el contador y la auditoría del fallo queden guardados.
    """
    with db.atomic():
        usuario, error = _intentar(normalizar_email(email), clave, ahora)
    if error:
        raise AutenticacionError(error)
    return usuario


def _intentar(email, clave, ahora):
    usuario = _buscar(email).for_update().first()
    if usuario is None or not usuario.activo or not usuario.password_hash:
        igualar_tiempo(clave)
        auditoria.registrar("LOGIN_FALLIDO", "usuario", usuario.id if usuario else None,
                            nuevo={"email": email}, usuario=usuario,
                            actor="USUARIO" if usuario else "PUBLICO")
        return None, MENSAJE_INCORRECTO

    if usuario.bloqueado_hasta is not None and usuario.bloqueado_hasta > ahora:
        minutos = max(1, -(-int((usuario.bloqueado_hasta - ahora).total_seconds()) // 60))
        auditoria.registrar("LOGIN_FALLIDO", "usuario", usuario.id,
                            nuevo={"email": email, "motivo": "bloqueada"}, usuario=usuario)
        return None, (
            "Cuenta bloqueada temporalmente por intentos fallidos. "
            f"Intente nuevamente en {minutos} minuto{'s' if minutos != 1 else ''}."
        )

    if not verificar_password(clave, usuario.password_hash):
        usuario.intentos_fallidos += 1
        auditoria.registrar("LOGIN_FALLIDO", "usuario", usuario.id,
                            nuevo={"email": email, "intentos": usuario.intentos_fallidos},
                            usuario=usuario)
        if usuario.intentos_fallidos < MAX_INTENTOS:
            usuario.save()
            return None, MENSAJE_INCORRECTO
        usuario.intentos_fallidos = 0
        usuario.bloqueado_hasta = ahora + DURACION_BLOQUEO
        usuario.save()
        auditoria.registrar("BLOQUEO_CUENTA", "usuario", usuario.id,
                            nuevo={"bloqueado_hasta": usuario.bloqueado_hasta.isoformat()},
                            usuario=usuario)
        return None, "Cuenta bloqueada temporalmente por 15 minutos tras 5 intentos fallidos."

    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    usuario.save()
    auditoria.registrar("LOGIN", "usuario", usuario.id, usuario=usuario)
    return usuario, None


def cambiar_password(usuario, actual, nueva, confirmacion):
    """Lanza AutenticacionError con el motivo si el cambio no es válido."""
    if not verificar_password(actual, usuario.password_hash):
        raise AutenticacionError("La contraseña actual no es correcta.")
    error = error_politica(nueva)
    if error:
        raise AutenticacionError(error)
    if nueva != confirmacion:
        raise AutenticacionError("La nueva contraseña y su confirmación no coinciden.")
    if nueva == actual:
        raise AutenticacionError("La nueva contraseña debe ser distinta de la actual.")
    with db.atomic():
        usuario.password_hash = hash_password(nueva)
        usuario.must_change_password = False
        usuario.save()
        auditoria.registrar("CAMBIO_PASSWORD", "usuario", usuario.id, usuario=usuario)


def asegurar_superadmin(config):
    """Crea el primer Administrador desde el `.env` si aún no existe (RF-22). Es idempotente:
    una cuenta existente con ese correo no se modifica."""
    email = normalizar_email(config.SUPERADMIN_EMAIL)
    if not email:
        return None
    with db.atomic():
        existente = _buscar(email).first()
        if existente is not None:
            return existente
        if config.SUPERADMIN_PASSWORD_HASH:
            hash_, cambiar = config.SUPERADMIN_PASSWORD_HASH, False
        elif config.SUPERADMIN_PASSWORD:
            # Una clave en texto plano en el .env se trata como temporal
            hash_, cambiar = hash_password(config.SUPERADMIN_PASSWORD), True
        else:
            raise ValueError("Falta SUPERADMIN_PASSWORD_HASH o SUPERADMIN_PASSWORD en el .env")
        usuario = m.Usuario.create(
            email=email, nombre=config.SUPERADMIN_NAME or "Administrador", rol="ADMIN",
            password_hash=hash_, must_change_password=cambiar,
        )
        auditoria.registrar("CREAR_SUPERADMIN", "usuario", usuario.id,
                            nuevo={"email": email, "rol": "ADMIN"}, usuario=None, actor="SISTEMA")
        return usuario
