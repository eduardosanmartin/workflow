"""Inicio de proceso por staff (RF-09) y por QR (RF-10), y creación del expediente (RF-11).

Todo inicio ocurre en una transacción que bloquea la fila del proceso ACTIVO
(`SELECT … FOR UPDATE`): así se valida que siga activo, se asigna el número de atención
sin duplicados (RF-19) y dos inicios simultáneos del mismo RUT producen un solo
expediente (RNF-06). Los mismos bloqueos usa la configuración para cambiar la
estructura, de modo que no se puede agregar una fase mientras se crea el primer expediente.

El correo de inicio (RF-21) se agrega en el paso de correo.
"""

import secrets

from peewee import IntegrityError

from app import models as m
from app.db import db
from app.services import auditoria, recorridos
from app.services import workflow as wf
from app.services.postulantes import CAMPOS_EDITABLES, CARRERAS, campos_pendientes, enmascarar, validar_postulante
from app.services.rut import RutInvalido, normalizar

SIN_PROCESO_ACTIVO = "No hay un proceso de matrícula activo"
DUDAS = ("no", "si", "flujo")


class DatosInvalidosError(wf.ReglaVioladaError):
    """Errores de validación de los datos del postulante, por campo."""

    def __init__(self, campos: dict, mensaje="Revise los datos del postulante"):
        super().__init__(mensaje, 422)
        self.campos = dict(campos)


class ExpedienteExistenteError(wf.ReglaVioladaError):
    """El postulante ya tiene expediente en el proceso activo: no se crea otro (RF-09, RF-10)."""

    def __init__(self, expediente):
        super().__init__("El postulante ya tiene un expediente en el proceso activo", 409)
        self.expediente = expediente


def _invalido(mensaje):
    return wf.ReglaVioladaError(mensaje, 422)


# ── Proceso activo y permisos ───────────────────────────────────────

def proceso_activo(bloquear=False):
    consulta = m.Proceso.select().where(m.Proceso.estado == "ACTIVO")
    if bloquear:
        consulta = consulta.for_update()
    return consulta.first()


def _exigir_proceso_activo():
    """Dentro de la transacción del inicio: bloquea el proceso activo o rechaza (RF-09, RF-10)."""
    proceso = proceso_activo(bloquear=True)
    if proceso is None:
        raise wf.ReglaVioladaError(SIN_PROCESO_ACTIVO, 409)
    return proceso


def fase_inicial(proceso):
    return m.Fase.get((m.Fase.proceso == proceso.id) & (m.Fase.tipo == wf.INICIAL))


def puede_iniciar(usuario, proceso=None) -> bool:
    """§4: Administrador y Gestor siempre; Usuario de fase solo si es responsable de "Iniciado"
    del proceso activo."""
    if usuario is None or not usuario.activo:
        return False
    if usuario.rol in wf.ROLES_TODAS_LAS_FASES:
        return True
    proceso = proceso or proceso_activo()
    if proceso is None:
        return False
    return (
        m.FaseResponsable.select()
        .join(m.Fase)
        .where((m.FaseResponsable.usuario == usuario.id) & (m.Fase.proceso == proceso.id)
               & (m.Fase.tipo == wf.INICIAL))
        .exists()
    )


def numero_visible(proceso, numero) -> str:
    """RF-19: prefijo y correlativo de 4 dígitos (ej.: `M-0042`)."""
    return f"{proceso.prefijo_atencion}-{numero:04d}"


# ── Postulantes ─────────────────────────────────────────────────────

def normalizar_rut(rut) -> str:
    try:
        return normalizar(rut)
    except RutInvalido as error:
        raise DatosInvalidosError({"rut": str(error)}, str(error)) from None


def buscar_postulante(rut):
    return m.Postulante.get_or_none(m.Postulante.rut == rut)


