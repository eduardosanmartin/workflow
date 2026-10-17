"""Carga el catálogo de carreras referencial de la §14.2 (solo desarrollo y pruebas).

Es idempotente: agrega las carreras que faltan y no modifica las existentes.
Antes de producción, se reemplaza con el catálogo oficial (RF-25).

Uso:
    python seed_carreras.py
"""

from app import models as m
from app.config import Config
from app.db import crear_base, db

CARRERAS = (
    ("ENF", "Enfermería"),
    ("KIN", "Kinesiología"),
    ("PSI", "Psicología"),
    ("DER", "Derecho"),
    ("ICO", "Ingeniería Comercial"),
    ("ICI", "Ingeniería Civil Industrial"),
    ("PEB", "Pedagogía en Educación Básica"),
    ("TSO", "Trabajo Social"),
)


def sembrar() -> int:
    """Devuelve cuántas carreras agregó."""
    with db.atomic():
        return (
            m.Carrera.insert_many([{"codigo": c, "nombre": n} for c, n in CARRERAS])
            .on_conflict_ignore()
            .as_rowcount()
            .execute()
        )


if __name__ == "__main__":
    db.initialize(crear_base(Config))
    with db.connection_context():
        agregadas = sembrar()
    print(f"Carreras agregadas: {agregadas} (catálogo referencial, no oficial)")
