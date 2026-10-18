"""Panel de inicio y salud del servicio."""

from flask import Blueprint, g, render_template

from app.security import publico
from app.services import retencion

bp = Blueprint("principal", __name__)

ROLES = {"ADMIN": "Administrador", "GESTOR": "Gestor", "USUARIO_FASE": "Usuario de fase"}


@bp.get("/")
def inicio():
    return render_template("inicio.html", avisos=retencion.avisos(g.get("usuario")))


@bp.get("/salud")
@publico
def salud():
    return {"estado": "ok"}
