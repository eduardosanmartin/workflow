"""Validación de datos del postulante (RF-06) y enmascaramiento para el QR (RF-10).

Se comparte entre la carga manual, la carga masiva, el inicio por staff y el inicio por QR.
"""

import re

from app.services.rut import RutInvalido, normalizar

OBLIGATORIOS = ("rut", "nombres", "apellidos", "telefono", "correo_personal", "carrera1")
CAMPOS_EDITABLES = ("nombres", "apellidos", "telefono", "correo_personal", "carrera1", "carrera2", "carrera3")
CARRERAS = ("carrera1", "carrera2", "carrera3")

_MAX_NOMBRE = 100
_TELEFONO = re.compile(r"^\+?\d{8,15}$")
_SEPARADORES_TELEFONO = re.compile(r"[\s\-]")
_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ESPACIOS = re.compile(r"\s+")


def _texto(valor):
    if valor is None:
        return ""
    return _ESPACIOS.sub(" ", str(valor)).strip()


def validar_postulante(datos: dict, carreras_activas: set) -> tuple[dict, dict]:
    """Valida y normaliza los datos de RF-06.

    Devuelve (limpios, errores). `errores` mapea cada campo con problemas a su motivo.
    `carreras_activas` es el conjunto de códigos de carrera aceptados.
    """
    limpios: dict = {}
    errores: dict = {}

    rut = _texto(datos.get("rut"))
    if not rut:
        errores["rut"] = "El RUT es obligatorio"
    else:
        try:
            limpios["rut"] = normalizar(rut)
        except RutInvalido as e:
            errores["rut"] = str(e)

    for campo, etiqueta in (("nombres", "Los nombres"), ("apellidos", "Los apellidos")):
        valor = _texto(datos.get(campo))
        if not valor:
            errores[campo] = f"{etiqueta} son obligatorios"
        elif len(valor) > _MAX_NOMBRE:
            errores[campo] = f"{etiqueta} no pueden superar {_MAX_NOMBRE} caracteres"
        else:
            limpios[campo] = valor

    telefono = _SEPARADORES_TELEFONO.sub("", _texto(datos.get("telefono")))
    if not telefono:
        errores["telefono"] = "El teléfono es obligatorio"
    elif not _TELEFONO.match(telefono):
        errores["telefono"] = "El teléfono debe tener entre 8 y 15 dígitos, con + opcional"
    else:
        limpios["telefono"] = telefono

    correo = _texto(datos.get("correo_personal")).lower()
    if not correo:
        errores["correo_personal"] = "El correo personal es obligatorio"
    elif not _CORREO.match(correo):
        errores["correo_personal"] = "El correo personal no tiene un formato válido"
    else:
        limpios["correo_personal"] = correo

    _validar_carreras(datos, carreras_activas, limpios, errores)
    return limpios, errores


def _validar_carreras(datos, carreras_activas, limpios, errores):
    vistas = set()
    for i, campo in enumerate(CARRERAS, start=1):
        codigo = _texto(datos.get(campo)).upper() or None
        limpios[campo] = None
        if codigo is None:
            if i == 1:
                errores[campo] = "La carrera 1 es obligatoria"
            continue
        if i == 3 and limpios.get("carrera2") is None and "carrera2" not in errores:
            errores[campo] = "La carrera 3 requiere la carrera 2"
        elif codigo not in carreras_activas:
            errores[campo] = f"La carrera {codigo} no existe o no está activa"
        elif codigo in vistas:
            errores[campo] = "Las carreras deben ser distintas entre sí"
        else:
            limpios[campo] = codigo
            vistas.add(codigo)


def campos_pendientes(datos: dict) -> list[str]:
    """Campos que el postulante aún debe completar: obligatorios vacíos y carreras opcionales faltantes."""
    return [campo for campo in CAMPOS_EDITABLES if not _texto(datos.get(campo))]


def enmascarar_nombre(valor):
    if not valor:
        return valor
    return " ".join(palabra[:2] + "***" for palabra in valor.split())


def enmascarar_telefono(valor):
    if not valor:
        return valor
    digitos = valor.lstrip("+")
    if digitos.startswith("569") and len(digitos) == 11:
        return "+569 ****" + digitos[-4:]
    return "****" + digitos[-4:]


def enmascarar_correo(valor):
    if not valor or "@" not in valor:
        return valor
    usuario, dominio = valor.split("@", 1)
    return f"{usuario[:1]}***@{dominio}"


def enmascarar(datos: dict) -> dict:
    """Copia de los datos del postulante con nombres, teléfono y correo enmascarados (RF-10)."""
    resultado = dict(datos)
    resultado["nombres"] = enmascarar_nombre(datos.get("nombres"))
    resultado["apellidos"] = enmascarar_nombre(datos.get("apellidos"))
    resultado["telefono"] = enmascarar_telefono(datos.get("telefono"))
    resultado["correo_personal"] = enmascarar_correo(datos.get("correo_personal"))
    return resultado
