"""Pantallas de configuración del Administrador (§10, pantallas 8, 8b, 9, 10, 13 y 16)."""

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from app import models as m
from app.security import requiere_rol
from app.services import catalogo, poster
from app.services import configuracion as cfg
from app.services import workflow as wf

bp = Blueprint("admin", __name__, url_prefix="/admin")

NOMBRE_TIPO = {wf.INICIAL: "Inicial", wf.CONSULTA: "Consulta", wf.CIERRE_DIGITAL: "Cierre digital"}
NOMBRE_ESTADO = {"BORRADOR": "Borrador", "ACTIVO": "Activo", "CERRADO": "Cerrado"}


@bp.before_request
@requiere_rol("ADMIN")
def _solo_admin():
    return None


@bp.after_request
def _sin_cache(respuesta):
    # Algunas páginas muestran claves temporales: que el navegador no las guarde
    respuesta.headers["Cache-Control"] = "no-store"
    return respuesta


@bp.context_processor
def _contexto():
    return {"NOMBRE_TIPO": NOMBRE_TIPO, "NOMBRE_ESTADO": NOMBRE_ESTADO}


def _ejecutar(funcion, *args):
    """Ejecuta un cambio del servicio. Devuelve (resultado, True) o, si viola una regla,
    muestra los errores y devuelve (None, False)."""
    try:
        return funcion(*args), True
    except wf.ReglaVioladaError as error:
        if error.codigo_http == 404:
            abort(404)
        for mensaje in getattr(error, "errores", [error.mensaje]):
            flash(mensaje, "error")
        return None, False


def _obtener(modelo, id_):
    objeto = modelo.get_or_none(modelo.id == id_)
    if objeto is None:
        abort(404)
    return objeto


def _form():
    """Formulario como dict, con las listas (casillas múltiples) como listas."""
    datos = request.form.to_dict()
    datos.pop("csrf_token", None)
    for campo in ("fases", "usuarios"):
        if campo in request.form:
            datos[campo] = request.form.getlist(campo)
    return datos


def _casillas(*campos):
    """Las casillas sin marcar no se envían: se completan como vacías."""
    datos = _form()
    for campo in campos:
        datos.setdefault(campo, "")
    return datos


@bp.get("/")
def inicio():
    return redirect(url_for("admin.procesos"))


# ── Procesos ──

@bp.get("/procesos")
def procesos():
    lista = m.Proceso.select().order_by(m.Proceso.fecha_inicio.desc(), m.Proceso.id.desc())
    return render_template("admin/procesos.html", procesos=lista)


@bp.route("/procesos/nuevo", methods=["GET", "POST"])
def proceso_nuevo():
    datos = {"prefijo_atencion": "M", "enmascarar_datos_qr": "on"}
    if request.method == "POST":
        datos = _casillas("enmascarar_datos_qr")
        proceso, ok = _ejecutar(cfg.crear_proceso, datos, g.usuario)
        if ok:
            flash(f"Proceso «{proceso.nombre}» creado en borrador, con sus fases Iniciado y MNP.", "exito")
            return redirect(url_for("admin.proceso", proceso_id=proceso.id))
    return render_template("admin/proceso_form.html", datos=datos, proceso=None, iniciado=False)


@bp.get("/procesos/<int:proceso_id>")
def proceso(proceso_id):
    proceso = _obtener(m.Proceso, proceso_id)
    fases = list(m.Fase.select().where(m.Fase.proceso == proceso.id).order_by(m.Fase.orden))
    responsables = {}
    for fila in (m.FaseResponsable.select(m.FaseResponsable, m.Usuario).join(m.Usuario)
                 .where(m.FaseResponsable.fase.in_([f.id for f in fases])).order_by(m.Usuario.nombre)):
        responsables.setdefault(fila.fase_id, []).append(fila.usuario)
    nombres = {f.id: f.nombre for f in fases}
    flujos = [
        (flujo, [nombres[f] for f in cfg.fases_del_flujo(flujo.id)])
        for flujo in m.FlujoAlternativo.select().where(m.FlujoAlternativo.proceso == proceso.id)
        .order_by(m.FlujoAlternativo.nombre)
    ]
    return render_template(
        "admin/proceso.html", proceso=proceso, fases=fases, responsables=responsables, flujos=flujos,
        iniciado=cfg.proceso_iniciado(proceso.id),
        errores=cfg.errores_de_activacion(proceso.id) if proceso.estado == "BORRADOR" else [],
        activo=m.Proceso.get_or_none(m.Proceso.estado == "ACTIVO"),
    )


