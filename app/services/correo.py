"""Correos al postulante (RF-21): inicio, matrícula completada y reenvíos del enlace.

Un envío nunca hace fallar la operación que lo originó: se intenta después de confirmar la
transacción y queda registrado en `envio_correo` con estado ENVIADO o ERROR, y en la
auditoría. Sin SMTP configurado el envío queda con el error "SMTP no configurado" (en
desarrollo, el contenido se escribe en el log).
"""

import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app, render_template
from peewee import JOIN

from app import models as m
from app.services import auditoria, seguimiento, tiempo
from app.services import workflow as wf
from app.services.postulantes import enmascarar_correo

INICIO, MATRICULADO, REENVIO, REENVIO_QR = "INICIO", "MATRICULADO", "REENVIO", "REENVIO_QR"
TIPOS = {INICIO: "Inicio", MATRICULADO: "Matrícula completada", REENVIO: "Reenvío manual",
         REENVIO_QR: "Reenvío por QR"}
ESTADOS = {"ENVIADO": "Enviado", "ERROR": "Error"}
SIN_SMTP = "SMTP no configurado"

# RF-10: como máximo 3 reenvíos por hora, sumando el manual y el del QR
LIMITE_REENVIOS = 3
_VENTANA = timedelta(hours=1)
# RNF-04: el envío no debe bloquear la respuesta más de unos segundos
_TIMEOUT_SMTP = 5


class _SinSmtp(Exception):
    pass


# ── Contenido ───────────────────────────────────────────────────────

def construir(expediente, postulante) -> EmailMessage:
    """Mensaje con el estado actual del recorrido: resumen e instrucción mientras está en
    curso, o el resumen final si ya quedó Matriculado."""
    config = current_app.config
    datos = seguimiento.vista(expediente)
    datos.update(
        enlace=f"{config['APP_BASE_URL']}/seguimiento/{expediente.token_seguimiento}",
        contacto=config.get("CONTACTO_MATRICULA") or "",
    )
    if datos["matriculado"]:
        asunto = f"{datos['proceso']} · Ya estás Matriculado ({datos['numero']})"
    else:
        asunto = f"{datos['proceso']} · Tu número de atención es {datos['numero']}"

    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = formataddr((config["SMTP_FROM_NAME"], config["SMTP_FROM_EMAIL"]))
    mensaje["To"] = postulante.correo_personal
    mensaje.set_content(render_template("correo/postulante.txt", **datos))
    mensaje.add_alternative(render_template("correo/postulante.html", **datos), subtype="html")
    return mensaje


# ── Envío ───────────────────────────────────────────────────────────

def _enviar_smtp(mensaje):
    config = current_app.config
    contexto = ssl.create_default_context()
    if config["SMTP_PORT"] == 465:
        conexion = smtplib.SMTP_SSL(config["SMTP_HOST"], 465, timeout=_TIMEOUT_SMTP, context=contexto)
    else:
        conexion = smtplib.SMTP(config["SMTP_HOST"], config["SMTP_PORT"], timeout=_TIMEOUT_SMTP)
    with conexion:
        if config["SMTP_PORT"] != 465 and config["SMTP_USE_TLS"]:
            conexion.starttls(context=contexto)
        if config["SMTP_USER"]:
            conexion.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
        conexion.send_message(mensaje)


def enviar(expediente, tipo, usuario=None, actor=None, ahora=None):
    """Envía el correo y registra el resultado. Nunca lanza por fallas del SMTP."""
    expediente = m.Expediente.get_by_id(expediente.id)  # estado vigente tras la transacción
    postulante = m.Postulante.get_by_id(expediente.postulante_id)
    mensaje = construir(expediente, postulante)
    estado, detalle = "ENVIADO", None
    try:
        if not current_app.config.get("SMTP_HOST"):
            raise _SinSmtp
        _enviar_smtp(mensaje)
    except _SinSmtp:
        estado, detalle = "ERROR", SIN_SMTP
        if current_app.debug:
            current_app.logger.info("Correo no enviado (%s):\n%s", SIN_SMTP, mensaje.get_body(("plain",)).get_content())
    except (smtplib.SMTPException, OSError) as error:
        estado, detalle = "ERROR", (str(error) or type(error).__name__)[:1000]
        current_app.logger.warning("Falló el envío de correo del expediente %s: %s", expediente.id, detalle)

    envio = m.EnvioCorreo.create(
        expediente=expediente.id, tipo=tipo, destinatario=postulante.correo_personal, estado=estado,
        detalle_error=detalle, enviado_por=usuario.id if usuario else None, creado_en=ahora or m.ahora(),
    )
    auditoria.registrar("ENVIAR_CORREO", "expediente", expediente.id, usuario=usuario, actor=actor, nuevo={
        "tipo": tipo, "destinatario": postulante.correo_personal, "estado": estado, "detalle": detalle,
    })
    return envio


