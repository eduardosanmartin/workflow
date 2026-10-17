import logging

from flask import Flask, g, render_template
from peewee import OperationalError

from app import db
from app.config import Config
from app.extensions import csrf, limiter

log = logging.getLogger(__name__)


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    db.init_app(app, config_class)
    csrf.init_app(app)
    limiter.init_app(app)

    from app.blueprints import admin, auth, iniciar, principal, qr, seguimiento
    from app.security import cargar_usuario
    from app.services.inicio import puede_iniciar

    app.before_request(cargar_usuario)
    for blueprint in (auth, principal, admin, iniciar, qr, seguimiento):
        app.register_blueprint(blueprint.bp)

    @app.context_processor
    def _roles():
        def _puede_iniciar():
            usuario = g.get("usuario")
            return usuario is not None and not usuario.must_change_password and puede_iniciar(usuario)
        return {"NOMBRE_ROL": principal.ROLES, "puede_iniciar": _puede_iniciar}

    @app.errorhandler(403)
    def _prohibido(_e):
        return render_template("errores/403.html"), 403

    @app.errorhandler(404)
    def _no_encontrado(_e):
        return render_template("errores/404.html"), 404

    if not app.testing:
        _crear_superadmin(config_class)
    return app


def _crear_superadmin(config):
    """RF-22: el primer Administrador se crea desde el .env al iniciar la aplicación."""
    if not config.SUPERADMIN_EMAIL:
        return
    from app.services.autenticacion import asegurar_superadmin

    try:
        with db.db.connection_context():
            asegurar_superadmin(config)
    except OperationalError as e:
        log.warning("No se pudo verificar el super administrador (¿base de datos detenida?): %s", e)
    except ValueError as e:
        log.error("Super administrador no creado: %s", e)
