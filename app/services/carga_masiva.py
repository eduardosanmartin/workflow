"""Carga masiva de postulantes desde CSV o Excel (RF-08), para el Administrador y el Gestor.

Flujo: se sube el archivo y se muestra una vista previa (nuevos, existentes con sus cambios y
filas con error); al confirmar, se importa en una sola transacción según la política elegida
para los existentes.

El archivo original no se guarda (RNF-02). Entre la vista previa y la confirmación se conservan
solo las filas leídas, en un archivo temporal del servidor que vence en una hora; tras importar,
queda solo el detalle de errores para descargarlo.
"""

import csv
import io
import json
import os
import re
import secrets
import time

import openpyxl
from flask import current_app
from peewee import IntegrityError

from app import models as m
from app.db import db
from app.services import archivos, auditoria
from app.services import workflow as wf
from app.services.inicio import CARRERAS, _actualizar_postulante
from app.services.postulantes import CAMPOS_EDITABLES, validar_postulante
from app.services.registro import eliminar_en_para_carga, puede_editar
from app.services.rut import RutInvalido, normalizar

COLUMNAS = {
    "rut_postulante": "rut",
    "nombres_postulante": "nombres",
    "apellidos_postulante": "apellidos",
    "telefono_postulante": "telefono",
    "correo_personal_postulante": "correo_personal",
    "carrera1_postulante": "carrera1",
    "carrera2_postulante": "carrera2",
    "carrera3_postulante": "carrera3",
}
OBLIGATORIAS = tuple(c for c in COLUMNAS if not c.startswith(("carrera2", "carrera3")))
ALIAS = {"telefonos_postulante": "telefono_postulante", "teléfonos_postulante": "telefono_postulante"}
POLITICAS = {
    "NO_MODIFICAR": "No modificar",
    "COMPLETAR": "Completar solo los campos vacíos",
    "SOBRESCRIBIR": "Sobrescribir",
}
ETIQUETAS = {
    "nombres": "Nombres", "apellidos": "Apellidos", "telefono": "Teléfono", "correo_personal": "Correo personal",
    "carrera1": "Carrera 1", "carrera2": "Carrera 2", "carrera3": "Carrera 3",
}

_VIGENCIA = 60 * 60
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_LOTE = 1000


def _exigir_permiso(usuario):
    if not puede_editar(usuario):
        raise wf.ReglaVioladaError("Solo el Administrador y el Gestor usan la carga masiva", 403)


# ── Carga pendiente (entre la vista previa y la confirmación) ───────

def _carpeta() -> str:
    carpeta = current_app.config.get("CARGAS_DIR") or os.path.join(current_app.instance_path, "cargas")
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def _ruta(token) -> str:
    return os.path.join(_carpeta(), f"{token}.json")


def _purgar_vencidas():
    limite = time.time() - _VIGENCIA
    for nombre in os.listdir(_carpeta()):
        ruta = os.path.join(_carpeta(), nombre)
        if nombre.endswith(".json") and os.path.getmtime(ruta) < limite:
            try:
                os.remove(ruta)
            except OSError:
                pass


def _guardar(token, datos):
    ruta = _ruta(token)
    temporal = f"{ruta}.tmp"
    with open(temporal, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False)
    os.replace(temporal, ruta)


