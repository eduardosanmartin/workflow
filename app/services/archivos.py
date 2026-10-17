"""Lectura de archivos CSV y Excel para las cargas (RF-08 y RF-25).

- Encabezados en la primera fila, sin distinguir mayúsculas y sin espacios en los bordes.
- CSV en UTF-8 (con o sin BOM), con separador `,` o `;` detectado automáticamente.
- Excel: solo la primera hoja, y todas las celdas como texto.
- Las filas vacías se ignoran; cada fila conserva su número en el archivo.
"""

import csv
import io
import zipfile
from datetime import date, datetime

import openpyxl

from app.services import workflow as wf

MAX_FILAS = 20_000


class ArchivoInvalido(wf.ReglaVioladaError):
    def __init__(self, mensaje):
        super().__init__(mensaje, 422)


def _celda(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "1" if valor else "0"
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))  # Excel guarda los números como decimales: 56912345678.0
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    return str(valor).strip()


def _filas_csv(contenido: bytes):
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ArchivoInvalido("El archivo CSV debe estar guardado en UTF-8 (en Excel: «CSV UTF-8»).") from None
    primera = texto.split("\n", 1)[0]
    separador = ";" if primera.count(";") > primera.count(",") else ","
    for fila in csv.reader(io.StringIO(texto, newline=""), delimiter=separador):
        yield [_celda(valor) for valor in fila]


def _filas_xlsx(contenido: bytes):
    try:
        libro = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        raise ArchivoInvalido("No se pudo leer el archivo Excel. Verifique que sea un .xlsx válido.") from None
    try:
        for fila in libro.worksheets[0].iter_rows(values_only=True):
            yield [_celda(valor) for valor in fila]
    finally:
        libro.close()


def leer_tabla(contenido: bytes, nombre_archivo: str, obligatorias, alias=None) -> list:
    """Devuelve [(número de fila, {columna: texto})]. Lanza ArchivoInvalido si el formato no
    sirve, falta una columna obligatoria o se supera el máximo de filas."""
    alias = alias or {}
    extension = (nombre_archivo or "").lower().rsplit(".", 1)[-1]
    if extension == "csv":
        filas = _filas_csv(contenido)
    elif extension == "xlsx":
        filas = _filas_xlsx(contenido)
    else:
        raise ArchivoInvalido("El archivo debe ser .csv o .xlsx")

    encabezados = None
    resultado = []
    for numero, valores in enumerate(filas, 1):
        if encabezados is None:
            if not any(valores):
                continue
            encabezados = [alias.get(v.strip().lower(), v.strip().lower()) for v in valores]
            faltan = [c for c in obligatorias if c not in encabezados]
            if faltan:
                raise ArchivoInvalido(f"Falta la columna obligatoria: {', '.join(faltan)}")
            continue
        if not any(valores):
            continue
        if len(resultado) >= MAX_FILAS:
            raise ArchivoInvalido(f"El archivo supera el máximo de {MAX_FILAS} filas")
        fila = {}
        for columna, valor in zip(encabezados, valores):
            if columna and columna not in fila:
                fila[columna] = valor
        resultado.append((numero, fila))
    if encabezados is None:
        raise ArchivoInvalido("El archivo está vacío")
    return resultado
