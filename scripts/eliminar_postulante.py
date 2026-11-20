"""Elimina un postulante por RUT, con sus expedientes y todo lo asociado.

Borra el postulante, sus carreras de interés, sus expedientes, las atenciones y el registro
de correos. La auditoría no se borra: se anonimiza (sin RUT, correo ni datos personales),
igual que en la purga de fin de proceso (RF-26), y se deja un registro de la eliminación.

Pensado para limpiar pruebas (ej.: un inicio por QR con un correo real). Se ejecuta con
scripts/eliminar-postulante.sh, que pide el RUT y la confirmación. Directo, en el contenedor:

    PYTHONPATH=/app python scripts/eliminar_postulante.py 14319010-2              # muestra qué borraría
    PYTHONPATH=/app python scripts/eliminar_postulante.py 14319010-2 --confirmar  # borra
"""

import sys

from app import create_app
from app import models as m
from app.db import db
from app.services import auditoria, inicio, retencion, tiempo

NO_ENCONTRADO = 3


def _auditoria_del_rut(rut):
    """Registros de auditoría sin postulante asociado que mencionan el RUT (consultas del QR)."""
    return ((m.Auditoria.entidad == "postulante") & m.Auditoria.entidad_id.is_null()
            & (m.Auditoria.valor_nuevo.cast("text").contains(rut)
               | m.Auditoria.valor_anterior.cast("text").contains(rut)))


def resumen(postulante, mostrar=True):
    expedientes = list(m.Expediente.select(m.Expediente, m.Proceso).join(m.Proceso)
                       .where(m.Expediente.postulante == postulante.id))
    ids = [e.id for e in expedientes]
    atenciones = m.Atencion.select().where(m.Atencion.expediente.in_(ids)).count() if ids else 0
    correos = m.EnvioCorreo.select().where(m.EnvioCorreo.expediente.in_(ids)).count() if ids else 0
    if not mostrar:
        return ids
    print(f"Postulante: {postulante.nombres} {postulante.apellidos} · RUT {postulante.rut} · "
          f"origen {postulante.origen} · registrado el "
          f"{postulante.creado_en.astimezone(tiempo.ZONA):%d-%m-%Y %H:%M}")
    if not expedientes:
        print("Expedientes: ninguno (cargado sin proceso iniciado)")
    for e in expedientes:
        numero = inicio.numero_visible(e.proceso, e.numero_atencion)
        print(f"Expediente: {numero} en «{e.proceso.nombre}» ({e.proceso.estado}) · {e.estado}")
        if e.proceso.estado == "ACTIVO" and e.proceso.codigo != "DEMO":
            print("  ATENCIÓN: es un expediente del proceso de matrícula real en curso.")
    print(f"Se borran {len(expedientes)} expediente(s), {atenciones} atención(es) y {correos} registro(s) "
          f"de correo. La auditoría se conserva anonimizada.")
    return ids


def eliminar(postulante, ids):
    rut = postulante.rut
    anonimizados = 0
    if ids:
        anonimizados += retencion._anonimizar_auditoria(
            (m.Auditoria.entidad == "expediente") & m.Auditoria.entidad_id.in_(ids))
    anonimizados += retencion._anonimizar_auditoria(_auditoria_del_rut(rut))
    expedientes = m.Expediente.delete().where(m.Expediente.id.in_(ids)).execute() if ids else 0  # atenciones y correos en cascada
    postulantes, extra = retencion._borrar_postulantes([postulante.id])
    anonimizados += extra
    auditoria.registrar("ELIMINAR_POSTULANTE", "postulante", None, actor="SISTEMA", usuario=None,
                        nuevo={"expedientes": expedientes, "postulantes": postulantes,
                               "auditoria_anonimizada": anonimizados,
                               "motivo": "Eliminado con scripts/eliminar_postulante.py"})
    print(f"Listo: {postulantes} postulante y {expedientes} expediente(s) eliminados; "
          f"{anonimizados} registro(s) de auditoría anonimizados.")


def main(argv):
    if not argv or argv[0].startswith("-"):
        print("Uso: eliminar_postulante.py <RUT> [--confirmar]", file=sys.stderr)
        return 2
    try:
        rut = inicio.normalizar_rut(argv[0])
    except inicio.DatosInvalidosError as error:
        print(f"RUT no válido: {error}", file=sys.stderr)
        return 2
    app = create_app()
    with app.app_context(), db.connection_context(), db.atomic():
        postulante = m.Postulante.get_or_none(m.Postulante.rut == rut)
        if postulante is None:
            print(f"No hay un postulante con RUT {rut}.")
            return NO_ENCONTRADO
        confirmar = "--confirmar" in argv[1:]
        ids = resumen(postulante, mostrar=not confirmar)
        if confirmar:
            eliminar(postulante, ids)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
