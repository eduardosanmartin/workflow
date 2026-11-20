"""Guion de demostración: 10 postulantes que recorren los tres flujos del proceso.

Arma el proceso "Matrícula 2027 (DEMO)" (código DEMO) con sus fases, el flujo alternativo
"Pago en efectivo" y usuarios de prueba por rol. Deja 10 casos: unos ya terminados, para
mostrar el historial y los reportes, y otros detenidos justo antes de la acción que se
muestra en vivo. No envía correos reales.

Solo en un ambiente sin procesos activos ni expedientes. Se elimina con:
    flask --app app:create_app datos-demo --eliminar

Uso, dentro del contenedor web:
    PYTHONPATH=/app python scripts/demo_recorrido.py
"""

from datetime import timedelta

from app import create_app
from app import models as m
from app.db import db
from app.services import bandeja, inicio, registro
from app.services import configuracion as cfg
from app.services import demo
from app.services.rut import calcular_dv

# (caso, nombres, apellidos, cuerpo del RUT, carreras)
POSTULANTES = [
    (1, "Catalina", "Rojas Fuentes", "22104387", ["ENF"]),
    (2, "Benjamín", "Soto Muñoz", "22215640", ["ICI", "ICO"]),
    (3, "Valentina", "Pérez Lagos", "22326915", ["PSI", "TSO"]),
    (4, "Matías", "Contreras Vidal", "22437182", ["KIN", "ENF"]),
    (5, "Josefa", "Herrera Navarro", "22548471", ["DER"]),
    (6, "Tomás", "Espinoza Araya", "22659734", ["ICO", "ICI", "DER"]),
    (7, "Florencia", "Castro Pino", "22760028", ["PEB", "PSI"]),
    (8, "Ignacio", "Morales Tapia", "22871355", ["ICI"]),
    (9, "Antonia", "Vásquez Bravo", "22982640", ["TSO", "PSI"]),
    (10, "Martín", "Gutiérrez Cortés", "23093917", ["DER", "ICO"]),
]


def datos(caso):
    _, nombres, apellidos, cuerpo, carreras = POSTULANTES[caso - 1]
    usuario = nombres.lower().translate(str.maketrans("áéíóúñ", "aeioun"))
    return {
        "rut": f"{cuerpo}-{calcular_dv(cuerpo)}", "nombres": nombres, "apellidos": apellidos,
        "telefono": f"+5699{caso:03d}{cuerpo[-4:]}",
        "correo_personal": f"{usuario}.caso{caso}@{demo.DOMINIO_POSTULANTES}",
        "carrera1": carreras[0], "carrera2": carreras[1] if len(carreras) > 1 else "",
        "carrera3": carreras[2] if len(carreras) > 2 else "",
    }


def configurar(admin, hoy):
    with db.atomic():
        proceso = cfg.crear_proceso({
            "codigo": demo.CODIGO, "nombre": "Matrícula 2027 (DEMO)", "prefijo_atencion": "D",
            "descripcion": "Proceso de demostración con datos sintéticos. No contiene datos reales.",
            "fecha_inicio": (hoy - timedelta(days=1)).isoformat(), "fecha_fin": (hoy + timedelta(days=30)).isoformat(),
            "fecha_eliminacion": (hoy + timedelta(days=60)).isoformat(), "enmascarar_datos_qr": "on",
        }, admin)
        for ident, nombre, qr in (("BEC", "Becas", "on"), ("FIN", "Finanzas", "on"),
                                  ("CAJA", "Caja", ""), ("FIRMA", "Firma de contrato", "")):
            cfg.agregar_fase(proceso.id, {"identificador": ident, "nombre": nombre, "mostrar_en_qr": qr}, admin)
        f = {x.identificador: x.id for x in m.Fase.select().where(m.Fase.proceso == proceso.id)}
        flujo = cfg.crear_flujo(proceso.id, {
            "codigo": "EFE", "nombre": "Pago en efectivo", "fases": [f["FIN"], f["CAJA"], f["FIRMA"]],
            "descripcion": "Pago presencial en Caja y firma manual del contrato, sin pasar por MNP.",
        }, admin)
        usuarios, credenciales = {}, []
        for clave, nombre, rol, fases, carga in (
            ("gestor", "Gabriela Gestora (demo)", "GESTOR", [], False),
            ("inicio", "Inés Inicio (demo)", "USUARIO_FASE", [f["INI"]], True),
            ("becas", "Beatriz Becas (demo)", "USUARIO_FASE", [f["BEC"]], False),
            ("finanzas", "Felipe Finanzas (demo)", "USUARIO_FASE", [f["FIN"], f["CAJA"], f["FIRMA"]], False),
            ("mnp", "Marta Tótem MNP (demo)", "USUARIO_FASE", [f["MNP"]], False),
        ):
            datos_usuario = {
                "email": f"{demo.PREFIJO_CORREO}{clave}@unach.cl", "nombre": nombre, "rol": rol, "fases": fases,
                "puede_registrar_postulantes": "on" if carga else "",
            }
            existente = m.Usuario.get_or_none(m.Usuario.email == datos_usuario["email"])
            if existente is None:
                usuario, temporal = cfg.crear_usuario(datos_usuario, admin)
            else:  # quedó desactivado por `datos-demo --eliminar`: se reutiliza con clave nueva
                usuario = cfg.editar_usuario(existente.id, datos_usuario, admin)
                cfg.cambiar_estado_usuario(usuario.id, True, admin)
                temporal = cfg.restablecer_password(usuario.id, admin)
                usuario = m.Usuario.get_by_id(usuario.id)
            usuarios[clave] = usuario
            credenciales.append((usuario.email, temporal, nombre, rol))
        cfg.activar_proceso(proceso.id, admin)
    return proceso, f, flujo, usuarios, credenciales