@bp.route("/procesos/<int:proceso_id>/editar", methods=["GET", "POST"])
def proceso_editar(proceso_id):
    proceso = _obtener(m.Proceso, proceso_id)
    iniciado = cfg.proceso_iniciado(proceso.id)
    datos = {campo: getattr(proceso, campo) for campo in cfg._CAMPOS_PROCESO}
    datos["enmascarar_datos_qr"] = "on" if proceso.enmascarar_datos_qr else ""
    if request.method == "POST":
        datos = _casillas("enmascarar_datos_qr")
        _, ok = _ejecutar(cfg.editar_proceso, proceso.id, datos, g.usuario)
        if ok:
            flash("Proceso actualizado.", "exito")
            return redirect(url_for("admin.proceso", proceso_id=proceso.id))
    return render_template("admin/proceso_form.html", datos=datos, proceso=proceso, iniciado=iniciado)


@bp.post("/procesos/<int:proceso_id>/activar")
def proceso_activar(proceso_id):
    _, ok = _ejecutar(cfg.activar_proceso, proceso_id, g.usuario)
    if ok:
        flash("Proceso activado: ya acepta inicios de proceso por QR y por staff.", "exito")
    return redirect(url_for("admin.proceso", proceso_id=proceso_id))


@bp.route("/procesos/<int:proceso_id>/cerrar", methods=["GET", "POST"])
def proceso_cerrar(proceso_id):
    proceso = _obtener(m.Proceso, proceso_id)
    if request.method == "POST":
        if request.form.get("confirmacion") != proceso.codigo:
            flash(f"Para confirmar, escriba el código del proceso ({proceso.codigo}).", "error")
        else:
            pendientes, ok = _ejecutar(cfg.cerrar_proceso, proceso.id, g.usuario)
            if ok:
                flash(f"Proceso cerrado. {pendientes} expediente(s) quedaron como «Proceso pendiente».", "exito")
                return redirect(url_for("admin.proceso", proceso_id=proceso.id))
    return render_template("admin/proceso_cerrar.html", proceso=proceso,
                           pendientes=cfg.pendientes_al_cerrar(proceso.id))


# ── Fases ──

@bp.route("/procesos/<int:proceso_id>/fases/nueva", methods=["GET", "POST"])
def fase_nueva(proceso_id):
    proceso = _obtener(m.Proceso, proceso_id)
    datos = {"mostrar_en_qr": "on"}
    if request.method == "POST":
        datos = _casillas("mostrar_en_qr")
        fase, ok = _ejecutar(cfg.agregar_fase, proceso.id, datos, g.usuario)
        if ok:
            flash(f"Fase «{fase.nombre}» agregada.", "exito")
            return redirect(url_for("admin.proceso", proceso_id=proceso.id))
    return render_template("admin/fase_form.html", proceso=proceso, fase=None, datos=datos, iniciado=False)


def _usuarios_activos():
    return list(m.Usuario.select().where(m.Usuario.activo == True).order_by(m.Usuario.nombre))  # noqa: E712


@bp.route("/fases/<int:fase_id>", methods=["GET", "POST"])
def fase(fase_id):
    fase = _obtener(m.Fase, fase_id)
    datos = {campo: getattr(fase, campo) for campo in ("identificador", "nombre", "descripcion")}
    datos["mostrar_en_qr"] = "on" if fase.mostrar_en_qr else ""
    if request.method == "POST":
        datos = _casillas("mostrar_en_qr")
        _, ok = _ejecutar(cfg.editar_fase, fase.id, datos, g.usuario)
        if ok:
            flash("Fase actualizada.", "exito")
            return redirect(url_for("admin.proceso", proceso_id=fase.proceso_id))
    return render_template(
        "admin/fase_form.html", proceso=fase.proceso, fase=fase, datos=datos,
        iniciado=cfg.proceso_iniciado(fase.proceso_id),
        usuarios=_usuarios_activos(), responsables=cfg.responsables_de(fase.id),
    )


