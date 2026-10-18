"""Datos de demostración para pilotos y capacitación (`flask datos-demo`).

Arma un proceso "Matrícula DEMO" activo, con fases, un flujo alternativo, usuarios de prueba
por rol y unos 30 postulantes en distintos estados, usando los mismos servicios que la
aplicación (quedan auditados como cualquier acción). No envía correos reales.

Solo para ambientes de prueba: se niega a correr si ya hay un proceso activo o expedientes.
"""

import random
from dataclasses import dataclass, field
from datetime import timedelta

from flask import current_app

from app import models as m
from app.db import db
from app.services import bandeja, ficha, inicio, registro, tiempo
from app.services import configuracion as cfg
from app.services import workflow as wf
from app.services.rut import calcular_dv

CODIGO = "DEMO"
PREFIJO_CORREO = "demo."  # los usuarios de prueba: demo.<rol>@unach.cl
DOMINIO_POSTULANTES = "example.com"  # dominio reservado: ningún correo llega a una persona real

NOMBRES = [
    ("Ana María", "Paz Rojas"), ("Luis Alberto", "Soto Vera"), ("Camila", "Fuentes Lagos"),
    ("Diego", "Muñoz Torres"), ("Valentina", "Reyes Díaz"), ("Tomás", "Herrera Silva"),
    ("Fernanda", "Castro Núñez"), ("Matías", "Rojas Pino"), ("Javiera", "Morales Vidal"),
    ("Benjamín", "Contreras Sepúlveda"), ("Isidora", "Espinoza Araya"), ("Joaquín", "Gutiérrez Tapia"),
    ("Antonia", "Vásquez Cortés"), ("Martín", "Fernández Bravo"), ("Florencia", "Pizarro Olivares"),
    ("Vicente", "Jara Carrasco"), ("Emilia", "Navarro Salazar"), ("Agustín", "Riquelme Parra"),
    ("Catalina", "Sandoval Figueroa"), ("Maximiliano", "Valenzuela Rubio"), ("Sofía", "Cáceres Molina"),
    ("Cristóbal", "Orellana Fuentes"), ("Martina", "Aguilera Ríos"), ("Lucas", "Bustos Leiva"),
    ("Trinidad", "Medina Gallardo"), ("Gaspar", "Zúñiga Vergara"), ("Josefa", "Saavedra Peña"),
    ("Felipe", "Arancibia Soto"), ("Amanda", "Toro Cárdenas"), ("Ignacio", "Lagos Mena"),
]


class DemoNoPermitidaError(Exception):
    pass


@dataclass
class Resumen:
    proceso: m.Proceso = None
    usuarios: list = field(default_factory=list)  # [(nombre, correo, rol, clave temporal)]
    cargados: int = 0
    expedientes: int = 0


# ── Ayudas ──────────────────────────────────────────────────────────

def _datos(i, carreras):
    cuerpo = str(15000000 + i * 271828)
    nombres, apellidos = NOMBRES[i]
    usuario = nombres.split()[0].lower().translate(str.maketrans("áéíóúñ", "aeioun"))
    elegidas = random.sample(carreras, k=min(len(carreras), 1 + i % 3))
    return {
        "rut": f"{cuerpo}-{calcular_dv(cuerpo)}", "nombres": nombres, "apellidos": apellidos,
        "telefono": f"+569{40000000 + i * 1234567 % 50000000:08d}",
        "correo_personal": f"{usuario}.{i}@{DOMINIO_POSTULANTES}",
        "carrera1": elegidas[0], "carrera2": elegidas[1] if len(elegidas) > 1 else "",
        "carrera3": elegidas[2] if len(elegidas) > 2 else "",
    }


def _version(expediente):
    return (m.Atencion.select().where((m.Atencion.expediente == expediente.id)
                                      & m.Atencion.estado.in_(wf.ESTADOS_ACTIVOS)).get().version)


def _atender(expediente, quien, momento):
    bandeja.atender(expediente.id, _version(expediente), quien, ahora=momento)


def _finalizar(expediente, quien, momento, **form):
    form = {"version": str(_version(expediente)), "accion": "seguir", "resultado": "completado", **form}
    bandeja.finalizar(expediente.id, form, quien, ahora=momento)


# ── Creación ────────────────────────────────────────────────────────

def _exigir_ambiente_vacio():
    if m.Proceso.select().where(m.Proceso.estado == "ACTIVO").exists():
        raise DemoNoPermitidaError("Ya hay un proceso activo: los datos de demostración solo se cargan en un "
                                   "ambiente de prueba sin procesos activos.")
    if m.Expediente.select().exists():
        raise DemoNoPermitidaError("Ya hay expedientes en la base: los datos de demostración solo se cargan en "
                                   "un ambiente de prueba vacío.")
    if m.Proceso.select().where(m.Proceso.codigo == CODIGO).exists():
        raise DemoNoPermitidaError("Ya existe el proceso DEMO. Elimínelo antes con `flask datos-demo --eliminar`.")