def recorridos(f, flujo, u, admin, ahora):
    ini, bec, fin, mnp = u["inicio"], u["becas"], u["finanzas"], u["mnp"]
    t0 = ahora - timedelta(minutes=150)

    def t(minutos):
        return t0 + timedelta(minutes=minutos)

    def staff(caso, minuto, consultas=()):
        return inicio.iniciar_por_staff(datos(caso), {"consultas": [f[c] for c in consultas]}, ini, ahora=t(minuto))

    def atender(e, quien, minuto):
        demo._atender(e, quien, t(minuto))

    def finalizar(e, quien, minuto, **form):
        demo._finalizar(e, quien, t(minuto), **form)

    def efectivo(entrada):
        return f"{flujo.id}:{f[entrada]}"

    # Caso 1 · Flujo 1 (directo) terminado: Iniciado → MNP, cierre digital
    e = staff(1, 0)
    atender(e, mnp, 5)
    finalizar(e, mnp, 18)

    # Caso 4 · Flujo 2 (con consultas) terminado: Becas (con observaciones) → Finanzas → MNP
    e = staff(4, 8, ["BEC", "FIN"])
    atender(e, bec, 14)
    finalizar(e, bec, 29, resultado="observaciones",
              observacion="Debe presentar el certificado de notas de 4° medio para la beca.",
              **{f"orden_{f['FIN']}": "1"})  # confirma que sigue a Finanzas
    atender(e, fin, 35)
    finalizar(e, fin, 46)
    atender(e, mnp, 52)
    finalizar(e, mnp, 66)

    # Caso 8 · Flujo 3 (alternativo) terminado: Finanzas → [Pago en efectivo] Caja → Firma, cierre manual
    e = staff(8, 20, ["FIN"])
    atender(e, fin, 30)
    finalizar(e, fin, 41, accion="flujo", entrada=efectivo("CAJA"))
    atender(e, fin, 48)
    finalizar(e, fin, 55)
    atender(e, fin, 60)
    finalizar(e, fin, 72, detalle_cierre="Pago en efectivo, boleta N° 004512. Contrato firmado en papel.",
              confirmar_cierre="on")

    # Caso 9 · Flujo 3 en curso: entró desde Becas; espera en Firma de contrato (fase final manual)
    e = staff(9, 70, ["BEC"])
    atender(e, bec, 78)
    finalizar(e, bec, 90, accion="flujo", entrada=efectivo("FIN"))
    atender(e, fin, 96)
    finalizar(e, fin, 104)
    atender(e, fin, 110)
    finalizar(e, fin, 118)

    # Caso 6 · Flujo 2 en curso: pasó por Becas y está en atención en MNP
    e = staff(6, 85, ["BEC"])
    atender(e, bec, 95)
    finalizar(e, bec, 108)
    atender(e, mnp, 135)

    # Caso 5 · Flujo 2 en curso: en atención en Becas, ruta Becas → MNP
    e = staff(5, 120, ["BEC"])
    atender(e, bec, 132)

    # Caso 10 · Flujo 2 en curso: espera en Finanzas
    staff(10, 128, ["FIN"])

    # Caso 2 · Flujo 1 en curso: espera en MNP
    staff(2, 138)

    # Caso 7 · Inicio por QR: espera en Iniciado con la ruta que propuso (Becas y Finanzas)
    inicio.iniciar_por_qr(datos(7), [f["BEC"], f["FIN"]], ahora=t(142))

    # Caso 3 · Cargado por el Administrador, sin proceso iniciado (se inicia en vivo)
    registro.registrar(datos(3), admin)


def main():
    app = create_app()
    with app.app_context(), db.connection_context():
        demo._exigir_ambiente_vacio()
        admin = demo._administrador()
        import seed_carreras
        seed_carreras.sembrar()
        app.config["SMTP_HOST"], app.config["CORREO_EN_SEGUNDO_PLANO"] = "", False  # ningún correo sale
        ahora = m.ahora()
        hoy = ahora.astimezone(demo.tiempo.ZONA).date()
        with db.atomic():  # si algo falla, no queda nada a medias
            proceso, f, flujo, usuarios, credenciales = configurar(admin, hoy)
            recorridos(f, flujo, usuarios, admin, ahora)

    print(f"Listo: «{proceso.nombre}» activo con 10 postulantes de demostración.")
    print("Usuarios de prueba (contraseña temporal; se pide cambiarla en el primer ingreso):")
    for correo, clave, nombre, rol in credenciales:
        print(f"  {correo:26s} {clave}   {nombre} · {rol}")
    print("\nPostulantes:")
    for caso, nombres, apellidos, *_ in POSTULANTES:
        d = datos(caso)
        print(f"  Caso {caso:2d}  {d['rut']:12s} {nombres} {apellidos}")


if __name__ == "__main__":
    main()
