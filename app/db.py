"""Conexión Peewee a PostgreSQL. El esquema lo definen las migraciones SQL, no Peewee."""

from peewee import DatabaseProxy, PostgresqlDatabase

db = DatabaseProxy()


def crear_base(config):
    return PostgresqlDatabase(
        config.DATABASE_NAME,
        user=config.DATABASE_USER,
        password=config.DATABASE_PASSWORD,
        host=config.DATABASE_HOST,
        port=config.DATABASE_PORT,
    )


def init_app(app, config):
    if db.obj is None:  # en pruebas, el fixture ya la inicializó
        db.initialize(crear_base(config))

    @app.before_request
    def _abrir():
        db.connect(reuse_if_open=True)

    @app.teardown_request
    def _cerrar(_exc):
        # En pruebas la conexión la maneja el fixture, que envuelve cada prueba en una transacción.
        if not app.testing and not db.is_closed():
            db.close()