def _leer(token, usuario) -> dict:
    no_existe = wf.ReglaVioladaError("La carga no existe. Vuelva a subir el archivo.", 404)
    if not token or not _TOKEN.match(token):
        raise no_existe
    try:
        with open(_ruta(token), encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except (OSError, ValueError):
        raise no_existe from None
    if datos.get("usuario_id") != usuario.id:
        raise no_existe
    if datos.get("creado", 0) < time.time() - _VIGENCIA:
        raise wf.ReglaVioladaError("La vista previa venció. Vuelva a subir el archivo.", 409)
    return datos


# ── Análisis ────────────────────────────────────────────────────────

def analizar_archivo(contenido: bytes, nombre_archivo: str, usuario) -> str:
    """Lee el archivo y deja la carga pendiente. Devuelve su token."""
    _exigir_permiso(usuario)
    filas = archivos.leer_tabla(contenido, nombre_archivo, OBLIGATORIAS, ALIAS)
    _purgar_vencidas()
    token = secrets.token_urlsafe(24)
    _guardar(token, {
        "usuario_id": usuario.id, "archivo": os.path.basename(nombre_archivo)[:255], "creado": time.time(),
        "filas": [[numero, {COLUMNAS[c]: v for c, v in fila.items() if c in COLUMNAS}] for numero, fila in filas],
    })
    return token


def _rut_o_none(valor):
    try:
        return normalizar(valor)
    except RutInvalido:
        return None


def _existentes_en_lote(ruts) -> dict:
    """{rut: (postulante, datos de RF-06)} con pocas consultas, para miles de filas."""
    resultado = {}
    ruts = list(ruts)
    for inicio in range(0, len(ruts), _LOTE):
        postulantes = {p.id: p for p in m.Postulante.select().where(m.Postulante.rut.in_(ruts[inicio:inicio + _LOTE]))}
        if not postulantes:
            continue
        datos = {pid: {"rut": p.rut, "nombres": p.nombres, "apellidos": p.apellidos, "telefono": p.telefono or "",
                       "correo_personal": p.correo_personal, **{c: None for c in CARRERAS}}
                 for pid, p in postulantes.items()}
        filas = (m.PostulanteCarrera.select(m.PostulanteCarrera.postulante, m.PostulanteCarrera.preferencia, m.Carrera.codigo)
                 .join(m.Carrera).where(m.PostulanteCarrera.postulante.in_(list(postulantes))).tuples())
        for postulante_id, preferencia, codigo in filas:
            datos[postulante_id][f"carrera{preferencia}"] = codigo
        for pid, p in postulantes.items():
            resultado[p.rut] = (p, datos[pid])
    return resultado


def _diferencias(anteriores, limpios) -> dict:
    cambios = {}
    for campo in CAMPOS_EDITABLES:
        antes, despues = anteriores.get(campo) or None, limpios.get(campo) or None
        if antes != despues:
            cambios[campo] = (antes, despues)
    return cambios


def _para_completar(anteriores, limpios) -> dict:
    """Solo los campos vacíos, sin dejar carreras repetidas ni la 3 sin la 2."""
    cambios = {c: (None, limpios[c]) for c in CAMPOS_EDITABLES if not anteriores.get(c) and limpios.get(c)}
    carreras = [cambios.get(c, (None, anteriores.get(c)))[1] for c in CARRERAS]
    llenas = [c for c in carreras if c]
    if len(set(llenas)) != len(llenas) or (carreras[2] and not carreras[1]):
        cambios = {c: v for c, v in cambios.items() if c not in CARRERAS}
    return cambios


def _analizar(filas) -> dict:
    activas = {c.codigo for c in m.Carrera.select(m.Carrera.codigo).where(m.Carrera.activo == True)}  # noqa: E712
    existentes = _existentes_en_lote({r for r in (_rut_o_none(f.get("rut")) for _, f in filas) if r})
    analisis = {"total": len(filas), "nuevos": [], "existentes": [], "errores": []}
    primera_fila = {}
    for numero, fila in filas:
        limpios, errores = validar_postulante(fila, activas)
        if errores:
            analisis["errores"].append({"fila": numero, "rut": fila.get("rut", ""), "motivo": "; ".join(errores.values())})
            continue
        rut = limpios["rut"]
        if rut in primera_fila:
            analisis["errores"].append({"fila": numero, "rut": fila.get("rut", ""),
                                        "motivo": f"RUT repetido en el archivo (fila {primera_fila[rut]})"})
            continue
        primera_fila[rut] = numero
        nombre = f"{limpios['nombres']} {limpios['apellidos']}"
        if rut not in existentes:
            analisis["nuevos"].append({"fila": numero, "rut": rut, "nombre": nombre, "datos": limpios})
            continue
        postulante, anteriores = existentes[rut]
        analisis["existentes"].append({
            "fila": numero, "rut": rut, "nombre": nombre, "postulante": postulante, "anteriores": anteriores,
            "datos": limpios, "cambios": _diferencias(anteriores, limpios),
            "completar": _para_completar(anteriores, limpios),
        })
    return analisis


def vista_previa(token, usuario) -> dict:
    _exigir_permiso(usuario)
    pendiente = _leer(token, usuario)
    if "filas" not in pendiente:
        raise wf.ReglaVioladaError("Esta carga ya fue importada", 409)
    analisis = _analizar(pendiente["filas"])
    analisis.update(token=token, archivo=pendiente["archivo"])
    return analisis


# ── Importación ─────────────────────────────────────────────────────

def _crear_nuevos(nuevos, carga, eliminar_en):
    codigos = {c.codigo: c.id for c in m.Carrera.select()}
    for inicio in range(0, len(nuevos), _LOTE):
        lote = nuevos[inicio:inicio + _LOTE]
        ids = (m.Postulante.insert_many([{
            "rut": n["datos"]["rut"], "nombres": n["datos"]["nombres"], "apellidos": n["datos"]["apellidos"],
            "telefono": n["datos"]["telefono"], "correo_personal": n["datos"]["correo_personal"],
            "origen": "CARGA_MASIVA", "carga": carga.id, "eliminar_en": eliminar_en,
        } for n in lote]).returning(m.Postulante.id).tuples().execute())
        carreras = []
        for (postulante_id,), nuevo in zip(ids, lote):
            for preferencia, campo in enumerate(CARRERAS, 1):
                if nuevo["datos"][campo]:
                    carreras.append({"postulante": postulante_id, "preferencia": preferencia,
                                     "carrera": codigos[nuevo["datos"][campo]]})
        if carreras:
            m.PostulanteCarrera.insert_many(carreras).execute()


def importar(token, politica, usuario):
    """Importa en una sola transacción, revalidando contra la base actual. Devuelve la CargaMasiva."""
    _exigir_permiso(usuario)
    if politica not in POLITICAS:
        raise wf.ReglaVioladaError("Elija qué hacer con los postulantes existentes", 422)
    try:
        return _importar(token, politica, usuario)
    except IntegrityError:
        # Otro registro de alguno de los RUT se confirmó durante la importación
        raise wf.ReglaVioladaError("Otro usuario registró postulantes del archivo mientras se importaba. "
                                   "Revise la vista previa e importe de nuevo.", 409) from None


def _importar(token, politica, usuario):
    with db.atomic():
        pendiente = _leer(token, usuario)
        if "filas" not in pendiente:
            raise wf.ReglaVioladaError("Esta carga ya fue importada", 409)
        # Bloquea las cargas concurrentes para que dos importaciones no creen el mismo RUT
        db.execute_sql("LOCK TABLE carga_masiva IN SHARE ROW EXCLUSIVE MODE")
        analisis = _analizar(pendiente["filas"])
        carga = m.CargaMasiva.create(usuario=usuario.id, nombre_archivo=pendiente["archivo"],
                                     politica_existentes=politica, total_filas=analisis["total"],
                                     con_error=len(analisis["errores"]))
        _crear_nuevos(analisis["nuevos"], carga, eliminar_en_para_carga())
        carga.creados = len(analisis["nuevos"])

        ediciones = []
        for existente in analisis["existentes"]:
            cambios = {"NO_MODIFICAR": {}, "COMPLETAR": existente["completar"],
                       "SOBRESCRIBIR": existente["cambios"]}[politica]
            if not cambios:
                carga.omitidos += 1
                continue
            antes, despues = _actualizar_postulante(existente["postulante"], existente["anteriores"],
                                                    existente["datos"], list(cambios))
            ediciones.append((existente["postulante"].id, antes, {**despues, "carga_id": carga.id}))
            carga.actualizados += 1
        auditoria.registrar_lote("EDITAR_POSTULANTE", "postulante", ediciones, usuario=usuario)
        carga.save()
        auditoria.registrar("CARGA_MASIVA", "carga_masiva", carga.id, usuario=usuario, nuevo={
            "archivo": carga.nombre_archivo, "politica": politica, "total_filas": carga.total_filas,
            "creados": carga.creados, "actualizados": carga.actualizados, "omitidos": carga.omitidos,
            "con_error": carga.con_error,
        })
        # Ya no se conservan las filas: solo el detalle de errores, para descargarlo
        _guardar(token, {"usuario_id": usuario.id, "archivo": pendiente["archivo"], "creado": pendiente["creado"],
                         "carga_id": carga.id, "errores": analisis["errores"]})
    return carga


def resultado(token, usuario) -> dict:
    _exigir_permiso(usuario)
    pendiente = _leer(token, usuario)
    if "carga_id" not in pendiente:
        raise wf.ReglaVioladaError("La carga aún no se importa", 404)
    return {"carga": m.CargaMasiva.get_by_id(pendiente["carga_id"]), "errores": pendiente["errores"], "token": token}


def errores_csv(token, usuario) -> str:
    _exigir_permiso(usuario)
    pendiente = _leer(token, usuario)
    errores = pendiente.get("errores")
    if errores is None:
        errores = _analizar(pendiente["filas"])["errores"]
    salida = io.StringIO()
    salida.write("﻿")  # para que Excel lo abra en UTF-8
    escritor = csv.writer(salida, delimiter=";")
    escritor.writerow(["fila", "rut_postulante", "motivo"])
    for error in errores:
        escritor.writerow([error["fila"], error["rut"], error["motivo"]])
    return salida.getvalue()


# ── Plantillas ──────────────────────────────────────────────────────

def _ejemplo() -> list:
    carrera = m.Carrera.select().where(m.Carrera.activo == True).order_by(m.Carrera.nombre).first()  # noqa: E712
    return ["12.345.678-5", "Juan Andrés", "Pérez Soto", "+56 9 1234 5678", "juan.perez@gmail.com",
            carrera.codigo if carrera else "", "", ""]


def plantilla_csv() -> bytes:
    salida = io.StringIO()
    salida.write("﻿")
    escritor = csv.writer(salida, delimiter=";", lineterminator="\n")
    escritor.writerow(list(COLUMNAS))
    escritor.writerow(_ejemplo())
    return salida.getvalue().encode("utf-8")


def plantilla_xlsx() -> bytes:
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.title = "Postulantes"
    hoja.append(list(COLUMNAS))
    hoja.append(_ejemplo())
    for fila in hoja.iter_rows():
        for celda in fila:
            celda.number_format = "@"  # texto: no perder ceros ni la K del RUT
    for columna, ancho in zip("ABCDEFGH", (16, 22, 22, 20, 30, 20, 20, 20)):
        hoja.column_dimensions[columna].width = ancho
    referencia = libro.create_sheet("Carreras")
    referencia.append(["codigo_carrera", "nombre_carrera"])
    for carrera in m.Carrera.select().where(m.Carrera.activo == True).order_by(m.Carrera.nombre):  # noqa: E712
        referencia.append([carrera.codigo, carrera.nombre])
    referencia.column_dimensions["A"].width = 16
    referencia.column_dimensions["B"].width = 40
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()