@bp.post("/fases/<int:fase_id>/responsables")
def fase_responsables(fase_id):
    fase = _obtener(m.Fase, fase_id)
    _, ok = _ejecutar(cfg.asignar_responsables, fase.id, request.form.getlist("usuarios"), g.usuario)
    if ok:
        flash(f"Responsables de «{fase.nombre}» actualizados.", "exito")
    return redirect(url_for("admin.fase", fase_id=fase.id))


@bp.post("/fases/<int:fase_id>/mover")
def fase_mover(fase_id):
    fase = _obtener(m.Fase, fase_id)
    _ejecutar(cfg.mover_fase, fase.id, -1 if request.form.get("direccion") == "subir" else 1, g.usuario)
    return redirect(url_for("admin.proceso", proceso_id=fase.proceso_id))


@bp.post("/fases/<int:fase_id>/eliminar")
def fase_eliminar(fase_id):
    fase = _obtener(m.Fase, fase_id)
    _, ok = _ejecutar(cfg.eliminar_fase, fase.id, g.usuario)
    if ok:
        flash(f"Fase «{fase.nombre}» eliminada.", "exito")
    return redirect(url_for("admin.proceso", proceso_id=fase.proceso_id))


# ── Flujos alternativos ──

def _consultas(proceso_id):
    return list(m.Fase.select().where((m.Fase.proceso == proceso_id) & (m.Fase.tipo == wf.CONSULTA))
                .order_by(m.Fase.orden))


def _secuencia_del_form(consultas):
    """La secuencia se arma con el número de posición escrito junto a cada fase (vacío = no va)."""
    posiciones = []
    for fase in consultas:
        valor = request.form.get(f"posicion_{fase.id}", "").strip()
        if valor:
            try:
                posiciones.append((int(valor), fase.orden, fase.id))
            except ValueError:
                flash(f"La posición de «{fase.nombre}» debe ser un número.", "error")
                return None
    return [fase_id for _, _, fase_id in sorted(posiciones)]


def _flujo_form(proceso, flujo, datos, posiciones):
    return render_template("admin/flujo_form.html", proceso=proceso, flujo=flujo, datos=datos,
                           consultas=_consultas(proceso.id), posiciones=posiciones,
                           iniciado=cfg.proceso_iniciado(proceso.id))


@bp.route("/procesos/<int:proceso_id>/flujos/nuevo", methods=["GET", "POST"])
def flujo_nuevo(proceso_id):
    proceso = _obtener(m.Proceso, proceso_id)
    datos, posiciones = {"activo": "on"}, {}
    if request.method == "POST":
        datos = _casillas("activo")
        posiciones = {k: v for k, v in request.form.items() if k.startswith("posicion_")}
        secuencia = _secuencia_del_form(_consultas(proceso.id))
        if secuencia is not None:
            flujo, ok = _ejecutar(cfg.crear_flujo, proceso.id, {**datos, "fases": secuencia}, g.usuario)
            if ok:
                flash(f"Flujo alternativo «{flujo.nombre}» creado.", "exito")
                return redirect(url_for("admin.proceso", proceso_id=proceso.id))
    return _flujo_form(proceso, None, datos, posiciones)


