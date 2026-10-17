"""Contraseñas, sesión y control de acceso (RF-22, RNF-01).

El acceso se niega por defecto: toda vista exige sesión salvo las marcadas con `@publico`.
"""

import functools
import time

import bcrypt
from flask import abort, current_app, flash, g, redirect, request, session, url_for

from app import models as m

LARGO_MINIMO = 10

# Vistas que un usuario con `must_change_password` puede abrir
_PERMITIDAS_SIN_CAMBIO = {"auth.cambiar_password", "auth.logout", "static"}


# ── Contraseñas ──

def _rondas():
    try:
        return current_app.config.get("BCRYPT_ROUNDS", 12)
    except RuntimeError:  # fuera de una aplicación (scripts)
        return 12


def hash_password(clave: str) -> str:
    return bcrypt.hashpw(clave.encode(), bcrypt.gensalt(_rondas())).decode()


def verificar_password(clave: str, hash_: str | None) -> bool:
    if not hash_:
        return False
    try:
        return bcrypt.checkpw(clave.encode(), hash_.encode())
    except ValueError:  # hash con formato inválido
        return False


@functools.cache
def _hash_ficticio():
    return hash_password("clave-ficticia-para-igualar-tiempos")


def igualar_tiempo(clave: str):
    """Gasta lo mismo que una verificación real, para no revelar si el correo existe."""
    verificar_password(clave, _hash_ficticio())


def error_politica(clave: str) -> str | None:
    if len(clave) < LARGO_MINIMO:
        return f"La contraseña debe tener al menos {LARGO_MINIMO} caracteres."
    return None


# ── Sesión ──

def iniciar_sesion(usuario):
    session.clear()
    session.permanent = True
    ahora = time.time()
    session["usuario_id"] = usuario.id
    session["inicio"] = ahora
    session["ultimo_acceso"] = ahora


def _sesion_vencida(config) -> bool:
    ahora = time.time()
    return (
        ahora - session.get("inicio", 0) > config["PERMANENT_SESSION_LIFETIME"]
        or ahora - session.get("ultimo_acceso", 0) > config["SESION_INACTIVIDAD_SEGUNDOS"]
    )


def publico(vista):
    vista.publico = True
    return vista


def _es_publica() -> bool:
    if request.endpoint is None or request.endpoint == "static":
        return True
    vista = current_app.view_functions.get(request.endpoint)
    return getattr(vista, "publico", False)


def cargar_usuario():
    """`before_request`: valida la sesión, carga `g.usuario` y aplica el acceso por defecto."""
    g.usuario = None
    if request.endpoint == "static":
        return None
    if "usuario_id" in session:
        if _sesion_vencida(current_app.config):
            session.clear()
            flash("Su sesión expiró. Ingrese nuevamente.", "aviso")
        else:
            usuario = m.Usuario.get_or_none(
                (m.Usuario.id == session["usuario_id"]) & (m.Usuario.activo == True)  # noqa: E712
            )
            if usuario is None:
                session.clear()
            else:
                if request.headers.get("X-Sondeo") != "1":
                    # La actualización automática de la bandeja no cuenta como actividad (RF-22)
                    session["ultimo_acceso"] = time.time()
                g.usuario = usuario

    if g.usuario is None:
        if _es_publica():
            return None
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
    if g.usuario.must_change_password and request.endpoint not in _PERMITIDAS_SIN_CAMBIO:
        return redirect(url_for("auth.cambiar_password"))
    return None


def requiere_rol(*roles):
    def decorador(vista):
        @functools.wraps(vista)
        def envoltura(*args, **kwargs):
            if g.get("usuario") is None or g.usuario.rol not in roles:
                abort(403)
            return vista(*args, **kwargs)

        return envoltura

    return decorador


def destino_seguro(destino: str | None) -> str:
    """Solo rutas internas, para evitar redirecciones abiertas."""
    if destino and destino.startswith("/") and not destino.startswith("//") and "\\" not in destino:
        return destino
    return url_for("principal.inicio")
