"""Consulta de la auditoría (§10, pantalla 12; RF-24).

El Administrador ve todos los registros. El Gestor, en solo lectura, ve solo los de
expedientes, postulantes y cargas masivas: no los de configuración, usuarios ni sesiones.
Los registros nunca se modifican desde aquí.
"""

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import openpyxl
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from peewee import JOIN

from app import models as m
from app.services import auditoria, tiempo

POR_PAGINA = 50
MAXIMO_EXPORTACION = 50000

ENTIDADES = {
    "expediente": "Expediente", "postulante": "Postulante", "carga_masiva": "Carga masiva",
    "proceso": "Proceso", "fase": "Fase", "flujo_alternativo": "Flujo alternativo", "carrera": "Carrera",
    "usuario": "Usuario",
}
ENTIDADES_GESTOR = ("expediente", "postulante", "carga_masiva")

ACCIONES = {
    # Sesión y usuarios
    "LOGIN": "Inicio de sesión", "LOGIN_FALLIDO": "Inicio de sesión fallido", "LOGOUT": "Cierre de sesión",
    "BLOQUEO_CUENTA": "Bloqueo de cuenta", "CAMBIO_PASSWORD": "Cambio de contraseña",
    "RESTABLECER_PASSWORD": "Restablecimiento de contraseña", "CREAR_SUPERADMIN": "Creación del super administrador",
    "CREAR_USUARIO": "Creación de usuario", "EDITAR_USUARIO": "Edición de usuario",
    "ACTIVAR_USUARIO": "Activación de usuario", "DESACTIVAR_USUARIO": "Desactivación de usuario",
    "CAMBIO_PERMISO_REGISTRO": "Cambio del permiso de carga manual",
    # Configuración
    "CREAR_PROCESO": "Creación de proceso", "EDITAR_PROCESO": "Edición de proceso",
    "ACTIVAR_PROCESO": "Activación de proceso", "CERRAR_PROCESO": "Cierre de proceso",
    "CREAR_FASE": "Creación de fase", "EDITAR_FASE": "Edición de fase", "ELIMINAR_FASE": "Eliminación de fase",
    "REORDENAR_FASE": "Cambio de orden de fase", "ASIGNAR_RESPONSABLES": "Asignación de responsables",
    "CREAR_FLUJO": "Creación de flujo alternativo", "EDITAR_FLUJO": "Edición de flujo alternativo",
    "ELIMINAR_FLUJO": "Eliminación de flujo alternativo",
    "CREAR_CARRERA": "Creación de carrera", "EDITAR_CARRERA": "Edición de carrera",
    "CAMBIAR_ESTADO_CARRERA": "Activación o desactivación de carrera", "ELIMINAR_CARRERA": "Eliminación de carrera",
    "IMPORTAR_CARRERAS": "Importación del catálogo de carreras",
    "EXPORTAR_REPORTE": "Exportación de reporte", "EXPORTAR_AUDITORIA": "Exportación de auditoría",
    # Postulantes y cargas
    "CREAR_POSTULANTE": "Registro de postulante", "EDITAR_POSTULANTE": "Edición de postulante",
    "COMPLETAR_POSTULANTE": "Datos completados en el inicio", "CAMBIAR_ESTADO_POSTULANTE": "Activación o desactivación de postulante",
    "CONSULTA_RUT_QR": "Consulta de RUT en el formulario QR", "CARGA_MASIVA": "Carga masiva",
    # Expedientes
    "INICIAR_PROCESO": "Inicio de proceso", "ATENDER": "Atender", "FINALIZAR_ATENCION": "Finalizar y derivar",
    "DERIVAR_AHORA": "Derivar ahora", "DERIVAR_A_FLUJO": "Derivación a flujo alternativo",
    "DEVOLVER_A_PRINCIPAL": "Devolución al flujo principal", "CIERRE_MANUAL": "Cierre manual de matrícula",
    "ABANDONO": "Abandonó", "REACTIVAR": "Reactivación", "CORREGIR_RUTA": "Corrección de ruta",
    "REABRIR_ATENCION": "Reapertura de atención", "ENVIAR_CORREO": "Envío de correo",
    "REENVIO_QR_LIMITADO": "Reenvío por QR rechazado por límite",
}
# Acciones sobre expedientes, postulantes y cargas: las que ve el Gestor
ACCIONES_GESTOR = (
    "CREAR_POSTULANTE", "EDITAR_POSTULANTE", "COMPLETAR_POSTULANTE", "CAMBIAR_ESTADO_POSTULANTE", "CONSULTA_RUT_QR",
    "CARGA_MASIVA", "INICIAR_PROCESO", "ATENDER", "FINALIZAR_ATENCION", "DERIVAR_AHORA", "DERIVAR_A_FLUJO",
    "DEVOLVER_A_PRINCIPAL", "CIERRE_MANUAL", "ABANDONO", "REACTIVAR", "CORREGIR_RUTA", "REABRIR_ATENCION",
    "ENVIAR_CORREO", "REENVIO_QR_LIMITADO",
)
ACTORES = {"PUBLICO": "Postulante (QR)", "SISTEMA": "Sistema"}