@bp.route("/flujos/<int:flujo_id>", methods=["GET", "POST"])
def flujo(flujo_id):
    flujo = _obtener(m.FlujoAlternativo, flujo_id)
    proceso = flujo.proceso
    datos = {campo: getattr(flujo, campo) for campo in ("codigo", "nombre", "descripcion")}
    datos["activo"] = "on" if flujo.activo else ""
    posiciones = {f"posicion_{f}": str(i) for i, f in enumerate(cfg.fases_del_flujo(flujo.id), 1)}
    if request.method == "POST":
        datos = _casillas("activo")
        posiciones = {k: v for k, v in request.form.items() if k.startswith("posicion_")}
        cambios = dict(datos)
        if not cfg.proceso_iniciado(proceso.id):
            cambios["fases"] = _secuencia_del_form(_consultas(proceso.id))
        if cambios.get("fases", []) is not None:
            _, ok = _ejecutar(cfg.editar_flujo, flujo.id, cambios, g.usuario)
            if ok:
                flash("Flujo alternativo actualizado.", "exito")
                return redirect(url_for("admin.proceso", proceso_id=proceso.id))
    return _flujo_form(proceso, flujo, datos, posiciones)


@bp.post("/flujos/<int:flujo_id>/eliminar")
def flujo_eliminar(flujo_id):
    flujo = _obtener(m.FlujoAlternativo, flujo_id)
    _, ok = _ejecutar(cfg.eliminar_flujo, flujo.id, g.usuario)
    if ok:
        flash(f"Flujo alternativo «{flujo.nombre}» eliminado.", "exito")
    return redirect(url_for("admin.proceso", proceso_id=flujo.proceso_id))


# ── Usuarios ──

def _fases_por_proceso():
    """Fases de procesos no cerrados, agrupadas por proceso, para asignarlas a un usuario."""
    grupos = []
    for proceso in m.Proceso.select().where(m.Proceso.estado != "CERRADO").order_by(m.Proceso.fecha_inicio.desc()):
        grupos.append((proceso, list(m.Fase.select().where(m.Fase.proceso == proceso.id).order_by(m.Fase.orden))))
    return grupos


@bp.get("/usuarios")
def usuarios():
    lista = list(m.Usuario.select().order_by(m.Usuario.activo.desc(), m.Usuario.nombre))
    cantidad_fases = {u.id: len(cfg.fases_abiertas_de(u.id)) for u in lista}
    return render_template("admin/usuarios.html", usuarios=lista, cantidad_fases=cantidad_fases,
                           ahora=m.ahora())


def _usuario_form(usuario, datos):
    return render_template("admin/usuario_form.html", usuario=usuario, datos=datos,
                           grupos=_fases_por_proceso(), roles=cfg.ROLES)


@bp.route("/usuarios/nuevo", methods=["GET", "POST"])
def usuario_nuevo():
    datos = {"rol": "USUARIO_FASE", "fases": []}
    if request.method == "POST":
        datos = _casillas("puede_registrar_postulantes")
        datos.setdefault("fases", [])
        resultado, ok = _ejecutar(cfg.crear_usuario, datos, g.usuario)
        if ok:
            usuario, clave = resultado
            # La clave se muestra en la respuesta, nunca en un flash: la sesión es una cookie firmada, no cifrada
            return render_template("admin/clave_temporal.html", usuario=usuario, clave=clave, nuevo=True)
    return _usuario_form(None, datos)


@bp.route("/usuarios/<int:usuario_id>", methods=["GET", "POST"])
def usuario(usuario_id):
    usuario = _obtener(m.Usuario, usuario_id)
    datos = {campo: getattr(usuario, campo) for campo in ("email", "nombre", "rol")}
    datos["puede_registrar_postulantes"] = "on" if usuario.puede_registrar_postulantes else ""
    datos["fases"] = [str(f) for f in cfg.fases_abiertas_de(usuario.id)]
    if request.method == "POST":
        datos = _casillas("puede_registrar_postulantes")
        datos.setdefault("fases", [])
        _, ok = _ejecutar(cfg.editar_usuario, usuario.id, datos, g.usuario)
        if ok:
            flash(f"Usuario «{datos.get('nombre', usuario.nombre).strip()}» actualizado.", "exito")
            return redirect(url_for("admin.usuarios"))
    return _usuario_form(usuario, datos)


