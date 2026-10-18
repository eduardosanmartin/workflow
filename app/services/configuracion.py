"""Configuración del Administrador: procesos, fases, flujos alternativos, responsables y
usuarios (RF-01 a RF-05, RF-23, RF-29). Cada cambio queda auditado (RF-24).

Las funciones que modifican reciben `por`, el usuario que actúa, para la auditoría.
Los errores de regla se informan con `ReglaVioladaError` (mensaje listo para mostrar).
"""

import re
import secrets
from datetime import date

from peewee import fn

from app import models as m
from app.db import db
from app.security import hash_password
from app.services import auditoria, recorridos, retencion
from app.services import workflow as wf

ROLES = ("ADMIN", "GESTOR", "USUARIO_FASE")
_IDENTIFICADOR = re.compile(r"^[A-Z0-9_]{1,20}$")
_PREFIJO = re.compile(r"^[A-Z0-9]{1,10}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ALFABETO_CLAVE = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class ConfiguracionInvalidaError(wf.ReglaVioladaError):
    """Varios errores a la vez (por ejemplo, al activar un proceso)."""

    def __init__(self, errores):
        super().__init__("; ".join(errores), 409)
        self.errores = list(errores)


def _invalido(mensaje):
    return wf.ReglaVioladaError(mensaje, 422)


def _obtener(modelo, id_, nombre):
    objeto = modelo.get_or_none(modelo.id == id_)
    if objeto is None:
        raise wf.ReglaVioladaError(f"{nombre} no existe", 404)
    return objeto


def _texto(datos, campo, etiqueta, maximo, obligatorio=True):
    valor = (datos.get(campo) or "").strip()
    if obligatorio and not valor:
        raise _invalido(f"{etiqueta} es obligatorio")
    if len(valor) > maximo:
        raise _invalido(f"{etiqueta} admite hasta {maximo} caracteres")
    return valor


def _fecha(valor, etiqueta):
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat((valor or "").strip())
    except ValueError:
        raise _invalido(f"{etiqueta} no es una fecha válida") from None


def _bool(valor):
    if isinstance(valor, bool):
        return valor
    return str(valor or "").strip().lower() in ("1", "true", "on", "si", "sí", "yes")


def _foto(objeto, campos):
    salida = {}
    for campo in campos:
        valor = getattr(objeto, campo)
        salida[campo] = valor.isoformat() if isinstance(valor, date) else valor
    return salida


# ── Procesos (RF-01) ────────────────────────────────────────────────

_CAMPOS_PROCESO = (
    "codigo", "nombre", "descripcion", "fecha_inicio", "fecha_fin", "prefijo_atencion",
    "enmascarar_datos_qr", "fecha_eliminacion",
)
# No se pueden cambiar una vez iniciado: identifican el proceso y sus números de atención
_BLOQUEADOS_AL_INICIAR = ("codigo", "prefijo_atencion", "fecha_inicio")


def proceso_iniciado(proceso_id) -> bool:
    return m.Expediente.select().where(m.Expediente.proceso == proceso_id).exists()


def fecha_eliminacion_por_defecto(fecha_inicio: date) -> date:
    return date(fecha_inicio.year, 11, 30)  # RF-26


def _validar_proceso(datos, proceso_id=None):
    codigo = _texto(datos, "codigo", "El código", 30).upper()
    nombre = _texto(datos, "nombre", "El nombre", 200)
    descripcion = (datos.get("descripcion") or "").strip()
    inicio = _fecha(datos.get("fecha_inicio"), "La fecha de inicio")
    fin = _fecha(datos.get("fecha_fin"), "La fecha de término")
    prefijo = _texto(datos, "prefijo_atencion", "El prefijo de atención", 10).upper()
    if not _PREFIJO.match(prefijo):
        raise _invalido("El prefijo de atención solo admite letras y números, sin espacios")
    if fin < inicio:
        raise _invalido("La fecha de término no puede ser anterior a la de inicio")
    eliminacion = datos.get("fecha_eliminacion")
    eliminacion = (
        _fecha(eliminacion, "La fecha de eliminación") if eliminacion
        else fecha_eliminacion_por_defecto(inicio)
    )
    if eliminacion < fin:
        raise _invalido("La fecha de eliminación de datos no puede ser anterior a la de término")
    repetido = m.Proceso.select().where(fn.upper(m.Proceso.codigo) == codigo)
    if proceso_id is not None:
        repetido = repetido.where(m.Proceso.id != proceso_id)
    if repetido.exists():
        raise _invalido(f"Ya existe un proceso con el código {codigo}")
    return {
        "codigo": codigo, "nombre": nombre, "descripcion": descripcion, "fecha_inicio": inicio,
        "fecha_fin": fin, "prefijo_atencion": prefijo,
        "enmascarar_datos_qr": _bool(datos.get("enmascarar_datos_qr")),
        "fecha_eliminacion": eliminacion,
    }


def crear_proceso(datos, por):
    """Crea el proceso en BORRADOR con sus dos fases de sistema (RF-03)."""
    with db.atomic():
        valores = _validar_proceso(datos)
        proceso = m.Proceso.create(estado="BORRADOR", **valores)
        m.Fase.create(proceso=proceso, identificador="INI", nombre="Iniciado", tipo=wf.INICIAL,
                      orden=1, mostrar_en_qr=False)
        m.Fase.create(proceso=proceso, identificador="MNP", nombre="MNP", tipo=wf.CIERRE_DIGITAL,
                      orden=2, mostrar_en_qr=False,
                      descripcion="Matrícula No Presencial (cierre digital)")
        auditoria.registrar("CREAR_PROCESO", "proceso", proceso.id,
                            nuevo=_foto(proceso, _CAMPOS_PROCESO), usuario=por)
    return proceso


def _exigir_no_cerrado(proceso):
    if proceso.estado == "CERRADO":
        raise wf.ReglaVioladaError("El proceso está cerrado: su configuración es de solo lectura")


def editar_proceso(proceso_id, datos, por):
    with db.atomic():
        proceso = _obtener(m.Proceso, proceso_id, "El proceso")
        _exigir_no_cerrado(proceso)
        anterior = _foto(proceso, _CAMPOS_PROCESO)
        valores = _validar_proceso({**anterior, **datos}, proceso.id)
        if proceso_iniciado(proceso.id):
            for campo in _BLOQUEADOS_AL_INICIAR:
                if valores[campo] != getattr(proceso, campo):
                    raise wf.ReglaVioladaError(
                        "El proceso ya tiene expedientes: no se puede cambiar su código, "
                        "su prefijo de atención ni su fecha de inicio"
                    )
        for campo, valor in valores.items():
            setattr(proceso, campo, valor)
        proceso.save()
        nuevo = _foto(proceso, _CAMPOS_PROCESO)
        if nuevo != anterior:
            auditoria.registrar("EDITAR_PROCESO", "proceso", proceso.id,
                                anterior=anterior, nuevo=nuevo, usuario=por)
    return proceso


def fases_con_responsable(proceso_id) -> set:
    """Fases del proceso con al menos un responsable activo."""
    filas = (
        m.FaseResponsable.select(m.FaseResponsable.fase)
        .join(m.Usuario).switch(m.FaseResponsable).join(m.Fase)
        .where((m.Fase.proceso == proceso_id) & (m.Usuario.activo == True))  # noqa: E712
        .distinct()
    )
    return {f.fase_id for f in filas}


def errores_de_activacion(proceso_id) -> list:
    estructura = recorridos.estructura_del_proceso(proceso_id)
    solo_activos = wf.Estructura(estructura.fases, [f for f in estructura.flujos if f.activo])
    return wf.validar_activacion(solo_activos, fases_con_responsable(proceso_id))


def activar_proceso(proceso_id, por):
    with db.atomic():
        proceso = m.Proceso.select().where(m.Proceso.id == proceso_id).for_update().first()
        if proceso is None:
            raise wf.ReglaVioladaError("El proceso no existe", 404)
        if proceso.estado != "BORRADOR":
            raise wf.ReglaVioladaError("Solo se puede activar un proceso en borrador")
        activo = m.Proceso.get_or_none(m.Proceso.estado == "ACTIVO")
        if activo is not None:
            raise wf.ReglaVioladaError(
                f"Ya hay un proceso activo («{activo.nombre}»). Ciérrelo antes de activar otro."
            )
        errores = errores_de_activacion(proceso.id)
        if errores:
            raise ConfiguracionInvalidaError(errores)
        proceso.estado = "ACTIVO"
        proceso.save()
        auditoria.registrar("ACTIVAR_PROCESO", "proceso", proceso.id,
                            anterior={"estado": "BORRADOR"}, nuevo={"estado": "ACTIVO"}, usuario=por)
    return proceso


def _por_cerrar(proceso_id):
    return m.Expediente.select().where(
        (m.Expediente.proceso == proceso_id)
        & (m.Expediente.estado.in_((wf.EN_CURSO, wf.ABANDONO)))
    )


def pendientes_al_cerrar(proceso_id) -> int:
    """Cuántos expedientes quedarían como "Proceso pendiente" (para la confirmación)."""
    return _por_cerrar(proceso_id).count()


def cerrar_proceso(proceso_id, por):
    """RF-01: los expedientes no matriculados quedan como "Proceso pendiente" y todos se cierran."""
    with db.atomic():
        proceso = m.Proceso.select().where(m.Proceso.id == proceso_id).for_update().first()
        if proceso is None:
            raise wf.ReglaVioladaError("El proceso no existe", 404)
        if proceso.estado != "ACTIVO":
            raise wf.ReglaVioladaError("Solo se puede cerrar un proceso activo")
        pendientes = 0
        for fila in _por_cerrar(proceso.id).order_by(m.Expediente.id):
            expediente, recorrido = recorridos.cargar(fila.id)
            recorrido.cerrar_proceso()
            recorridos.guardar(expediente, recorrido)
            pendientes += 1
        cierre = m.ahora()
        (m.Expediente.update(cerrado_en=cierre)
         .where((m.Expediente.proceso == proceso.id) & (m.Expediente.cerrado_en.is_null()))
         .execute())
        proceso.estado = "CERRADO"
        proceso.fecha_cierre = cierre
        proceso.save()
        retencion.calcular_resumen(proceso.id, cierre)  # RF-26: totales que sobreviven a la eliminación
        auditoria.registrar("CERRAR_PROCESO", "proceso", proceso.id,
                            anterior={"estado": "ACTIVO"},
                            nuevo={"estado": "CERRADO", "proceso_pendiente": pendientes}, usuario=por)
    return pendientes


# ── Fases (RF-02, RF-03, RF-05) ─────────────────────────────────────

_CAMPOS_FASE = ("identificador", "nombre", "descripcion", "tipo", "orden", "mostrar_en_qr")


def _exigir_estructura_libre(proceso):
    _exigir_no_cerrado(proceso)
    wf.validar_cambio_estructura(proceso_iniciado(proceso.id))


def _validar_fase(datos, proceso_id, fase_id=None):
    identificador = _texto(datos, "identificador", "El identificador", 20).upper()
    if not _IDENTIFICADOR.match(identificador):
        raise _invalido("El identificador solo admite letras, números y guion bajo, sin espacios")
    repetido = m.Fase.select().where(
        (m.Fase.proceso == proceso_id) & (m.Fase.identificador == identificador)
    )
    if fase_id is not None:
        repetido = repetido.where(m.Fase.id != fase_id)
    if repetido.exists():
        raise _invalido(f"Ya existe una fase con el identificador {identificador} en este proceso")
    return {
        "identificador": identificador,
        "nombre": _texto(datos, "nombre", "El nombre", 100),
        "descripcion": (datos.get("descripcion") or "").strip(),
        "mostrar_en_qr": _bool(datos.get("mostrar_en_qr")),
    }


def agregar_fase(proceso_id, datos, por):
    """Agrega una fase de consulta, que se presenta antes de MNP."""
    with db.atomic():
        proceso = _obtener(m.Proceso, proceso_id, "El proceso")
        _exigir_estructura_libre(proceso)
        valores = _validar_fase(datos, proceso.id)
        mnp = m.Fase.get_or_none((m.Fase.proceso == proceso.id) & (m.Fase.tipo == wf.CIERRE_DIGITAL))
        if mnp is not None:
            orden = mnp.orden
            mnp.orden += 1
            mnp.save()
        else:
            orden = (m.Fase.select(fn.MAX(m.Fase.orden)).where(m.Fase.proceso == proceso.id).scalar() or 0) + 1
        fase = m.Fase.create(proceso=proceso, tipo=wf.CONSULTA, orden=orden, **valores)
        auditoria.registrar("CREAR_FASE", "fase", fase.id, nuevo=_foto(fase, _CAMPOS_FASE), usuario=por)
    return fase


def editar_fase(fase_id, datos, por):
    """Nombre, descripción y `mostrar_en_qr` siempre; el identificador solo sin expedientes."""
    with db.atomic():
        fase = _obtener(m.Fase, fase_id, "La fase")
        proceso = fase.proceso
        _exigir_no_cerrado(proceso)
        anterior = _foto(fase, _CAMPOS_FASE)
        valores = _validar_fase({**anterior, **datos}, proceso.id, fase.id)
        if valores["identificador"] != fase.identificador and proceso_iniciado(proceso.id):
            raise wf.ReglaVioladaError(
                "El proceso ya tiene expedientes: no se puede cambiar el identificador de la fase"
            )
        if fase.tipo != wf.CONSULTA:
            valores["mostrar_en_qr"] = False  # solo las consultas se ofrecen en el QR (RF-10)
        for campo, valor in valores.items():
            setattr(fase, campo, valor)
        fase.save()
        nuevo = _foto(fase, _CAMPOS_FASE)
        if nuevo != anterior:
            auditoria.registrar("EDITAR_FASE", "fase", fase.id, anterior=anterior, nuevo=nuevo, usuario=por)
    return fase


def eliminar_fase(fase_id, por):
    with db.atomic():
        fase = _obtener(m.Fase, fase_id, "La fase")
        if fase.tipo != wf.CONSULTA:
            raise wf.ReglaVioladaError("Las fases de sistema (inicial y MNP) no se pueden eliminar")
        _exigir_estructura_libre(fase.proceso)
        flujos = [
            f.nombre for f in m.FlujoAlternativo.select().join(m.FlujoAlternativoFase)
            .where(m.FlujoAlternativoFase.fase == fase.id).order_by(m.FlujoAlternativo.nombre)
        ]
        if flujos:
            raise wf.ReglaVioladaError(
                f"La fase «{fase.nombre}» se usa en los flujos alternativos: {', '.join(flujos)}. "
                "Quítela de ellos antes de eliminarla."
            )
        anterior = _foto(fase, _CAMPOS_FASE)
        usuarios = [r.usuario_id for r in m.FaseResponsable.select().where(m.FaseResponsable.fase == fase.id)]
        fase.delete_instance()  # elimina también sus responsables (CASCADE)
        _exigir_usuarios_con_fase(usuarios)
        auditoria.registrar("ELIMINAR_FASE", "fase", fase_id, anterior=anterior, usuario=por)


def mover_fase(fase_id, direccion, por):
    """Sube (-1) o baja (+1) una fase de consulta en el orden de presentación. Inicial y MNP
    quedan siempre al principio y al final. Se permite con el proceso iniciado (RF-05)."""
    with db.atomic():
        fase = _obtener(m.Fase, fase_id, "La fase")
        _exigir_no_cerrado(fase.proceso)
        if fase.tipo != wf.CONSULTA:
            raise wf.ReglaVioladaError("Las fases de sistema no se reordenan")
        vecinas = m.Fase.select().where((m.Fase.proceso == fase.proceso_id) & (m.Fase.tipo == wf.CONSULTA))
        if direccion < 0:
            vecina = vecinas.where(m.Fase.orden < fase.orden).order_by(m.Fase.orden.desc()).first()
        else:
            vecina = vecinas.where(m.Fase.orden > fase.orden).order_by(m.Fase.orden).first()
        if vecina is None:
            return fase
        fase.orden, vecina.orden = vecina.orden, fase.orden
        fase.save()
        vecina.save()
        auditoria.registrar("REORDENAR_FASE", "fase", fase.id,
                            anterior={"orden": vecina.orden}, nuevo={"orden": fase.orden}, usuario=por)
    return fase


# ── Flujos alternativos (RF-29) ─────────────────────────────────────

def fases_del_flujo(flujo_id) -> list:
    return [
        f.fase_id for f in m.FlujoAlternativoFase.select()
        .where(m.FlujoAlternativoFase.flujo_alternativo == flujo_id)
        .order_by(m.FlujoAlternativoFase.posicion)
    ]


def _validar_flujo(datos, proceso_id, flujo_id=None):
    codigo = _texto(datos, "codigo", "El código", 20).upper()
    if not _IDENTIFICADOR.match(codigo):
        raise _invalido("El código solo admite letras, números y guion bajo, sin espacios")
    repetido = m.FlujoAlternativo.select().where(
        (m.FlujoAlternativo.proceso == proceso_id) & (m.FlujoAlternativo.codigo == codigo)
    )
    if flujo_id is not None:
        repetido = repetido.where(m.FlujoAlternativo.id != flujo_id)
    if repetido.exists():
        raise _invalido(f"Ya existe un flujo alternativo con el código {codigo} en este proceso")
    nombre = _texto(datos, "nombre", "El nombre", 100)
    try:
        fases = [int(f) for f in datos.get("fases") or []]
    except (TypeError, ValueError):
        raise _invalido("Fases inválidas") from None
    estructura = recorridos.estructura_del_proceso(proceso_id)
    errores = wf.validar_flujo(estructura, wf.FlujoAlternativo(flujo_id, nombre, tuple(fases)))
    if errores:
        raise ConfiguracionInvalidaError(errores)
    return {
        "codigo": codigo, "nombre": nombre, "descripcion": (datos.get("descripcion") or "").strip(),
        "activo": _bool(datos.get("activo", True)), "fases": fases,
    }


def _foto_flujo(flujo):
    return {**_foto(flujo, ("codigo", "nombre", "descripcion", "activo")), "fases": fases_del_flujo(flujo.id)}


def _guardar_secuencia(flujo, fases):
    m.FlujoAlternativoFase.delete().where(m.FlujoAlternativoFase.flujo_alternativo == flujo.id).execute()
    for posicion, fase_id in enumerate(fases, 1):
        m.FlujoAlternativoFase.insert(flujo_alternativo=flujo.id, posicion=posicion, fase=fase_id).execute()


def crear_flujo(proceso_id, datos, por):
    with db.atomic():
        proceso = _obtener(m.Proceso, proceso_id, "El proceso")
        _exigir_estructura_libre(proceso)
        valores = _validar_flujo(datos, proceso.id)
        fases = valores.pop("fases")
        flujo = m.FlujoAlternativo.create(proceso=proceso, **valores)
        _guardar_secuencia(flujo, fases)
        auditoria.registrar("CREAR_FLUJO", "flujo_alternativo", flujo.id, nuevo=_foto_flujo(flujo), usuario=por)
    return flujo


def editar_flujo(flujo_id, datos, por):
    """Nombre, descripción y activo siempre; código y secuencia solo sin expedientes (RF-05).
    Un flujo inactivo conserva sus expedientes, pero no admite nuevas entradas."""
    with db.atomic():
        flujo = _obtener(m.FlujoAlternativo, flujo_id, "El flujo alternativo")
        proceso = flujo.proceso
        _exigir_no_cerrado(proceso)
        anterior = _foto_flujo(flujo)
        valores = _validar_flujo({**anterior, **datos}, proceso.id, flujo.id)
        fases = valores.pop("fases")
        estructura_cambia = valores["codigo"] != flujo.codigo or fases != anterior["fases"]
        if estructura_cambia and proceso_iniciado(proceso.id):
            raise wf.ReglaVioladaError(
                "El proceso ya tiene expedientes: no se puede cambiar el código ni las fases "
                "del flujo alternativo"
            )
        for campo, valor in valores.items():
            setattr(flujo, campo, valor)
        flujo.save()
        if fases != anterior["fases"]:
            db.execute_sql("SET CONSTRAINTS flujo_alternativo_fase_pk DEFERRED")
            _guardar_secuencia(flujo, fases)
        nuevo = _foto_flujo(flujo)
        if nuevo != anterior:
            auditoria.registrar("EDITAR_FLUJO", "flujo_alternativo", flujo.id,
                                anterior=anterior, nuevo=nuevo, usuario=por)
    return flujo


def eliminar_flujo(flujo_id, por):
    with db.atomic():
        flujo = _obtener(m.FlujoAlternativo, flujo_id, "El flujo alternativo")
        _exigir_estructura_libre(flujo.proceso)
        anterior = _foto_flujo(flujo)
        flujo.delete_instance()
        auditoria.registrar("ELIMINAR_FLUJO", "flujo_alternativo", flujo_id, anterior=anterior, usuario=por)


# ── Responsables (RF-04, §4) ────────────────────────────────────────

def responsables_de(fase_id) -> set:
    return {r.usuario_id for r in m.FaseResponsable.select().where(m.FaseResponsable.fase == fase_id)}


def _exigir_responsables(fase_ids):
    """Cada fase de un proceso ACTIVO conserva al menos un responsable activo (RF-04)."""
    if not fase_ids:
        return
    fases = m.Fase.select().join(m.Proceso).where(
        (m.Fase.id.in_(list(fase_ids))) & (m.Proceso.estado == "ACTIVO")
    )
    for fase in fases:
        if fase.id not in fases_con_responsable(fase.proceso_id):
            raise wf.ReglaVioladaError(
                f"La fase «{fase.nombre}» quedaría sin responsables y el proceso está activo. "
                "Asigne otro responsable antes."
            )


def _exigir_usuarios_con_fase(usuario_ids):
    """Todo Usuario de fase activo conserva al menos una fase asignada (§4)."""
    if not usuario_ids:
        return
    sin_fase = (
        m.Usuario.select()
        .where(
            (m.Usuario.id.in_(list(usuario_ids)))
            & (m.Usuario.rol == "USUARIO_FASE")
            & (m.Usuario.activo == True)  # noqa: E712
            & ~fn.EXISTS(m.FaseResponsable.select().where(m.FaseResponsable.usuario == m.Usuario.id))
        )
        .order_by(m.Usuario.nombre)
    )
    nombres = [u.nombre for u in sin_fase]
    if nombres:
        raise wf.ReglaVioladaError(
            f"Todo usuario de fase debe tener al menos una fase. Quedarían sin fases: {', '.join(nombres)}. "
            "Asígneles otra fase antes."
        )


def asignar_responsables(fase_id, usuario_ids, por):
    """Define los responsables activos de una fase. Las asignaciones de usuarios desactivados
    no se tocan desde aquí."""
    with db.atomic():
        fase = _obtener(m.Fase, fase_id, "La fase")
        _exigir_no_cerrado(fase.proceso)
        deseados = {int(u) for u in usuario_ids}
        activos = {u.id for u in m.Usuario.select().where(m.Usuario.activo == True)}  # noqa: E712
        if not deseados <= activos:
            raise _invalido("Solo se pueden asignar usuarios activos")
        actuales = responsables_de(fase.id)
        agregar = deseados - actuales
        quitar = (actuales & activos) - deseados
        if not agregar and not quitar:
            return
        for usuario_id in agregar:
            m.FaseResponsable.insert(fase=fase.id, usuario=usuario_id).execute()
        if quitar:
            m.FaseResponsable.delete().where(
                (m.FaseResponsable.fase == fase.id) & (m.FaseResponsable.usuario.in_(list(quitar)))
            ).execute()
        _exigir_responsables({fase.id})
        _exigir_usuarios_con_fase(quitar)
        auditoria.registrar("ASIGNAR_RESPONSABLES", "fase", fase.id,
                            anterior={"usuarios": sorted(actuales)},
                            nuevo={"usuarios": sorted(responsables_de(fase.id))}, usuario=por)


# ── Usuarios (RF-22, RF-23) ─────────────────────────────────────────

_CAMPOS_USUARIO = ("email", "nombre", "rol", "puede_registrar_postulantes", "activo")


def clave_temporal() -> str:
    return "".join(secrets.choice(_ALFABETO_CLAVE) for _ in range(12))


def fases_abiertas_de(usuario_id) -> set:
    """Fases asignadas en procesos no cerrados (las que se editan desde la ficha del usuario)."""
    filas = (
        m.FaseResponsable.select(m.FaseResponsable.fase).join(m.Fase).join(m.Proceso)
        .where((m.FaseResponsable.usuario == usuario_id) & (m.Proceso.estado != "CERRADO"))
    )
    return {f.fase_id for f in filas}


def _validar_usuario(datos, usuario_id=None):
    email = _texto(datos, "email", "El correo", 254).lower()
    if not _EMAIL.match(email):
        raise _invalido("El correo no tiene un formato válido")
    repetido = m.Usuario.select().where(fn.lower(m.Usuario.email) == email)
    if usuario_id is not None:
        repetido = repetido.where(m.Usuario.id != usuario_id)
    if repetido.exists():
        raise _invalido(f"Ya existe un usuario con el correo {email}")
    rol = datos.get("rol")
    if rol not in ROLES:
        raise _invalido("Rol inválido")
    try:
        fases = {int(f) for f in datos.get("fases") or []}
    except (TypeError, ValueError):
        raise _invalido("Fases inválidas") from None
    abiertas = {
        f.id for f in m.Fase.select().join(m.Proceso)
        .where((m.Fase.id.in_(list(fases))) & (m.Proceso.estado != "CERRADO"))
    } if fases else set()
    if fases != abiertas:
        raise _invalido("Solo se pueden asignar fases de procesos no cerrados")
    return {
        "email": email,
        "nombre": _texto(datos, "nombre", "El nombre", 200),
        "rol": rol,
        # RF-23: el permiso de carga manual solo aplica al Usuario de fase
        "puede_registrar_postulantes": rol == "USUARIO_FASE" and _bool(datos.get("puede_registrar_postulantes")),
        "fases": fases,
    }


def _foto_usuario(usuario):
    return {**_foto(usuario, _CAMPOS_USUARIO), "fases": sorted(fases_abiertas_de(usuario.id))}


def _sincronizar_fases(usuario, deseadas):
    actuales = fases_abiertas_de(usuario.id)
    for fase_id in deseadas - actuales:
        m.FaseResponsable.insert(fase=fase_id, usuario=usuario.id).execute()
    quitar = actuales - deseadas
    if quitar:
        m.FaseResponsable.delete().where(
            (m.FaseResponsable.usuario == usuario.id) & (m.FaseResponsable.fase.in_(list(quitar)))
        ).execute()
    return quitar


def _exigir_otro_admin(usuario):
    otros = m.Usuario.select().where(
        (m.Usuario.rol == "ADMIN") & (m.Usuario.activo == True) & (m.Usuario.id != usuario.id)  # noqa: E712
    )
    if not otros.exists():
        raise wf.ReglaVioladaError("Debe quedar al menos un Administrador activo")


def crear_usuario(datos, por):
    """Devuelve (usuario, clave_temporal). La clave se muestra una sola vez al Administrador."""
    with db.atomic():
        valores = _validar_usuario(datos)
        fases = valores.pop("fases")
        clave = clave_temporal()
        usuario = m.Usuario.create(password_hash=hash_password(clave), must_change_password=True, **valores)
        _sincronizar_fases(usuario, fases)
        _exigir_usuarios_con_fase({usuario.id})
        auditoria.registrar("CREAR_USUARIO", "usuario", usuario.id, nuevo=_foto_usuario(usuario), usuario=por)
    return usuario, clave


def editar_usuario(usuario_id, datos, por):
    with db.atomic():
        usuario = _obtener(m.Usuario, usuario_id, "El usuario")
        anterior = _foto_usuario(usuario)
        valores = _validar_usuario({**anterior, **datos}, usuario.id)
        fases = valores.pop("fases")
        if usuario.rol == "ADMIN" and valores["rol"] != "ADMIN" and usuario.activo:
            _exigir_otro_admin(usuario)
        for campo, valor in valores.items():
            setattr(usuario, campo, valor)
        usuario.save()
        quitadas = _sincronizar_fases(usuario, fases)
        _exigir_responsables(quitadas)
        _exigir_usuarios_con_fase({usuario.id})
        nuevo = _foto_usuario(usuario)
        if nuevo != anterior:
            auditoria.registrar("EDITAR_USUARIO", "usuario", usuario.id,
                                anterior=anterior, nuevo=nuevo, usuario=por)
        if nuevo["puede_registrar_postulantes"] != anterior["puede_registrar_postulantes"]:
            auditoria.registrar("CAMBIO_PERMISO_REGISTRO", "usuario", usuario.id,
                                anterior={"puede_registrar_postulantes": anterior["puede_registrar_postulantes"]},
                                nuevo={"puede_registrar_postulantes": nuevo["puede_registrar_postulantes"]},
                                usuario=por)
    return usuario


def cambiar_estado_usuario(usuario_id, activo, por):
    """Los usuarios no se eliminan, solo se desactivan (RF-23)."""
    with db.atomic():
        usuario = _obtener(m.Usuario, usuario_id, "El usuario")
        if usuario.activo == activo:
            return usuario
        if not activo:
            if usuario.id == por.id:
                raise wf.ReglaVioladaError("No puede desactivar su propia cuenta")
            if usuario.rol == "ADMIN":
                _exigir_otro_admin(usuario)
        usuario.activo = activo
        usuario.save()
        if activo:
            _exigir_usuarios_con_fase({usuario.id})
        else:
            _exigir_responsables({f.fase_id for f in m.FaseResponsable.select()
                                  .where(m.FaseResponsable.usuario == usuario.id)})
        auditoria.registrar("ACTIVAR_USUARIO" if activo else "DESACTIVAR_USUARIO", "usuario", usuario.id,
                            anterior={"activo": not activo}, nuevo={"activo": activo}, usuario=por)
    return usuario


def restablecer_password(usuario_id, por):
    """RF-22: nueva clave temporal, cambio obligatorio y desbloqueo de la cuenta."""
    with db.atomic():
        usuario = _obtener(m.Usuario, usuario_id, "El usuario")
        clave = clave_temporal()
        usuario.password_hash = hash_password(clave)
        usuario.must_change_password = True
        usuario.intentos_fallidos = 0
        usuario.bloqueado_hasta = None
        usuario.save()
        auditoria.registrar("RESTABLECER_PASSWORD", "usuario", usuario.id, usuario=por)
    return clave