# Nombres legibles de los campos más frecuentes en valor_anterior y valor_nuevo
CAMPOS = {
    "rut": "RUT", "telefono": "Teléfono", "correo_personal": "Correo personal", "via": "Vía",
    "fase_actual": "Fase actual", "estado_atencion": "Estado de la atención", "observacion": "Observación",
    "detalle_cierre": "Detalle del cierre", "politica": "Política", "codigo": "Código",
    "descripcion": "Descripción", "postulante_id": "Postulante", "numero": "Número de atención",
}

def puede_ver(usuario) -> bool:
    return usuario is not None and usuario.rol in ("ADMIN", "GESTOR")


def entidades_visibles(usuario) -> tuple:
    return tuple(ENTIDADES) if usuario.rol == "ADMIN" else ENTIDADES_GESTOR


# ── Filtros ─────────────────────────────────────────────────────────

@dataclass
class Filtros:
    desde: date = None
    hasta: date = None
    usuario: str = None  # id del usuario, o PUBLICO o SISTEMA
    accion: str = None
    entidad: str = None
    entidad_id: int = None
    pagina: int = 1
    errores: list = field(default_factory=list)

    @property
    def activos(self) -> bool:
        return any((self.desde, self.hasta, self.usuario, self.accion, self.entidad, self.entidad_id))

    def como_dict(self) -> dict:
        return {"desde": self.desde and self.desde.isoformat(), "hasta": self.hasta and self.hasta.isoformat(),
                "usuario": self.usuario, "accion": self.accion, "entidad": self.entidad,
                "entidad_id": self.entidad_id}


def _fecha(valor, etiqueta, errores):
    valor = (valor or "").strip()
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        errores.append(f"La fecha «{etiqueta}» no es válida.")
        return None


def leer_filtros(args, usuario) -> Filtros:
    errores = []
    filtros = Filtros(_fecha(args.get("desde"), "desde", errores), _fecha(args.get("hasta"), "hasta", errores),
                      errores=errores)
    if filtros.desde and filtros.hasta and filtros.desde > filtros.hasta:
        errores.append("La fecha «desde» es posterior a «hasta».")
        filtros.desde = filtros.hasta = None
    quien = (args.get("usuario") or "").strip()
    if quien in ACTORES or quien.isdigit():
        filtros.usuario = quien
    if args.get("accion") in ACCIONES:
        filtros.accion = args["accion"]
    if args.get("entidad") in entidades_visibles(usuario):
        filtros.entidad = args["entidad"]
    entidad_id = (args.get("entidad_id") or "").strip()
    if entidad_id:
        if entidad_id.isdigit() and len(entidad_id) < 10:
            filtros.entidad_id = int(entidad_id)
        else:
            errores.append("El identificador debe ser un número.")
    pagina = args.get("pagina") or ""
    filtros.pagina = max(1, int(pagina)) if pagina.isdigit() and len(pagina) < 7 else 1
    return filtros


def _inicio_del_dia(dia):
    return datetime.combine(dia, time.min, tzinfo=tiempo.ZONA)