def enviar_inicio(expediente, ahora=None):
    return enviar(expediente, INICIO, actor="SISTEMA", ahora=ahora)


def enviar_matricula(expediente):
    """Una única vez por expediente: si se reabre y se vuelve a completar, no se reenvía (RF-21)."""
    ya_enviado = (m.EnvioCorreo.select()
                  .where((m.EnvioCorreo.expediente == expediente.id) & (m.EnvioCorreo.tipo == MATRICULADO))
                  .exists())
    if not ya_enviado:
        enviar(expediente, MATRICULADO, actor="SISTEMA")


# ── Reenvíos ────────────────────────────────────────────────────────

def _reenvios_recientes(expediente, ahora) -> int:
    return (m.EnvioCorreo.select()
            .where((m.EnvioCorreo.expediente == expediente.id)
                   & m.EnvioCorreo.tipo.in_((REENVIO, REENVIO_QR))
                   & (m.EnvioCorreo.creado_en > ahora - _VENTANA))
            .count())


def reenviar(expediente_id, usuario, ahora=None) -> str:
    """Botón "Reenviar enlace" de la ficha (cualquier rol)."""
    ahora = ahora or m.ahora()
    expediente = m.Expediente.get_or_none(m.Expediente.id == expediente_id)
    if expediente is None:
        raise wf.ReglaVioladaError("El expediente no existe", 404)
    if m.Proceso.get_by_id(expediente.proceso_id).estado != "ACTIVO":
        raise wf.ReglaVioladaError("El proceso está cerrado: el enlace de seguimiento ya no está vigente", 409)
    if _reenvios_recientes(expediente, ahora) >= LIMITE_REENVIOS:
        raise wf.ReglaVioladaError(
            f"Se alcanzó el límite de {LIMITE_REENVIOS} reenvíos por hora. Intente más tarde.", 429)
    envio = enviar(expediente, REENVIO, usuario=usuario, ahora=ahora)
    if envio.estado == "ERROR":
        raise wf.ReglaVioladaError(f"No se pudo enviar el correo: {envio.detalle_error}", 502)
    return f"Enviamos el enlace a {enmascarar_correo(envio.destinatario)}"


def reenviar_qr(expediente, ahora=None):
    """RF-10 paso 4. Superado el límite no envía, y el QR muestra el mismo mensaje para no
    revelar nada; el intento queda auditado."""
    ahora = ahora or m.ahora()
    if _reenvios_recientes(expediente, ahora) >= LIMITE_REENVIOS:
        auditoria.registrar("REENVIO_QR_LIMITADO", "expediente", expediente.id, actor="PUBLICO")
        return None
    return enviar(expediente, REENVIO_QR, actor="PUBLICO", ahora=ahora)


# ── Historial para la ficha ─────────────────────────────────────────

def historial(expediente) -> list:
    filas = (m.EnvioCorreo.select(m.EnvioCorreo, m.Usuario.nombre)
             .join(m.Usuario, JOIN.LEFT_OUTER, on=(m.EnvioCorreo.enviado_por == m.Usuario.id))
             .where(m.EnvioCorreo.expediente == expediente.id)
             .order_by(m.EnvioCorreo.creado_en.desc(), m.EnvioCorreo.id.desc()))
    resultado = []
    for envio in filas.objects():
        if envio.enviado_por_id:
            quien = envio.nombre
        else:
            quien = "Postulante (QR)" if envio.tipo == REENVIO_QR else "Sistema"
        resultado.append({
            "fecha": tiempo.fecha_hora_corta(envio.creado_en),
            "tipo": TIPOS[envio.tipo],
            "destinatario": envio.destinatario,
            "estado": ESTADOS[envio.estado],
            "detalle": envio.detalle_error,
            "enviado_por": quien,
        })
    return resultado