def _administrador():
    admin = (m.Usuario.select().where((m.Usuario.rol == "ADMIN") & m.Usuario.activo)
             .order_by(m.Usuario.id).first())
    if admin is None:
        raise DemoNoPermitidaError("No hay un Administrador activo: configure el super administrador en el .env.")
    return admin


def crear(ahora=None, semilla=2027) -> Resumen:
    random.seed(semilla)
    ahora = ahora or m.ahora()
    _exigir_ambiente_vacio()
    admin = _administrador()
    config = current_app.config
    respaldo = config.get("SMTP_HOST"), config.get("CORREO_EN_SEGUNDO_PLANO")
    config["SMTP_HOST"], config["CORREO_EN_SEGUNDO_PLANO"] = "", False  # ningún correo sale
    try:
        return _crear(admin, ahora)
    finally:
        config["SMTP_HOST"], config["CORREO_EN_SEGUNDO_PLANO"] = respaldo


def _crear(admin, ahora) -> Resumen:
    resumen = Resumen()
    hoy = ahora.astimezone(tiempo.ZONA).date()
    with db.atomic():
        proceso = cfg.crear_proceso({
            "codigo": CODIGO, "nombre": "Matrícula DEMO", "prefijo_atencion": "D",
            "descripcion": "Proceso de demostración para pilotos y capacitación. No contiene datos reales.",
            "fecha_inicio": (hoy - timedelta(days=7)).isoformat(), "fecha_fin": (hoy + timedelta(days=60)).isoformat(),
            "fecha_eliminacion": (hoy + timedelta(days=90)).isoformat(), "enmascarar_datos_qr": "on",
        }, admin)
        for identificador, nombre, qr in (("BEC", "Becas", "on"), ("FIN", "Finanzas", "on"),
                                          ("CAJA", "Caja", ""), ("FIRMA", "Firma de contrato", "")):
            cfg.agregar_fase(proceso.id, {"identificador": identificador, "nombre": nombre, "mostrar_en_qr": qr}, admin)
        f = {x.identificador: x.id for x in m.Fase.select().where(m.Fase.proceso == proceso.id)}
        flujo = cfg.crear_flujo(proceso.id, {
            "codigo": "EFE", "nombre": "Pago en efectivo", "fases": [f["FIN"], f["CAJA"], f["FIRMA"]],
            "descripcion": "Pago presencial y firma manual del contrato, sin pasar por MNP.",
        }, admin)

        usuarios = {}
        for clave, nombre, rol, fases, carga in (
            ("gestor", "Gabriela Gestora (demo)", "GESTOR", [], False),
            ("inicio", "Inés Inicio (demo)", "USUARIO_FASE", [f["INI"]], True),
            ("becas", "Beatriz Becas (demo)", "USUARIO_FASE", [f["BEC"]], False),
            ("finanzas", "Felipe Finanzas (demo)", "USUARIO_FASE", [f["FIN"], f["CAJA"], f["FIRMA"]], False),
            ("mnp", "Marta Tótem MNP (demo)", "USUARIO_FASE", [f["MNP"]], False),
        ):
            usuario, temporal = cfg.crear_usuario({
                "email": f"{PREFIJO_CORREO}{clave}@unach.cl", "nombre": nombre, "rol": rol, "fases": fases,
                "puede_registrar_postulantes": "on" if carga else "",
            }, admin)
            usuarios[clave] = usuario
            resumen.usuarios.append((nombre, usuario.email, rol, temporal))
        cfg.activar_proceso(proceso.id, admin)
        resumen.proceso = m.Proceso.get_by_id(proceso.id)

    carreras = [c.codigo for c in m.Carrera.select().where(m.Carrera.activo).order_by(m.Carrera.nombre)]
    if not carreras:
        raise DemoNoPermitidaError("No hay carreras activas: importe el catálogo antes de cargar la demostración.")
    _recorridos(resumen, usuarios, f, flujo, carreras, admin, ahora)
    return resumen