def datos_de(postulante) -> dict:
    """Datos de RF-06 del postulante, con los códigos de sus carreras."""
    datos = {
        "rut": postulante.rut, "nombres": postulante.nombres, "apellidos": postulante.apellidos,
        "telefono": postulante.telefono or "", "correo_personal": postulante.correo_personal,
    }
    datos.update({campo: None for campo in CARRERAS})
    filas = (m.PostulanteCarrera.select(m.PostulanteCarrera, m.Carrera).join(m.Carrera)
             .where(m.PostulanteCarrera.postulante == postulante.id))
    for fila in filas:
        datos[f"carrera{fila.preferencia}"] = fila.carrera.codigo
    return datos


def opciones_de_carrera(incluir=()) -> list:
    """Carreras para las listas desplegables: las activas, más las que ya tiene el postulante
    aunque estén inactivas (RF-25)."""
    incluir = [c for c in incluir if c]
    condicion = m.Carrera.activo == True  # noqa: E712
    if incluir:
        condicion |= m.Carrera.codigo.in_(incluir)
    return list(m.Carrera.select().where(condicion).order_by(m.Carrera.nombre))


def nombres_de_carrera(codigos) -> list:
    codigos = [c for c in codigos if c]
    por_codigo = {c.codigo: c.nombre for c in m.Carrera.select().where(m.Carrera.codigo.in_(codigos))}
    return [por_codigo.get(c, c) for c in codigos]


def _codigos_permitidos(actuales: dict) -> set:
    return {c.codigo for c in opciones_de_carrera([actuales.get(campo) for campo in CARRERAS])}


def _guardar_carreras(postulante, limpios):
    m.PostulanteCarrera.delete().where(m.PostulanteCarrera.postulante == postulante.id).execute()
    codigos = [limpios.get(campo) for campo in CARRERAS]
    ids = {c.codigo: c.id for c in m.Carrera.select().where(m.Carrera.codigo.in_([c for c in codigos if c]))}
    for preferencia, codigo in enumerate(codigos, 1):
        if codigo:
            m.PostulanteCarrera.create(postulante=postulante.id, preferencia=preferencia, carrera=ids[codigo])


def _crear_postulante(limpios, origen):
    try:
        with db.atomic():
            postulante = m.Postulante.create(
                rut=limpios["rut"], nombres=limpios["nombres"], apellidos=limpios["apellidos"],
                telefono=limpios["telefono"], correo_personal=limpios["correo_personal"], origen=origen,
            )
    except IntegrityError:
        # Otro registro del mismo RUT se confirmó entre la búsqueda y la creación
        raise wf.ReglaVioladaError("El postulante acaba de ser registrado por otra vía. Intente de nuevo.") from None
    _guardar_carreras(postulante, limpios)
    return postulante


def _actualizar_postulante(postulante, anteriores, limpios, campos):
    """Aplica `campos` de `limpios`. Devuelve (anterior, nuevo) solo con lo que cambió."""
    antes, despues = {}, {}
    for campo in campos:
        nuevo = limpios.get(campo) or None
        viejo = anteriores.get(campo) or None
        if nuevo != viejo:
            antes[campo], despues[campo] = viejo, nuevo
    if not despues:
        return {}, {}
    for campo in ("nombres", "apellidos", "telefono", "correo_personal"):
        if campo in despues:
            setattr(postulante, campo, despues[campo])
    postulante.actualizado_en = m.ahora()
    postulante.save()
    if any(campo in despues for campo in CARRERAS):
        _guardar_carreras(postulante, {**anteriores, **{c: limpios.get(c) for c in campos}})
    return antes, despues


def _exigir_activo(postulante):
    if postulante is not None and not postulante.activo:
        raise wf.ReglaVioladaError("El postulante está desactivado. Contacte al Administrador.")


def expediente_en(proceso, postulante):
    if postulante is None:
        return None
    return m.Expediente.get_or_none(
        (m.Expediente.proceso == proceso.id) & (m.Expediente.postulante == postulante.id)
    )


# ── Ruta inicial (RF-14) ────────────────────────────────────────────