def _consulta(filtros: Filtros, usuario):
    a = m.Auditoria
    consulta = (a.select(a, m.Usuario.nombre.alias("nombre_usuario"))
                .join(m.Usuario, JOIN.LEFT_OUTER, on=(a.usuario == m.Usuario.id))
                .where(a.entidad.in_(entidades_visibles(usuario))))
    if filtros.desde:
        consulta = consulta.where(a.fecha_hora >= _inicio_del_dia(filtros.desde))
    if filtros.hasta:
        consulta = consulta.where(a.fecha_hora < _inicio_del_dia(filtros.hasta + timedelta(days=1)))
    if filtros.usuario in ACTORES:
        consulta = consulta.where(a.usuario.is_null() & (a.actor == filtros.usuario))
    elif filtros.usuario:
        consulta = consulta.where(a.usuario == int(filtros.usuario))
    if filtros.accion:
        consulta = consulta.where(a.accion == filtros.accion)
    if filtros.entidad:
        consulta = consulta.where(a.entidad == filtros.entidad)
    if filtros.entidad_id is not None:
        consulta = consulta.where(a.entidad_id == filtros.entidad_id)
    return consulta.order_by(a.fecha_hora.desc(), a.id.desc())


# ── Presentación ────────────────────────────────────────────────────

# Códigos de estado que se muestran con su nombre
VALORES = {
    "PLANIFICADA": "Planificada", "EN_ESPERA": "En espera", "EN_ATENCION": "En atención",
    "COMPLETADO": "Completado", "COMPLETADO_CON_OBSERVACIONES": "Completado con observaciones",
    "DERIVADA": "Derivada", "ABANDONADA": "Abandonó", "EN_CURSO": "En curso", "ABANDONO": "Abandonó",
    "MATRICULADO": "Matriculado", "PROCESO_PENDIENTE": "Proceso pendiente", "ENVIADO": "Enviado", "ERROR": "Error",
}


def _texto_valor(valor) -> str:
    if isinstance(valor, str) and valor in VALORES:
        return VALORES[valor]
    if valor is None:
        return "—"
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    if isinstance(valor, list) and all(not isinstance(v, (dict, list)) for v in valor):
        return ", ".join(str(v) for v in valor) or "(vacío)"
    if isinstance(valor, (dict, list)):
        return json.dumps(valor, ensure_ascii=False)
    return str(valor) if valor != "" else "(vacío)"


def _campos(anterior, nuevo) -> list:
    """Cada campo con su valor anterior y nuevo; primero los que cambiaron."""
    anterior = anterior if isinstance(anterior, dict) else ({"valor": anterior} if anterior is not None else {})
    nuevo = nuevo if isinstance(nuevo, dict) else ({"valor": nuevo} if nuevo is not None else {})
    claves = list(anterior) + [k for k in nuevo if k not in anterior]
    con_ambos = bool(anterior) and bool(nuevo)
    campos = [{
        "campo": CAMPOS.get(clave, clave.replace("_", " ").capitalize()),
        "anterior": _texto_valor(anterior.get(clave)) if clave in anterior else "",
        "nuevo": _texto_valor(nuevo.get(clave)) if clave in nuevo else "",
        "cambio": con_ambos and anterior.get(clave) != nuevo.get(clave),
    } for clave in claves]
    return sorted(campos, key=lambda c: not c["cambio"])


def _quien(registro) -> str:
    if registro.usuario_id:
        return registro.nombre_usuario
    return ACTORES.get(registro.actor, registro.actor)


def _fichas(registros) -> dict:
    """Postulante de cada entidad, para enlazar a su ficha: {(entidad, id): postulante_id}."""
    expedientes = {r.entidad_id for r in registros if r.entidad == "expediente" and r.entidad_id}
    postulantes = {r.entidad_id for r in registros if r.entidad == "postulante" and r.entidad_id}
    fichas = {}
    if expedientes:
        for e in m.Expediente.select(m.Expediente.id, m.Expediente.postulante).where(m.Expediente.id.in_(expedientes)):
            fichas[("expediente", e.id)] = e.postulante_id
    if postulantes:
        for p in m.Postulante.select(m.Postulante.id).where(m.Postulante.id.in_(postulantes)):
            fichas[("postulante", p.id)] = p.id
    return fichas


def _fila(registro, fichas) -> dict:
    return {
        "id": registro.id,
        "fecha": tiempo.fecha_hora_local(registro.fecha_hora),
        "quien": _quien(registro),
        "rol": registro.rol,
        "accion": ACCIONES.get(registro.accion, registro.accion),
        "codigo_accion": registro.accion,
        "entidad": ENTIDADES.get(registro.entidad, registro.entidad),
        "entidad_id": registro.entidad_id,
        "ficha": fichas.get((registro.entidad, registro.entidad_id)),
        "campos": _campos(registro.valor_anterior, registro.valor_nuevo),
        "ip": registro.ip,
        "user_agent": registro.user_agent,
    }


