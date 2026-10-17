"""Vista de seguimiento del postulante con su enlace único (RF-20).

Solo expone lo que el postulante necesita: número de atención, primer nombre, carreras y
su recorrido. Nunca el RUT, el teléfono, el correo, las observaciones ni los responsables.
"""

from app import models as m
from app.services import recorridos
from app.services import workflow as wf
from app.services.inicio import datos_de, nombres_de_carrera, numero_visible
from app.services.postulantes import CARRERAS

# Atenciones que no se muestran: la fase interrumpida vuelve a planificarse más adelante,
# y el abandono se informa con un mensaje propio
_OCULTAS = (wf.DERIVADA, wf.ABANDONADA)


def por_token(token):
    if not token or len(token) > 64:
        return None
    return m.Expediente.get_or_none(m.Expediente.token_seguimiento == token)


def vista(expediente) -> dict:
    proceso = m.Proceso.get_by_id(expediente.proceso_id)
    if proceso.estado == "CERRADO":
        return {"finalizado": True}
    _, recorrido = recorridos.cargar(expediente.id, bloquear=False)
    postulante = m.Postulante.get_by_id(expediente.postulante_id)
    datos = datos_de(postulante)

    pasos = []
    for atencion in recorrido.ruta:
        if atencion.estado in _OCULTAS:
            continue
        if atencion.estado in wf.ESTADOS_COMPLETOS:
            estado = "hecho"
        elif atencion.estado in wf.ESTADOS_ACTIVOS:
            estado = "actual"
        else:
            estado = "pendiente"
        pasos.append({"fase": recorrido.estructura.fase(atencion.fase_id).nombre, "estado": estado})

    actual = recorrido.actual
    instruccion = None
    if actual is not None:
        nombre = recorrido.estructura.fase(actual.fase_id).nombre
        instruccion = (f"Estás siendo atendido en: {nombre}" if actual.estado == wf.EN_ATENCION
                       else f"Dirígete a: {nombre}")
    return {
        "finalizado": False,
        "proceso": proceso.nombre,
        "numero": numero_visible(proceso, expediente.numero_atencion),
        "primer_nombre": (postulante.nombres.split() or [""])[0],
        "carreras": nombres_de_carrera([datos[campo] for campo in CARRERAS]),
        "pasos": pasos,
        "instruccion": instruccion,
        "matriculado": expediente.estado == wf.MATRICULADO,
        "en_pausa": expediente.estado == wf.ABANDONO,
    }