def ruta_desde_formulario(form, estructura) -> dict:
    """Lee la respuesta a "¿Tiene dudas?" del inicio por staff.

    - `dudas=no`: ruta directa a MNP.
    - `dudas=si`: fases de consulta numeradas en `orden_<fase_id>` (1, 2, 3…), en ese orden.
    - `dudas=flujo`: `entrada=<flujo_id>:<fase_id>`, un flujo alternativo desde una de sus fases.
    """
    dudas = (form.get("dudas") or "").strip()
    if dudas not in DUDAS:
        raise _invalido("Indique si el postulante tiene dudas")
    if dudas == "no":
        return {"consultas": []}
    if dudas == "si":
        consultas = consultas_numeradas(form, estructura)
        if not consultas:
            raise _invalido("Elija al menos una fase de consulta, numerándolas en el orden de visita")
        return {"consultas": consultas}
    try:
        flujo_id, fase_id = (int(parte) for parte in (form.get("entrada") or "").split(":"))
    except ValueError:
        raise _invalido("Elija el flujo alternativo y su fase de entrada") from None
    return {"flujo_id": flujo_id, "fase_entrada_id": fase_id}


def consultas_numeradas(form, estructura) -> list:
    """Fases de consulta numeradas 1, 2, 3… en `orden_<fase_id>`, en ese orden (puede ser vacía)."""
    elegidas = []
    for fase in estructura.fases:
        if fase.tipo != wf.CONSULTA:
            continue
        valor = (form.get(f"orden_{fase.id}") or "").strip()
        if not valor:
            continue
        if not valor.isdigit() or int(valor) < 1:
            raise _invalido("El orden de las fases debe ser un número desde 1")
        elegidas.append((int(valor), fase.id))
    if len({n for n, _ in elegidas}) != len(elegidas):
        raise _invalido("No repita números en el orden de las fases")
    return [fase_id for _, fase_id in sorted(elegidas)]


def opciones_de_flujo(estructura, nombres_fase) -> list:
    """(valor, texto) de cada entrada posible a un flujo alternativo activo."""
    opciones = []
    for flujo in estructura.flujos:
        if not flujo.activo:
            continue
        for fase_id in flujo.fases:
            opciones.append((f"{flujo.id}:{fase_id}", f"{flujo.nombre}: desde {nombres_fase[fase_id]}"))
    return opciones


# ── Creación del expediente (RF-11) ─────────────────────────────────

def _crear_expediente(proceso, postulante, recorrido, via, por, ahora):
    numero = recorridos.siguiente_numero_atencion(proceso.id)
    try:
        with db.atomic():
            expediente = m.Expediente.create(
                proceso=proceso.id, postulante=postulante.id, via_inicio=via,
                iniciado_por=por.id if por else None, numero_atencion=numero, creado_en=ahora,
                token_seguimiento=secrets.token_urlsafe(32),  # 256 bits (RF-20)
            )
    except IntegrityError:
        raise ExpedienteExistenteError(expediente_en(proceso, postulante)) from None
    recorridos.guardar(expediente, recorrido)
    if postulante.eliminar_en is not None:
        # RF-26: desde que inicia un proceso rige la fecha de eliminación del proceso
        postulante.eliminar_en = None
        postulante.save()
    return expediente


def _foto_ruta(recorrido, estructura):
    return [
        {"fase": estructura.fase(a.fase_id).nombre, "estado": a.estado}
        for a in recorrido.ruta
    ]


