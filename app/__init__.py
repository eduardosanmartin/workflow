import logging

from flask import Flask, render_template
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

    from app.blueprints import auth, principal
    from app.security import cargar_usuario

    app.before_request(cargar_usuario)
    app.register_blueprint(auth.bp)
    app.register_blueprint(principal.bp)

    @app.context_processor
    def _roles():
        return {"NOMBRE_ROL": principal.ROLES}

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
