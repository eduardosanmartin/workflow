"""Fixtures de base de datos: `workflow_test` se recrea con las migraciones una vez por
sesión, y cada prueba corre dentro de una transacción que se revierte al terminar."""

import psycopg2
import pytest

import migrate
from app.config import TestConfig
from app.db import crear_base, db


@pytest.fixture(scope="session")
def base_de_pruebas():
    conexion = migrate.conectar(TestConfig)
    try:
        with conexion, conexion.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        migrate.migrar(conexion, salida=lambda _m: None)
    except psycopg2.OperationalError as e:  # pragma: no cover
        pytest.skip(f"PostgreSQL de pruebas no disponible: {e}")
    finally:
        conexion.close()
    db.initialize(crear_base(TestConfig))
    db.connect(reuse_if_open=True)
    yield db
    db.close()


@pytest.fixture
def bd(base_de_pruebas):
    with db.atomic() as transaccion:
        yield db
        transaccion.rollback()