def iniciar_por_staff(datos: dict, ruta: dict, por, ahora=None):
    """RF-09 y RF-11. `datos` trae todos los campos de RF-06 (el RUT no se corrige) y `ruta`
    viene de `ruta_desde_formulario`. Devuelve el expediente creado."""
    ahora = ahora or m.ahora()
    with db.atomic():
        proceso = _exigir_proceso_activo()
        if not puede_iniciar(por, proceso):
            raise wf.ReglaVioladaError("Solo los responsables de «Iniciado» pueden iniciar procesos", 403)
        rut = normalizar_rut(datos.get("rut"))
        postulante = buscar_postulante(rut)
        _exigir_activo(postulante)
        expediente = expediente_en(proceso, postulante)
        if expediente is not None:
            raise ExpedienteExistenteError(expediente)

        anteriores = datos_de(postulante) if postulante else {}
        limpios, errores = validar_postulante({**datos, "rut": rut}, _codigos_permitidos(anteriores))
        if errores:
            raise DatosInvalidosError(errores)

        estructura = recorridos.estructura_del_proceso(proceso.id)
        recorrido = wf.Recorrido.iniciar_por_staff(
            estructura, por.id, ahora, consultas=ruta.get("consultas", ()),
            flujo_id=ruta.get("flujo_id"), fase_entrada_id=ruta.get("fase_entrada_id"),
        )

        if postulante is None:
            postulante = _crear_postulante(limpios, "INICIO_STAFF")
            auditoria.registrar("CREAR_POSTULANTE", "postulante", postulante.id,
                                nuevo={**limpios, "origen": "INICIO_STAFF"}, usuario=por)
        else:
            antes, despues = _actualizar_postulante(postulante, anteriores, limpios, CAMPOS_EDITABLES)
            if despues:
                auditoria.registrar("EDITAR_POSTULANTE", "postulante", postulante.id,
                                    anterior=antes, nuevo=despues, usuario=por)

        expediente = _crear_expediente(proceso, postulante, recorrido, "STAFF", por, ahora)
        auditoria.registrar("INICIAR_PROCESO", "expediente", expediente.id, usuario=por, nuevo={
            "via": "STAFF", "postulante_id": postulante.id,
            "numero_atencion": numero_visible(proceso, expediente.numero_atencion),
            "flujo_alternativo_id": recorrido.flujo_alternativo_id,
            "ruta": _foto_ruta(recorrido, estructura),
        })
    return expediente


# ── Inicio por QR (RF-10) ───────────────────────────────────────────

def consultar_qr(rut_ingresado) -> dict:
    """Paso 1 del QR: valida el RUT y prepara lo que se puede mostrar al público.

    Devuelve un dict con `rut`, `proceso`, `existe`, `expediente`, `vista` (datos a mostrar,
    enmascarados si así lo configuró el Administrador), `pendientes` y `consultas`.
    Cada consulta de un RUT existente queda auditada con actor PUBLICO.
    """
    proceso = proceso_activo()
    if proceso is None:
        raise wf.ReglaVioladaError("El proceso de matrícula no está abierto", 409)
    rut = normalizar_rut(rut_ingresado)
    postulante = buscar_postulante(rut)
    _exigir_activo_qr(postulante)
    estructura = recorridos.estructura_del_proceso(proceso.id)
    resultado = {
        "rut": rut, "proceso": proceso, "existe": postulante is not None, "expediente": None,
        "vista": {}, "pendientes": list(CAMPOS_EDITABLES), "consultas": estructura.consultas_qr(),
    }
    if postulante is None:
        return resultado
    datos = datos_de(postulante)
    resultado["expediente"] = expediente_en(proceso, postulante)
    resultado["vista"] = enmascarar(datos) if proceso.enmascarar_datos_qr else datos
    resultado["pendientes"] = campos_pendientes(datos)
    resultado["correo_enmascarado"] = enmascarar(datos)["correo_personal"]
    auditoria.registrar("CONSULTA_RUT_QR", "postulante", postulante.id, actor="PUBLICO",
                        nuevo={"tiene_expediente": resultado["expediente"] is not None})
    return resultado


def _exigir_activo_qr(postulante):
    if postulante is not None and not postulante.activo:
        raise wf.ReglaVioladaError("No podemos iniciar tu proceso con este RUT. Acércate al staff de matrícula.")


