"""Catálogo de carreras (RF-25), mantenido por el Administrador.

Las carreras que usa algún postulante no se eliminan ni cambian de código (el código es el
que se escribe en los archivos de carga): solo se desactivan.
"""

import re

from peewee import JOIN, fn

from app import models as m
from app.db import db
from app.services import archivos, auditoria
from app.services import workflow as wf

_CODIGO = re.compile(r"^[A-Z0-9_-]{1,20}$")
COLUMNAS = ("codigo_carrera", "nombre_carrera")


def _invalido(mensaje):
    return wf.ReglaVioladaError(mensaje, 422)


def _obtener(carrera_id):
    carrera = m.Carrera.get_or_none(m.Carrera.id == carrera_id)
    if carrera is None:
        raise wf.ReglaVioladaError("La carrera no existe", 404)
    return carrera


def _foto(carrera):
    return {"codigo": carrera.codigo, "nombre": carrera.nombre, "activo": carrera.activo}


def _validar(datos) -> tuple:
    codigo = (datos.get("codigo") or "").strip().upper()
    nombre = " ".join((datos.get("nombre") or "").split())
    if not _CODIGO.match(codigo):
        raise _invalido("El código es obligatorio: hasta 20 letras, números, guiones o guiones bajos, sin espacios")
    if not nombre:
        raise _invalido("El nombre es obligatorio")
    if len(nombre) > 200:
        raise _invalido("El nombre admite hasta 200 caracteres")
    return codigo, nombre


def _exigir_codigo_libre(codigo, carrera_id=None):
    otra = m.Carrera.get_or_none(m.Carrera.codigo == codigo)
    if otra is not None and otra.id != carrera_id:
        raise _invalido(f"La carrera {codigo} ya existe")


def usada(carrera_id) -> bool:
    return m.PostulanteCarrera.select().where(m.PostulanteCarrera.carrera == carrera_id).exists()


def listar() -> list:
    cantidad = fn.COUNT(m.PostulanteCarrera.postulante)
    filas = (m.Carrera.select(m.Carrera, cantidad.alias("postulantes"))
             .join(m.PostulanteCarrera, JOIN.LEFT_OUTER, on=(m.PostulanteCarrera.carrera == m.Carrera.id))
             .group_by(m.Carrera.id)
             .order_by(m.Carrera.activo.desc(), m.Carrera.nombre))
    return [{"carrera": c, "postulantes": c.postulantes} for c in filas]


def crear(datos, por):
    codigo, nombre = _validar(datos)
    with db.atomic():
        _exigir_codigo_libre(codigo)
        carrera = m.Carrera.create(codigo=codigo, nombre=nombre)
        auditoria.registrar("CREAR_CARRERA", "carrera", carrera.id, nuevo=_foto(carrera), usuario=por)
    return carrera


def editar(carrera_id, datos, por):
    codigo, nombre = _validar(datos)
    with db.atomic():
        carrera = _obtener(carrera_id)
        anterior = _foto(carrera)
        if codigo != carrera.codigo:
            if usada(carrera.id):
                raise _invalido("No se puede cambiar el código de una carrera que ya tienen postulantes")
            _exigir_codigo_libre(codigo, carrera.id)
        carrera.codigo, carrera.nombre = codigo, nombre
        carrera.save()
        auditoria.registrar("EDITAR_CARRERA", "carrera", carrera.id, anterior, _foto(carrera), por)
    return carrera


def cambiar_estado(carrera_id, activo, por):
    with db.atomic():
        carrera = _obtener(carrera_id)
        if carrera.activo == activo:
            return carrera
        carrera.activo = activo
        carrera.save()
        auditoria.registrar("CAMBIAR_ESTADO_CARRERA", "carrera", carrera.id,
                            {"activo": not activo}, {"activo": activo}, por)
    return carrera


def eliminar(carrera_id, por):
    with db.atomic():
        carrera = _obtener(carrera_id)
        if usada(carrera.id):
            raise wf.ReglaVioladaError("La carrera tiene postulantes: desactívela en vez de eliminarla", 409)
        auditoria.registrar("ELIMINAR_CARRERA", "carrera", carrera.id, anterior=_foto(carrera), usuario=por)
        carrera.delete_instance()


def importar(contenido: bytes, nombre_archivo: str, por) -> dict:
    """Carga del catálogo desde CSV o Excel: crea las carreras nuevas y actualiza el nombre de
    las existentes. Las filas con errores se informan y no se importan."""
    filas = archivos.leer_tabla(contenido, nombre_archivo, COLUMNAS)
    resultado = {"creadas": 0, "actualizadas": 0, "sin_cambios": 0, "errores": []}
    with db.atomic():
        existentes = {c.codigo: c for c in m.Carrera.select()}
        vistos = {}
        for numero, fila in filas:
            try:
                codigo, nombre = _validar({"codigo": fila["codigo_carrera"], "nombre": fila["nombre_carrera"]})
            except wf.ReglaVioladaError as error:
                resultado["errores"].append((numero, error.mensaje))
                continue
            if codigo in vistos:
                resultado["errores"].append((numero, f"El código {codigo} se repite en la fila {vistos[codigo]}"))
                continue
            vistos[codigo] = numero
            carrera = existentes.get(codigo)
            if carrera is None:
                m.Carrera.create(codigo=codigo, nombre=nombre)
                resultado["creadas"] += 1
            elif carrera.nombre != nombre:
                carrera.nombre = nombre
                carrera.save()
                resultado["actualizadas"] += 1
            else:
                resultado["sin_cambios"] += 1
        auditoria.registrar("IMPORTAR_CARRERAS", "carrera", None, usuario=por, nuevo={
            "archivo": nombre_archivo, "creadas": resultado["creadas"], "actualizadas": resultado["actualizadas"],
            "sin_cambios": resultado["sin_cambios"], "con_error": len(resultado["errores"]),
        })
    return resultado
