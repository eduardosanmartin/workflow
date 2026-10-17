"""Inicio y cierre de sesión, y cambio de contraseña (RF-22)."""

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from app import models as m
from app.extensions import limiter
from app.security import destino_seguro, iniciar_sesion, publico
from app.services import auditoria, autenticacion

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
@publico
@limiter.limit("20 per minute", methods=["POST"])
def login():
    if g.usuario is not None:
        return redirect(url_for("principal.inicio"))
    destino = request.values.get("next", "")
    email = ""
    if request.method == "POST":
        email = request.form.get("email", "")
        try:
            usuario = autenticacion.autenticar(email, request.form.get("password", ""), m.ahora())
        except autenticacion.AutenticacionError as error:
            flash(str(error), "error")
        else:
            iniciar_sesion(usuario)
            if usuario.must_change_password:
                return redirect(url_for("auth.cambiar_password"))
            return redirect(destino_seguro(destino))
    return render_template("auth/login.html", email=email.strip(), destino=destino)


@bp.post("/logout")
def logout():
    auditoria.registrar("LOGOUT", "usuario", g.usuario.id)
    session.clear()
    flash("Sesión cerrada.", "exito")
    return redirect(url_for("auth.login"))


@bp.route("/cambiar-password", methods=["GET", "POST"])
def cambiar_password():
    if request.method == "POST":
        try:
            autenticacion.cambiar_password(
                g.usuario,
                request.form.get("actual", ""),
                request.form.get("nueva", ""),
                request.form.get("confirmacion", ""),
            )
        except autenticacion.AutenticacionError as error:
            flash(str(error), "error")
        else:
            flash("Contraseña actualizada.", "exito")
            return redirect(url_for("principal.inicio"))
    return render_template("auth/cambiar_password.html", obligatorio=g.usuario.must_change_password)
