"""Horas y duraciones para mostrar al staff, en hora de Chile."""

from zoneinfo import ZoneInfo

ZONA = ZoneInfo("America/Santiago")


def hora_local(momento) -> str:
    return momento.astimezone(ZONA).strftime("%H:%M") if momento else ""


def duracion(segundos) -> str:
    """'menos de 1 min', '7 min' o '1 h 05 min'."""
    minutos = max(0, int(segundos)) // 60
    if minutos < 1:
        return "menos de 1 min"
    if minutos < 60:
        return f"{minutos} min"
    return f"{minutos // 60} h {minutos % 60:02d} min"