def _recorridos(resumen, u, f, flujo, carreras, admin, ahora):
    """20 expedientes en distintos estados y 10 postulantes cargados sin iniciar."""
    inicio_staff, becas, finanzas, mnp = u["inicio"], u["becas"], u["finanzas"], u["mnp"]
    t0 = ahora - timedelta(hours=3)

    def t(minutos):
        return t0 + timedelta(minutes=minutos)

    def staff(i, minuto, consultas=()):
        return inicio.iniciar_por_staff(_datos(i, carreras), {"consultas": [f[c] for c in consultas]},
                                        inicio_staff, ahora=t(minuto))

    def efectivo(entrada):
        return f"{flujo.id}:{f[entrada]}"

    # Matriculados: directos, con Becas, uno con observaciones y uno por cierre manual
    for i, minuto in ((0, 0), (1, 6), (2, 15)):
        e = staff(i, minuto)
        _atender(e, mnp, t(minuto + 4))
        _finalizar(e, mnp, t(minuto + 16))
    for i, minuto, obs in ((3, 3, False), (4, 10, True), (5, 22, False)):
        e = staff(i, minuto, ["BEC"])
        _atender(e, becas, t(minuto + 7))
        extra = {"resultado": "observaciones", "observacion": "Debe presentar el certificado de notas."} if obs else {}
        _finalizar(e, becas, t(minuto + 19), **extra)
        _atender(e, mnp, t(minuto + 28))
        _finalizar(e, mnp, t(minuto + 41))
    e = staff(6, 30, ["FIN"])
    _atender(e, finanzas, t(38))
    _finalizar(e, finanzas, t(47), accion="flujo", entrada=efectivo("CAJA"))
    _atender(e, finanzas, t(55))
    _finalizar(e, finanzas, t(60))
    _atender(e, finanzas, t(66))
    _finalizar(e, finanzas, t(80), detalle_cierre="Pago en efectivo, boleta 004512. Contrato firmado en papel.",
               confirmar_cierre="on")

    # En la bandeja: en espera y en atención en distintas fases
    e = staff(7, 120, ["BEC", "FIN"])
    _atender(e, becas, t(128))                                  # en atención en Becas
    for i, minuto in ((8, 140), (9, 150), (10, 162)):
        staff(i, minuto, ["BEC"])                               # en espera en Becas
    e = staff(11, 100, ["FIN"])
    _atender(e, finanzas, t(110))
    _finalizar(e, finanzas, t(124))                             # en espera en MNP, viene de Finanzas
    staff(12, 155)                                              # en espera en MNP
    e = staff(13, 145)
    _atender(e, mnp, t(160))                                    # en atención en MNP
    e = staff(14, 90, ["FIN"])
    _finalizar(e, finanzas, t(105), accion="flujo", entrada=efectivo("FIN"))
    _atender(e, finanzas, t(115))
    _finalizar(e, finanzas, t(130))                             # en espera en Caja (flujo alternativo)
    staff(15, 170, ["FIN"])                                     # en espera en Finanzas

    # Por QR: esperan en "Iniciado" con la ruta que propusieron
    inicio.iniciar_por_qr(_datos(16, carreras), [f["BEC"]], ahora=t(172))
    inicio.iniciar_por_qr(_datos(17, carreras), [], ahora=t(175))

    # Abandonos: uno vigente y otro reactivado
    e = staff(18, 40, ["BEC"])
    bandeja.marcar_abandono(e.id, _version(e), becas, ahora=t(95))
    e = staff(19, 50, ["BEC"])
    bandeja.marcar_abandono(e.id, _version(e), becas, ahora=t(85))
    ficha.reactivar(e.id, {}, admin, ahora=t(165))              # vuelve a esperar en Becas
    resumen.expedientes = 20

    for i in range(20, 30):
        registro.registrar(_datos(i, carreras), admin)
    resumen.cargados = 10


# ── Eliminación ─────────────────────────────────────────────────────

def eliminar() -> int:
    """Borra el proceso DEMO con sus expedientes y los postulantes de demostración, y desactiva
    los usuarios de prueba (los usuarios no se borran, para conservar la auditoría)."""
    proceso = m.Proceso.get_or_none(m.Proceso.codigo == CODIGO)
    if proceso is None:
        raise DemoNoPermitidaError("No existe el proceso DEMO.")
    with db.atomic():
        expedientes = m.Expediente.select(m.Expediente.id).where(m.Expediente.proceso == proceso.id)
        postulantes = [p.id for p in m.Postulante.select(m.Postulante.id)
                       .where(m.Postulante.correo_personal.endswith(f"@{DOMINIO_POSTULANTES}"))]
        borrados = m.Expediente.delete().where(m.Expediente.id.in_(expedientes)).execute()
        con_otro = {e.postulante_id for e in m.Expediente.select(m.Expediente.postulante)
                    .where(m.Expediente.postulante.in_(postulantes))} if postulantes else set()
        restantes = [p for p in postulantes if p not in con_otro]
        if restantes:
            m.Postulante.delete().where(m.Postulante.id.in_(restantes)).execute()
        m.ProcesoResumen.delete().where(m.ProcesoResumen.proceso == proceso.id).execute()
        m.FlujoAlternativo.delete().where(m.FlujoAlternativo.proceso == proceso.id).execute()
        m.Proceso.delete().where(m.Proceso.id == proceso.id).execute()
        m.Usuario.update(activo=False).where(m.Usuario.email.startswith(PREFIJO_CORREO)).execute()
    return borrados
