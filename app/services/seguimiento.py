"""Vista de seguimiento del postulante con su enlace único (RF-20).

Solo expone lo que el postulante necesita: número de atención, primer nombre, carreras y
su recorrido. Nunca el RUT, el teléfono, el correo, las observaciones ni los responsables.
"""

import hashlib

from app import models as m
from app.db import db
from app.services import recorridos
from app.services import workflow as wf
from app.services.inicio import datos_de, nombres_de_carrera, numero_visible
from app.services.postulantes import CARRERAS

# Atenciones que no se muestran: la fase interrumpida vuelve a planificarse más adelante,
# y el abandono se informa con un mensaje propio
_OCULTAS = (wf.DERIVADA, wf.ABANDONADA)


# RNF-04: el sondeo cada 10 s de miles de postulantes resuelve con una sola consulta si algo
# cambió (estado del expediente y del proceso, datos del postulante y cada atención de la ruta).
# Solo cuando cambia se arma la vista completa.
_FIRMA = """
SELECT e.estado, p.estado, po.actualizado_en,
       (SELECT string_agg(a.id || ':' || a.secuencia || ':' || a.estado || ':' || a.version, ','
                          ORDER BY a.secuencia)
          FROM atencion a WHERE a.expediente_id = e.id)
  FROM expediente e
  JOIN proceso p ON p.id = e.proceso_id
  JOIN postulante po ON po.id = e.postulante_id
 WHERE e.token_seguimiento = %s
"""


def firma(token):
    """Huella del estado visible del expediente, o None si el token no existe."""
    if not token or len(token) > 64:
        return None
    fila = db.execute_sql(_FIRMA, (token,)).fetchone()
    if fila is None:
        return None
    return hashlib.sha256(repr(fila).encode()).hexdigest()[:32]


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
