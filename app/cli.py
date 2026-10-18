"""Comandos de línea (`flask ...`) para tareas programadas con cron, fuera de Gunicorn."""

import click

from app.services import demo, retencion


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


@click.command("datos-demo")
@click.option("--eliminar", is_flag=True, help="Elimina el proceso DEMO y sus postulantes de prueba.")
def datos_demo(eliminar):
    """Carga un proceso de demostración con usuarios y postulantes de prueba (solo ambientes de prueba)."""
    try:
        if eliminar:
            borrados = demo.eliminar()
            click.echo(f"Proceso DEMO eliminado ({borrados} expedientes). Los usuarios de prueba quedaron desactivados.")
            return
        resumen = demo.crear()
    except demo.DemoNoPermitidaError as error:
        raise click.ClickException(str(error)) from None
    click.echo(f"Listo: «{resumen.proceso.nombre}» activo, con {resumen.expedientes} expedientes en distintos "
               f"estados y {resumen.cargados} postulantes cargados sin iniciar.")
    click.echo("Usuarios de prueba (contraseña temporal: se pide cambiarla al primer ingreso):")
    for nombre, correo, rol, clave in resumen.usuarios:
        click.echo(f"  {correo:28s} {clave}   {nombre} · {rol}")
    click.echo("Los correos a los postulantes no se enviaron: aparecen en Reportes › Correos fallidos.")


def registrar(app):
    app.cli.add_command(purgar_datos)
    app.cli.add_command(datos_demo)