def buscar(filtros: Filtros, usuario) -> dict:
    consulta = _consulta(filtros, usuario)
    total = consulta.count()
    paginas = max(1, -(-total // POR_PAGINA))
    pagina = min(filtros.pagina, paginas)
    registros = list(consulta.paginate(pagina, POR_PAGINA).objects())
    fichas = _fichas(registros)
    return {"filas": [_fila(r, fichas) for r in registros], "total": total, "pagina": pagina, "paginas": paginas}


def opciones(usuario) -> dict:
    """Valores de los filtros que el usuario puede usar."""
    visibles = entidades_visibles(usuario)
    codigos = ACCIONES if usuario.rol == "ADMIN" else ACCIONES_GESTOR
    return {
        "usuarios": list(m.Usuario.select(m.Usuario.id, m.Usuario.nombre).order_by(m.Usuario.nombre)),
        "acciones": sorted(((c, ACCIONES[c]) for c in codigos), key=lambda par: par[1]),
        "entidades": [(c, ENTIDADES[c]) for c in visibles],
    }


# ── Exportación ─────────────────────────────────────────────────────

_COLUMNAS = ["Fecha", "Usuario", "Rol", "Acción", "Entidad", "Id", "Valor anterior", "Valor nuevo", "IP"]
_PELIGROSOS = ("=", "+", "-", "@", "\t", "\r")


def _celda(valor):
    if valor is None or isinstance(valor, int):
        return valor
    texto = str(valor)
    return "'" + texto if texto.startswith(_PELIGROSOS) else texto


def exportar(filtros: Filtros, formato, usuario) -> tuple[bytes, str, str]:
    """(contenido, tipo MIME, nombre). Hasta MAXIMO_EXPORTACION registros; queda auditado."""
    registros = _consulta(filtros, usuario).limit(MAXIMO_EXPORTACION).objects()
    filas = [[tiempo.fecha_hora_local(r.fecha_hora), _quien(r), r.rol or "", ACCIONES.get(r.accion, r.accion),
              ENTIDADES.get(r.entidad, r.entidad), r.entidad_id,
              json.dumps(r.valor_anterior, ensure_ascii=False) if r.valor_anterior is not None else "",
              json.dumps(r.valor_nuevo, ensure_ascii=False) if r.valor_nuevo is not None else "", r.ip or ""]
             for r in registros]
    nombre = f"auditoria_{datetime.now(tiempo.ZONA):%Y%m%d_%H%M}"
    if formato == "csv":
        salida = io.StringIO()
        salida.write("﻿")  # para que Excel lo abra en UTF-8
        escritor = csv.writer(salida, delimiter=";")
        escritor.writerow(_COLUMNAS)
        escritor.writerows([_celda(c) for c in fila] for fila in filas)
        contenido, tipo, nombre = salida.getvalue().encode("utf-8"), "text/csv", nombre + ".csv"
    else:
        libro = openpyxl.Workbook(write_only=True)  # mucho más rápido con decenas de miles de filas
        hoja = libro.create_sheet("Auditoría")
        for letra, ancho in zip("ABCDEFGHI", (17, 24, 14, 32, 16, 8, 50, 50, 16)):
            hoja.column_dimensions[letra].width = ancho
        hoja.freeze_panes = "A2"
        encabezado = []
        for columna in _COLUMNAS:
            celda = WriteOnlyCell(hoja, value=columna)
            celda.font = Font(bold=True)
            encabezado.append(celda)
        hoja.append(encabezado)
        for fila in filas:
            hoja.append([_celda(c) for c in fila])
        salida = io.BytesIO()
        libro.save(salida)
        contenido = salida.getvalue()
        tipo, nombre = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", nombre + ".xlsx"
    auditoria.registrar("EXPORTAR_AUDITORIA", "usuario", usuario.id, usuario=usuario,
                        nuevo={"formato": formato, "registros": len(filas), "filtros": filtros.como_dict()})
    return contenido, tipo, nombre
