"""Aplica en orden los scripts SQL pendientes de `migrations/` (§7).

Cada script corre en su propia transacción y queda registrado en `schema_migrations`.

Uso:
    python migrate.py            # aplica las pendientes en la base del .env
    python migrate.py --estado   # muestra aplicadas y pendientes
"""

import argparse
import re
import sys
from pathlib import Path

import psycopg2

CARPETA = Path(__file__).resolve().parent / "migrations"
PATRON = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def scripts_disponibles(carpeta=CARPETA):
    scripts = []
    for archivo in sorted(carpeta.glob("*.sql")):
        coincidencia = PATRON.match(archivo.name)
        if not coincidencia:
            raise ValueError(f"Nombre de migración inválido: {archivo.name} (se espera 000_nombre.sql)")
        scripts.append((coincidencia.group(1), archivo))
    versiones = [v for v, _ in scripts]
    if len(versiones) != len(set(versiones)):
        raise ValueError("Hay dos migraciones con el mismo número")
    return scripts


def _asegurar_registro(cur):
    cur.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version    VARCHAR(3) PRIMARY KEY,
               archivo    VARCHAR(255) NOT NULL,
               aplicada_en TIMESTAMPTZ NOT NULL DEFAULT now()
           )"""
    )


def aplicadas(conexion):
    with conexion, conexion.cursor() as cur:
        _asegurar_registro(cur)
        cur.execute("SELECT version FROM schema_migrations")
        return {fila[0] for fila in cur.fetchall()}


def migrar(conexion, carpeta=CARPETA, salida=print):
    """Aplica las migraciones pendientes. Devuelve la lista de versiones aplicadas."""
    ya_aplicadas = aplicadas(conexion)
    nuevas = []
    for version, archivo in scripts_disponibles(carpeta):
        if version in ya_aplicadas:
            continue
        with conexion, conexion.cursor() as cur:
            cur.execute(archivo.read_text(encoding="utf-8"))
            cur.execute(
                "INSERT INTO schema_migrations (version, archivo) VALUES (%s, %s)",
                (version, archivo.name),
            )
        salida(f"Aplicada {archivo.name}")
        nuevas.append(version)
    if not nuevas:
        salida("La base de datos está al día")
    return nuevas


def conectar(config):
    return psycopg2.connect(
        dbname=config.DATABASE_NAME,
        user=config.DATABASE_USER,
        password=config.DATABASE_PASSWORD,
        host=config.DATABASE_HOST,
        port=config.DATABASE_PORT,
    )


def main(argv=None):
    from app.config import Config

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--estado", action="store_true", help="muestra aplicadas y pendientes")
    args = parser.parse_args(argv)

    conexion = conectar(Config)
    try:
        if args.estado:
            hechas = aplicadas(conexion)
            for version, archivo in scripts_disponibles():
                print(f"[{'x' if version in hechas else ' '}] {archivo.name}")
        else:
            migrar(conexion)
    finally:
        conexion.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