@bp.post("/usuarios/<int:usuario_id>/estado")
def usuario_estado(usuario_id):
    activar = request.form.get("activo") == "1"
    usuario, ok = _ejecutar(cfg.cambiar_estado_usuario, usuario_id, activar, g.usuario)
    if ok:
        flash(f"Usuario «{usuario.nombre}» {'activado' if activar else 'desactivado'}.", "exito")
    return redirect(url_for("admin.usuarios"))


@bp.post("/usuarios/<int:usuario_id>/restablecer")
def usuario_restablecer(usuario_id):
    usuario = _obtener(m.Usuario, usuario_id)
    clave, ok = _ejecutar(cfg.restablecer_password, usuario.id, g.usuario)
    if not ok:
        return redirect(url_for("admin.usuarios"))
    return render_template("admin/clave_temporal.html", usuario=usuario, clave=clave, nuevo=False)


# ── Catálogo de carreras (RF-25) ──

def _carreras(datos=None, resultado=None, codigo=200):
    return render_template("admin/carreras.html", filas=catalogo.listar(), datos=datos or {},
                           resultado=resultado), codigo


@bp.get("/carreras")
def carreras():
    return _carreras()


@bp.post("/carreras")
def carrera_nueva():
    datos = _form()
    carrera, ok = _ejecutar(catalogo.crear, datos, g.usuario)
    if not ok:
        return _carreras(datos, codigo=422)
    flash(f"Carrera «{carrera.nombre}» ({carrera.codigo}) creada.", "exito")
    return redirect(url_for("admin.carreras"))


@bp.route("/carreras/<int:carrera_id>", methods=["GET", "POST"])
def carrera(carrera_id):
    carrera = _obtener(m.Carrera, carrera_id)
    datos = {"codigo": carrera.codigo, "nombre": carrera.nombre}
    if request.method == "POST":
        datos = _form()
        _, ok = _ejecutar(catalogo.editar, carrera.id, datos, g.usuario)
        if ok:
            flash("Carrera actualizada.", "exito")
            return redirect(url_for("admin.carreras"))
    return render_template("admin/carrera_form.html", carrera=carrera, datos=datos,
                           usada=catalogo.usada(carrera.id))


@bp.post("/carreras/<int:carrera_id>/estado")
def carrera_estado(carrera_id):
    activar = request.form.get("activo") == "1"
    carrera, ok = _ejecutar(catalogo.cambiar_estado, carrera_id, activar, g.usuario)
    if ok:
        flash(f"Carrera «{carrera.nombre}» {'activada' if activar else 'desactivada'}.", "exito")
    return redirect(url_for("admin.carreras"))


@bp.post("/carreras/<int:carrera_id>/eliminar")
def carrera_eliminar(carrera_id):
    carrera = _obtener(m.Carrera, carrera_id)
    _, ok = _ejecutar(catalogo.eliminar, carrera.id, g.usuario)
    if ok:
        flash(f"Carrera «{carrera.nombre}» eliminada.", "exito")
    return redirect(url_for("admin.carreras"))


@bp.post("/carreras/importar")
def carreras_importar():
    archivo = request.files.get("archivo")
    if archivo is None or not archivo.filename:
        flash("Elija un archivo .csv o .xlsx", "error")
        return _carreras(codigo=422)
    resultado, ok = _ejecutar(catalogo.importar, archivo.read(), archivo.filename, g.usuario)
    if not ok:
        return _carreras(codigo=422)
    flash(f"Catálogo importado: {resultado['creadas']} nuevas, {resultado['actualizadas']} actualizadas, "
          f"{resultado['sin_cambios']} sin cambios y {len(resultado['errores'])} con errores.", "exito")
    return _carreras(resultado=resultado)


# ── Póster del QR "Inicia aquí" (pantalla 16) ──

@bp.get("/poster-qr")
def poster_qr():
    enlace = poster.enlace()
    return render_template("admin/poster_qr.html", enlace=enlace, qr=poster.svg(enlace),
                           advertencias=poster.advertencias(enlace))


@bp.get("/poster-qr/qr.svg")
def poster_qr_svg():
    return Response(poster.svg(poster.enlace()), mimetype="image/svg+xml",
                    headers={"Content-Disposition": "attachment; filename=qr_inicia_aqui.svg"})
