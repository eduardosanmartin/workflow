"""Validación y normalización de RUT chileno (módulo 11)."""

import re

_MAX_DIGITOS_CUERPO = 8
_SEPARADORES = re.compile(r"[.\-\s]")
_FORMATO = re.compile(r"^(\d+)([\dK])$")


class RutInvalido(ValueError):
    pass


def calcular_dv(cuerpo: str) -> str:
    suma = 0
    factor = 2
    for digito in reversed(cuerpo):
        suma += int(digito) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - suma % 11
    if resto == 11:
        return "0"
    if resto == 10:
        return "K"
    return str(resto)


def normalizar(rut) -> str:
    """Devuelve el RUT con formato `12345678-9` o lanza RutInvalido.

    Acepta el RUT con o sin puntos, guion y espacios, y con `k` minúscula.
    """
    if rut is None:
        raise RutInvalido("RUT vacío")
    limpio = _SEPARADORES.sub("", str(rut)).upper()
    match = _FORMATO.match(limpio)
    if not match:
        raise RutInvalido("Formato de RUT inválido")
    cuerpo, dv = match.groups()
    cuerpo = cuerpo.lstrip("0")
    if not cuerpo or len(cuerpo) > _MAX_DIGITOS_CUERPO:
        raise RutInvalido("Formato de RUT inválido")
    if calcular_dv(cuerpo) != dv:
        raise RutInvalido("Dígito verificador inválido")
    return f"{cuerpo}-{dv}"


def es_valido(rut) -> bool:
    try:
        normalizar(rut)
    except RutInvalido:
        return False
    return True