def iniciar_por_qr(datos: dict, consultas, ahora=None):
    """RF-10 y RF-11. Si el RUT existe, solo se aceptan los campos pendientes: los datos ya
    registrados no se modifican desde el QR. `consultas` son los temas de duda marcados."""
    ahora = ahora or m.ahora()
    with db.atomic():
        proceso = _exigir_proceso_activo()
        rut = normalizar_rut(datos.get("rut"))
        postulante = buscar_postulante(rut)
        _exigir_activo_qr(postulante)
        expediente = expediente_en(proceso, postulante)
        if expediente is not None:
            raise ExpedienteExistenteError(expediente)

        anteriores = datos_de(postulante) if postulante else {}
        pendientes = campos_pendientes(anteriores) if postulante else list(CAMPOS_EDITABLES)
        combinados = {**anteriores, **{campo: datos.get(campo) for campo in pendientes}, "rut": rut}
        limpios, errores = validar_postulante(combinados, _codigos_permitidos(anteriores))
        if errores:
            ajenos = [campo for campo in errores if campo not in pendientes and campo != "rut"]
            if ajenos:
                raise wf.ReglaVioladaError(
                    "Algunos de tus datos registrados no son válidos. Acércate al staff para corregirlos.", 422)
            raise DatosInvalidosError(errores, "Revisa los datos marcados")

        estructura = recorridos.estructura_del_proceso(proceso.id)
        try:
            elegidas = [int(c) for c in consultas]
        except (TypeError, ValueError):
            raise wf.ReglaVioladaError("Tema de duda no disponible en el formulario", 400) from None
        recorrido = wf.Recorrido.iniciar_por_qr(estructura, ahora, consultas=elegidas)

        if postulante is None:
            postulante = _crear_postulante(limpios, "INICIO_QR")
            auditoria.registrar("CREAR_POSTULANTE", "postulante", postulante.id, actor="PUBLICO",
                                nuevo={**limpios, "origen": "INICIO_QR"})
        else:
            antes, despues = _actualizar_postulante(postulante, anteriores, limpios, pendientes)
            if despues:
                auditoria.registrar("COMPLETAR_POSTULANTE", "postulante", postulante.id, actor="PUBLICO",
                                    anterior=antes, nuevo=despues)

        expediente = _crear_expediente(proceso, postulante, recorrido, "QR", None, ahora)
        auditoria.registrar("INICIAR_PROCESO", "expediente", expediente.id, actor="PUBLICO", nuevo={
            "via": "QR", "postulante_id": postulante.id,
            "numero_atencion": numero_visible(proceso, expediente.numero_atencion),
            "ruta_propuesta": _foto_ruta(recorrido, estructura),
        })
    return expediente


def enmascarar_correo_de(expediente) -> str:
    return enmascarar({"correo_personal": m.Postulante.get_by_id(expediente.postulante_id).correo_personal})[
        "correo_personal"]


def registrar_reenvio_qr(expediente):
    """RF-10 paso 4: el RUT ya tiene expediente. El envío real del enlace llega con el paso de
    correo; por ahora queda auditada la solicitud."""
    auditoria.registrar("SOLICITUD_REENVIO_QR", "expediente", expediente.id, actor="PUBLICO")


# ── Resumen para la pantalla de confirmación ────────────────────────

def resumen(expediente) -> dict:
    """Número de atención, fase a la que debe dirigirse y ruta, para el staff."""
    _, recorrido = recorridos.cargar(expediente.id, bloquear=False)
    proceso = m.Proceso.get_by_id(expediente.proceso_id)
    estructura = recorrido.estructura
    actual = recorrido.fase_actual
    return {
        "expediente": expediente,
        "postulante": m.Postulante.get_by_id(expediente.postulante_id),
        "numero": numero_visible(proceso, expediente.numero_atencion),
        "fase_actual": actual.nombre if actual else None,
        "estado_actual": recorrido.actual.estado if recorrido.actual else None,
        "ruta": [estructura.fase(a.fase_id).nombre for a in recorrido.ruta],
        "flujo": estructura.flujo(recorrido.flujo_alternativo_id).nombre if recorrido.flujo_alternativo_id else None,
        "estado": expediente.estado,
    }
