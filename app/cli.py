"""Comandos de línea (`flask ...`) para tareas programadas con cron, fuera de Gunicorn."""

import click

from app.services import retencion


def _fila(resultado) -> str:
    return (f"{resultado.expedientes} expedientes, {resultado.atenciones} atenciones, {resultado.correos} correos, "
            f"{resultado.postulantes} postulantes, {resultado.auditoria_anonimizada} registros de auditoría anonimizados")


@click.command("purgar-datos")
@click.option("--simular", is_flag=True, help="Muestra qué se eliminaría, sin eliminar nada.")
@click.option("--fecha", type=click.DateTime(formats=["%Y-%m-%d"]), default=None,
              help="Fecha de referencia (AAAA-MM-DD); por defecto, hoy en Chile.")
def purgar_datos(simular, fecha):
    """RF-26: elimina los datos personales de los procesos cerrados con la fecha de eliminación cumplida."""
    informe = retencion.purgar(hoy=fecha.date() if fecha else None, simular=simular)
    prefijo = "[SIMULACIÓN] Se eliminarían" if simular else "Eliminados"
    for proceso in informe.alertas:
        click.echo(f"ALERTA: «{proceso.nombre}» sigue ACTIVO y su fecha de eliminación "
                   f"({proceso.fecha_eliminacion:%d-%m-%Y}) ya pasó. Ciérrelo o ajuste la fecha; no se eliminó nada.",
                   err=True)
    for resultado in informe.procesos:
        click.echo(f"{prefijo} de «{resultado.proceso}»: {_fila(resultado)}.")
    if informe.sin_proceso and informe.sin_proceso.postulantes:
        click.echo(f"{prefijo} {informe.sin_proceso.postulantes} postulantes cargados que nunca iniciaron un proceso.")
    if not informe.procesos and not (informe.sin_proceso and informe.sin_proceso.postulantes):
        click.echo("No hay datos con el plazo de conservación vencido.")


def registrar(app):
    app.cli.add_command(purgar_datos)
