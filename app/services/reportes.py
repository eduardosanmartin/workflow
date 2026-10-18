"""Reportería para el Gestor y el Administrador (RF-18).

Cada reporte se arma con los datos de un proceso, cargados con pocas consultas y agregados
en memoria: un proceso de matrícula tiene a lo sumo unos miles de expedientes. Un reporte es
un diccionario con indicadores y tablas de forma uniforme, que la misma plantilla muestra y
el mismo código exporta a CSV o Excel.
"""

import csv
import io
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime

import openpyxl
from openpyxl.styles import Font
from peewee import JOIN, fn

from app import models as m
from app.services import auditoria, tiempo
from app.services import workflow as wf
from app.services.inicio import numero_visible

ROLES = ("ADMIN", "GESTOR")

RUTAS = {"DIRECTA": "Directa", "CONSULTAS": "Con consultas", "FLUJO": "Flujo alternativo"}
DIRECTA, CON_CONSULTAS = RUTAS["DIRECTA"], RUTAS["CONSULTAS"]

ESTADOS = {
    wf.EN_CURSO: "En curso", wf.ABANDONO: "Abandonó", wf.PROCESO_PENDIENTE: "Proceso pendiente",
    wf.MATRICULADO: "Matriculado",
}
CON_OBSERVACIONES = "Matriculado con observaciones"
ORIGENES = {"CARGA_MANUAL": "Carga manual", "CARGA_MASIVA": "Carga masiva"}
VIAS = {"QR": "QR", "STAFF": "Staff"}

# Filas que se muestran en pantalla por tabla; la exportación las incluye todas
LIMITE_PANTALLA = 500


def puede_ver(usuario) -> bool:
    return usuario is not None and usuario.rol in ROLES


# ── Celdas ──────────────────────────────────────────────────────────

class Duracion:
    """Se muestra como '1 h 05 min' y se exporta en minutos, para poder operar en Excel."""

    def __init__(self, segundos):
        self.segundos = segundos

    def __str__(self):
        return tiempo.duracion(self.segundos)

    def __eq__(self, otro):
        return str(self) == str(otro)

    def __repr__(self):
        return f"Duracion({str(self)!r})"


class Enlace:
    """Texto con enlace a la ficha del postulante."""

    def __init__(self, texto, postulante_id):
        self.texto = texto
        self.postulante_id = postulante_id

    def __str__(self):
        return self.texto

    def __eq__(self, otro):
        return str(self) == str(otro)

    def __repr__(self):
        return f"Enlace({self.texto!r})"


def _porcentaje(parte, total) -> str:
    return f"{round(100 * parte / total)} %" if total else "—"


def _estadisticas(segundos) -> list:
    """Promedio, mediana y máximo, o guiones si no hay datos."""
    if not segundos:
        return ["—", "—", "—"]
    return [Duracion(statistics.fmean(segundos)), Duracion(statistics.median(segundos)), Duracion(max(segundos))]


def _tabla(titulo, columnas, filas, vacio="Sin datos para los filtros elegidos.", nota=None, lista=False) -> dict:
    """`lista`: una fila por postulante o caso, y la pantalla muestra cuántas son."""
    return {"titulo": titulo, "columnas": columnas, "filas": filas, "vacio": vacio, "nota": nota, "lista": lista}


# ── Filtros ─────────────────────────────────────────────────────────

@dataclass
class Filtros:
    proceso: m.Proceso
    desde: date = None
    hasta: date = None
    carrera: str = None  # código, en cualquiera de las tres preferencias
    fase_id: int = None
    ruta: str = None  # clave de RUTAS
    errores: list = field(default_factory=list)

    @property
    def con_fechas(self) -> bool:
        return bool(self.desde or self.hasta)

    def incluye_dia(self, momento) -> bool:
        """Si `momento` cae entre las fechas elegidas (hora de Chile). Sin fechas, siempre."""
        if not self.con_fechas:
            return True
        if momento is None:
            return False
        dia = momento.astimezone(tiempo.ZONA).date()
        return (self.desde is None or dia >= self.desde) and (self.hasta is None or dia <= self.hasta)

    def como_texto(self) -> list:
        """Filtros aplicados, para la cabecera de la exportación."""
        textos = [f"Proceso: {self.proceso.nombre}"]
        if self.desde or self.hasta:
            textos.append(f"Fechas: {self.desde or '…'} a {self.hasta or '…'}")
        if self.carrera:
            carrera = m.Carrera.get_or_none(m.Carrera.codigo == self.carrera)
            textos.append(f"Carrera: {carrera.nombre if carrera else self.carrera}")
        if self.fase_id:
            fase = m.Fase.get_or_none(m.Fase.id == self.fase_id)
            textos.append(f"Fase: {fase.nombre if fase else self.fase_id}")
        if self.ruta:
            textos.append(f"Ruta: {RUTAS[self.ruta]}")
        return textos

    def como_dict(self) -> dict:
        return {"proceso": self.proceso.id, "desde": self.desde and self.desde.isoformat(),
                "hasta": self.hasta and self.hasta.isoformat(), "carrera": self.carrera,
                "fase": self.fase_id, "ruta": self.ruta}


def procesos():
    """Procesos con reportes: todos menos los borradores, el activo primero."""
    return list(m.Proceso.select().where(m.Proceso.estado != "BORRADOR")
                .order_by((m.Proceso.estado == "ACTIVO").desc(), m.Proceso.fecha_inicio.desc(), m.Proceso.id.desc()))


def _fecha(valor, etiqueta, errores):
    valor = (valor or "").strip()
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        errores.append(f"La fecha «{etiqueta}» no es válida.")
        return None


def leer_filtros(args) -> Filtros | None:
    """Filtros desde la URL. None si no hay ningún proceso con reportes."""
    disponibles = procesos()
    if not disponibles:
        return None
    proceso = disponibles[0]
    if (args.get("proceso") or "").isdigit():
        proceso = next((p for p in disponibles if p.id == int(args["proceso"])), proceso)
    errores = []
    filtros = Filtros(proceso, _fecha(args.get("desde"), "desde", errores), _fecha(args.get("hasta"), "hasta", errores),
                      errores=errores)
    if filtros.desde and filtros.hasta and filtros.desde > filtros.hasta:
        errores.append("La fecha «desde» es posterior a «hasta».")
        filtros.desde = filtros.hasta = None
    carrera = (args.get("carrera") or "").strip().upper()
    if carrera and m.Carrera.select().where(m.Carrera.codigo == carrera).exists():
        filtros.carrera = carrera
    fase = args.get("fase") or ""
    if fase.isdigit() and m.Fase.select().where((m.Fase.id == int(fase)) & (m.Fase.proceso == proceso.id)).exists():
        filtros.fase_id = int(fase)
    if args.get("ruta") in RUTAS:
        filtros.ruta = args["ruta"]
    return filtros


# ── Datos del proceso ───────────────────────────────────────────────

class _Datos:
    """Expedientes del proceso (con sus atenciones en orden) que cumplen los filtros de
    carrera y tipo de ruta. Las fechas las aplica cada reporte, según qué mide."""

    def __init__(self, filtros: Filtros, ahora=None):
        self.filtros = filtros
        self.proceso = filtros.proceso
        self.ahora = ahora or m.ahora()
        self.fases = {f.id: f for f in m.Fase.select().where(m.Fase.proceso == self.proceso.id).order_by(m.Fase.orden)}
        self.flujos = {f.id: f.nombre for f in
                       m.FlujoAlternativo.select().where(m.FlujoAlternativo.proceso == self.proceso.id)}
        self.usuarios = {u.id: u.nombre for u in m.Usuario.select(m.Usuario.id, m.Usuario.nombre)}

        carrera1 = m.Carrera.alias()
        preferencia1 = m.PostulanteCarrera.alias()
        e = m.Expediente
        filas = (
            e.select(e.id, e.numero_atencion, e.creado_en, e.estado, e.con_observaciones, e.matriculado_en,
                     e.tipo_cierre, e.via_inicio, e.postulante.alias("postulante_id"),
                     e.iniciado_por.alias("iniciado_por_id"), e.flujo_alternativo.alias("flujo_alternativo_id"),
                     e.fase_cierre.alias("fase_cierre_id"), e.fase_detenida.alias("fase_detenida_id"),
                     m.Postulante.nombres, m.Postulante.apellidos, m.Postulante.rut, carrera1.nombre.alias("carrera1"))
            .join(m.Postulante)
            .join(preferencia1, JOIN.LEFT_OUTER,
                  on=((preferencia1.postulante == m.Postulante.id) & (preferencia1.preferencia == 1)))
            .join(carrera1, JOIN.LEFT_OUTER, on=(preferencia1.carrera == carrera1.id))
            .where(m.Expediente.proceso == self.proceso.id)
            .order_by(m.Expediente.numero_atencion)
        )
        if filtros.carrera:
            con_carrera = (m.PostulanteCarrera.select(m.PostulanteCarrera.postulante)
                           .join(m.Carrera).where(m.Carrera.codigo == filtros.carrera))
            filas = filas.where(m.Expediente.postulante.in_(con_carrera))
        self.expedientes = list(filas.namedtuples())

        self.rutas = {e.id: [] for e in self.expedientes}
        a = m.Atencion
        atenciones = (  # tuplas y no modelos: son muchas y solo se leen
            a.select(a.id, a.expediente.alias("expediente_id"), a.fase.alias("fase_id"), a.estado,
                     a.flujo_alternativo.alias("flujo_alternativo_id"), a.en_espera_desde, a.inicio_atencion,
                     a.fin_atencion, a.atendido_por.alias("atendido_por_id"), a.sin_tiempo_atencion, a.detalle_cierre)
            .join(m.Expediente).where(m.Expediente.proceso == self.proceso.id)
            .order_by(a.expediente, a.secuencia).namedtuples()
        )
        for atencion in atenciones:
            if atencion.expediente_id in self.rutas:
                self.rutas[atencion.expediente_id].append(atencion)

        if filtros.ruta:
            buscada = RUTAS[filtros.ruta]
            self.expedientes = [e for e in self.expedientes if self._tipo_ruta(e) == buscada]
            self.rutas = {e.id: self.rutas[e.id] for e in self.expedientes}
        self.por_id = {e.id: e for e in self.expedientes}

    # ── Ayudas ──

    def nombre_fase(self, fase_id):
        fase = self.fases.get(fase_id)
        return fase.nombre if fase else "—"

    def numero(self, expediente):
        return numero_visible(self.proceso, expediente.numero_atencion)

    def postulante(self, expediente):
        return Enlace(f"{expediente.nombres} {expediente.apellidos}", expediente.postulante_id)

    def cerradas(self, expediente):
        return [a for a in self.rutas[expediente.id] if a.estado != wf.PLANIFICADA]

    def _tipo_ruta(self, expediente):
        """Flujo alternativo (si está en uno), con consultas o directa. Clasifica cada expediente
        una sola vez: el que volvió al flujo principal cuenta como ruta con consultas."""
        if expediente.flujo_alternativo_id:
            return RUTAS["FLUJO"]
        if any(self.fases[a.fase_id].tipo == wf.CONSULTA for a in self.rutas[expediente.id]):
            return CON_CONSULTAS
        return DIRECTA

    def tipo_ruta(self, expediente):
        """Como `_tipo_ruta`, pero con el nombre del flujo alternativo."""
        if expediente.flujo_alternativo_id:
            return self.flujos[expediente.flujo_alternativo_id]
        return self._tipo_ruta(expediente)

    def estado(self, expediente):
        if expediente.estado == wf.MATRICULADO:
            return CON_OBSERVACIONES if expediente.con_observaciones else ESTADOS[wf.MATRICULADO]
        texto = ESTADOS[expediente.estado]
        if expediente.estado == wf.PROCESO_PENDIENTE and self.hubo_abandono(expediente):
            texto += " (abandonó)"
        return texto

    def hubo_abandono(self, expediente):
        cerradas = self.cerradas(expediente)
        return bool(cerradas) and cerradas[-1].estado == wf.ABANDONADA

    def fase_del_estado(self, expediente):
        """Fase actual (en curso), del abandono, en que quedó detenido o de cierre."""
        if expediente.estado == wf.MATRICULADO:
            return expediente.fase_cierre_id
        if expediente.estado == wf.PROCESO_PENDIENTE:
            return expediente.fase_detenida_id
        cerradas = self.cerradas(expediente)
        return cerradas[-1].fase_id if cerradas else None

    def atendidas(self):
        """Atenciones terminadas (completadas o interrumpidas por una derivación), con su expediente."""
        for expediente in self.expedientes:
            for atencion in self.rutas[expediente.id]:
                if (atencion.estado in (*wf.ESTADOS_COMPLETOS, wf.DERIVADA) and atencion.inicio_atencion
                        and atencion.fin_atencion):
                    yield expediente, atencion


# ── Reportes ────────────────────────────────────────────────────────

def _estado(d: _Datos) -> dict:
    expedientes = [e for e in d.expedientes if d.filtros.incluye_dia(e.creado_en)]
    if d.filtros.fase_id:
        expedientes = [e for e in expedientes if d.fase_del_estado(e) == d.filtros.fase_id]
    conteo = {estado: 0 for estado in ESTADOS}
    observados = abandonos = 0
    cierres = {}
    detenidos = {}
    filas = []
    for e in expedientes:
        conteo[e.estado] += 1
        if e.estado == wf.MATRICULADO:
            observados += e.con_observaciones
            tipo = ("Cierre digital (MNP)" if e.tipo_cierre == wf.DIGITAL
                    else f"Cierre manual · {d.flujos.get(e.flujo_alternativo_id, '—')}")
            cierres[tipo] = cierres.get(tipo, 0) + 1
        if e.estado == wf.PROCESO_PENDIENTE:
            clave = d.nombre_fase(e.fase_detenida_id)
            detenidos.setdefault(clave, [0, 0])
            detenidos[clave][0] += 1
            if d.hubo_abandono(e):
                detenidos[clave][1] += 1
                abandonos += 1
        filas.append([d.numero(e), d.postulante(e), e.rut, e.carrera1 or "—", d.estado(e),
                      d.nombre_fase(d.fase_del_estado(e)), d.tipo_ruta(e), tiempo.fecha_hora_local(e.creado_en)])
    pendientes = conteo[wf.EN_CURSO] + conteo[wf.ABANDONO] + conteo[wf.PROCESO_PENDIENTE]
    indicadores = [
        ("Expedientes", len(expedientes)),
        ("Pendientes", pendientes),
        ("Matriculados", conteo[wf.MATRICULADO]),
        ("De ellos, con observaciones", observados),
    ]
    tablas = [
        _tabla("Pendientes", ["Situación", "Expedientes"], [
            ["En curso", conteo[wf.EN_CURSO]],
            ["Abandonó", conteo[wf.ABANDONO]],
            ["Proceso pendiente", conteo[wf.PROCESO_PENDIENTE]],
        ]),
        _tabla("Matriculados por tipo de cierre", ["Tipo de cierre", "Matriculados"],
               sorted(([k, v] for k, v in cierres.items()), key=lambda f: (not f[0].startswith("Cierre digital"), f[0])),
               vacio="Aún no hay matriculados."),
    ]
    if detenidos:
        orden = {f.nombre: f.orden for f in d.fases.values()}
        tablas.append(_tabla(
            "Proceso pendiente por fase en que quedó detenido", ["Fase", "Expedientes", "De ellos, abandonaron"],
            [[fase, n, a] for fase, (n, a) in sorted(detenidos.items(), key=lambda par: orden.get(par[0], 0))]))
    tablas.append(_tabla("Expedientes", ["Número", "Postulante", "RUT", "Carrera 1", "Estado",
                                         "Fase actual, de cierre o detenida", "Ruta", "Inicio"], filas, lista=True))
    return {"indicadores": indicadores, "tablas": tablas,
            "fechas": "Las fechas filtran por el día de inicio del expediente."}


def _espera(d: _Datos) -> dict:
    filas, resumen = [], {}
    activas = []
    for e in d.expedientes:
        cerradas = d.cerradas(e)
        for i, a in enumerate(cerradas):
            if a.estado in wf.ESTADOS_ACTIVOS and (not d.filtros.fase_id or a.fase_id == d.filtros.fase_id):
                activas.append((e, a, cerradas[i - 1].fase_id if i else None))
    orden = {f.id: f.orden for f in d.fases.values()}
    activas.sort(key=lambda t: (orden[t[1].fase_id], t[1].en_espera_desde or d.ahora))
    for e, a, anterior in activas:
        en_atencion = a.estado == wf.EN_ATENCION
        hasta = a.inicio_atencion if en_atencion else d.ahora
        espera = (hasta - a.en_espera_desde).total_seconds() if a.en_espera_desde else 0
        fila_resumen = resumen.setdefault(a.fase_id, [0, 0, []])
        fila_resumen[1 if en_atencion else 0] += 1
        if not en_atencion:
            fila_resumen[2].append(espera)
        filas.append([
            d.nombre_fase(a.fase_id), d.numero(e), d.postulante(e), "En atención" if en_atencion else "En espera",
            tiempo.hora_local(a.en_espera_desde), Duracion(espera),
            Duracion((d.ahora - a.inicio_atencion).total_seconds()) if en_atencion else "—",
            d.usuarios.get(a.atendido_por_id, "—") if en_atencion else "—",
            d.nombre_fase(anterior) if anterior else "—",
        ])
    tablas = [
        _tabla("Por fase", ["Fase", "En espera", "En atención", "Espera más larga"], [
            [d.nombre_fase(fase_id), n, at, Duracion(max(esperas)) if esperas else "—"]
            for fase_id, (n, at, esperas) in sorted(resumen.items(), key=lambda par: orden[par[0]])
        ], vacio="No hay postulantes en espera ni en atención."),
        _tabla("Postulantes en espera y en atención",
               ["Fase", "Número", "Postulante", "Estado", "En espera desde", "Tiempo de espera",
                "En atención hace", "Atiende", "Viene de"], filas,
               vacio="No hay postulantes en espera ni en atención.", lista=True),
    ]
    en_espera = sum(r[0] for r in resumen.values())
    en_atencion = sum(r[1] for r in resumen.values())
    return {"indicadores": [("En espera", en_espera), ("En atención", en_atencion)], "tablas": tablas,
            "fechas": "Es una foto del momento: no se filtra por fecha.", "tiempo_real": True}


_COLUMNAS_TIEMPOS = ["Atenciones", "Sin tiempo de atención", "Espera promedio", "Espera mediana", "Espera máxima",
                     "Atención promedio", "Atención mediana", "Atención máxima"]


def _fila_tiempos(etiqueta, atenciones) -> list:
    esperas = [(a.inicio_atencion - a.en_espera_desde).total_seconds() for a in atenciones if a.en_espera_desde]
    duraciones = [(a.fin_atencion - a.inicio_atencion).total_seconds() for a in atenciones if not a.sin_tiempo_atencion]
    sin_tiempo = sum(1 for a in atenciones if a.sin_tiempo_atencion)
    return [etiqueta, len(atenciones), sin_tiempo, *_estadisticas(esperas), *_estadisticas(duraciones)]


def _atencion(d: _Datos) -> dict:
    atenciones = [a for _e, a in d.atendidas() if d.filtros.incluye_dia(a.fin_atencion)]
    if d.filtros.fase_id:
        atenciones = [a for a in atenciones if a.fase_id == d.filtros.fase_id]

    def agrupar(clave):
        grupos = {}
        for a in atenciones:
            grupos.setdefault(clave(a), []).append(a)
        return grupos

    orden = {f.id: f.orden for f in d.fases.values()}
    por_fase = agrupar(lambda a: a.fase_id)
    por_responsable = agrupar(lambda a: d.usuarios.get(a.atendido_por_id, "Postulante (QR)") if a.atendido_por_id
                              else "Sin responsable")
    por_dia = agrupar(lambda a: a.inicio_atencion.astimezone(tiempo.ZONA).date())
    por_hora = agrupar(lambda a: a.inicio_atencion.astimezone(tiempo.ZONA).hour)
    columnas = _COLUMNAS_TIEMPOS
    total = _fila_tiempos("Total", atenciones)
    nota = ("La espera va desde que el postulante llega a la fase hasta que lo atienden. Las atenciones finalizadas "
            "sin presionar «Atender» no cuentan para el tiempo de atención.")
    return {
        "indicadores": [("Atenciones", len(atenciones)), ("Espera mediana", total[4]), ("Atención mediana", total[7])],
        "tablas": [
            _tabla("Por fase", ["Fase", *columnas],
                   [_fila_tiempos(d.nombre_fase(f), por_fase[f]) for f in sorted(por_fase, key=orden.get)], nota=nota),
            _tabla("Por responsable", ["Responsable", *columnas],
                   [_fila_tiempos(r, por_responsable[r]) for r in sorted(por_responsable)]),
            _tabla("Por día", ["Día", *columnas],
                   [_fila_tiempos(dia.strftime("%d-%m-%Y"), por_dia[dia]) for dia in sorted(por_dia)]),
            _tabla("Por hora del día", ["Hora", *columnas],
                   [_fila_tiempos(f"{h:02d}:00 a {h:02d}:59", por_hora[h]) for h in sorted(por_hora)]),
        ],
        "fechas": "Las fechas filtran por el día en que terminó cada atención.",
    }


def _abandonos(d: _Datos) -> dict:
    casos = []
    for e in d.expedientes:
        cerradas = d.cerradas(e)
        for i, a in enumerate(cerradas):
            if a.estado != wf.ABANDONADA:
                continue
            if d.filtros.fase_id and a.fase_id != d.filtros.fase_id:
                continue
            if not d.filtros.incluye_dia(a.fin_atencion):
                continue
            espera = (a.fin_atencion - a.en_espera_desde).total_seconds() if a.en_espera_desde else None
            casos.append((e, a, espera, i < len(cerradas) - 1))
    reactivados = sum(1 for c in casos if c[3])
    orden = {f.id: f.orden for f in d.fases.values()}

    def resumen(clave, ordenar):
        grupos = {}
        for caso in casos:
            grupos.setdefault(clave(caso), []).append(caso)
        return [(k, grupos[k]) for k in sorted(grupos, key=ordenar)]

    def fila(etiqueta, grupo):
        esperas = [c[2] for c in grupo if c[2] is not None]
        return [etiqueta, len(grupo), sum(1 for c in grupo if c[3]), *_estadisticas(esperas)]

    columnas = ["Abandonos", "Reactivados", "Espera promedio", "Espera mediana", "Espera máxima"]
    esperas = [c[2] for c in casos if c[2] is not None]
    return {
        "indicadores": [("Abandonos", len(casos)), ("Reactivados", reactivados),
                        ("Espera promedio antes de abandonar", _estadisticas(esperas)[0])],
        "tablas": [
            _tabla("Por fase", ["Fase", *columnas],
                   [fila(d.nombre_fase(k), g) for k, g in resumen(lambda c: c[1].fase_id, orden.get)],
                   vacio="No hay abandonos."),
            _tabla("Por día", ["Día", *columnas],
                   [fila(k.strftime("%d-%m-%Y"), g) for k, g in
                    resumen(lambda c: c[1].fin_atencion.astimezone(tiempo.ZONA).date(), lambda k: k)],
                   vacio="No hay abandonos."),
            _tabla("Detalle", ["Fecha", "Número", "Postulante", "Fase", "Espera antes de abandonar", "Reactivado",
                               "Estado actual"],
                   [[tiempo.fecha_hora_local(a.fin_atencion), d.numero(e), d.postulante(e), d.nombre_fase(a.fase_id),
                     Duracion(espera) if espera is not None else "—", "Sí" if reactivado else "No", d.estado(e)]
                    for e, a, espera, reactivado in casos], vacio="No hay abandonos.", lista=True),
        ],
        "fechas": "Las fechas filtran por el día del abandono; la fase, por la fase en que abandonó.",
    }


def _matriculados_filtrados(d: _Datos):
    return [e for e in d.expedientes if e.estado == wf.MATRICULADO and e.matriculado_en
            and d.filtros.incluye_dia(e.matriculado_en)]


def _recorrido(d: _Datos) -> dict:
    grupos = {}
    for e in _matriculados_filtrados(d):
        grupos.setdefault(d.tipo_ruta(e), []).append((e.matriculado_en - e.creado_en).total_seconds())
    orden = {DIRECTA: 0, CON_CONSULTAS: 1}
    filas = [[ruta, len(t), *_estadisticas(t)] for ruta, t in sorted(grupos.items(), key=lambda p: (orden.get(p[0], 2), p[0]))]
    todos = [s for t in grupos.values() for s in t]
    return {
        "indicadores": [("Matriculados", len(todos)), ("Tiempo total mediano", _estadisticas(todos)[1])],
        "tablas": [_tabla("Tiempo total del recorrido por tipo de ruta",
                          ["Ruta", "Matriculados", "Promedio", "Mediana", "Máximo"], filas,
                          vacio="Aún no hay matriculados.",
                          nota="Desde la creación del expediente hasta la matrícula. Los flujos alternativos "
                               "terminan en un cierre manual.")],
        "fechas": "Las fechas filtran por el día de la matrícula.",
    }


def _rutas(d: _Datos) -> dict:
    expedientes = [e for e in d.expedientes if d.filtros.incluye_dia(e.creado_en)]
    ids = {e.id for e in expedientes}
    por_ruta = {}
    for e in expedientes:
        por_ruta[d.tipo_ruta(e)] = por_ruta.get(d.tipo_ruta(e), 0) + 1
    visitas, derivaciones = {}, {}
    for e in expedientes:
        cerradas = d.cerradas(e)
        for i, a in enumerate(cerradas):
            if d.fases[a.fase_id].tipo == wf.CONSULTA and a.estado in wf.ESTADOS_COMPLETOS:
                visitas.setdefault(a.fase_id, [0, set()])
                visitas[a.fase_id][0] += 1
                visitas[a.fase_id][1].add(e.id)
            if i + 1 < len(cerradas) and a.estado in (*wf.ESTADOS_COMPLETOS, wf.DERIVADA):
                par = (a.fase_id, cerradas[i + 1].fase_id)
                derivaciones[par] = derivaciones.get(par, 0) + 1
    total = len(ids)
    orden = {DIRECTA: 0, CON_CONSULTAS: 1}
    return {
        "indicadores": [("Expedientes", total), ("Con ruta directa", _porcentaje(por_ruta.get(DIRECTA, 0), total))],
        "tablas": [
            _tabla("Tipo de ruta", ["Ruta", "Expedientes", "Porcentaje"],
                   [[r, n, _porcentaje(n, total)] for r, n in sorted(por_ruta.items(), key=lambda p: (orden.get(p[0], 2), p[0]))],
                   nota="Directa: sin fases de consulta. Con consultas: pasó o pasará por al menos una."),
            _tabla("Fases de consulta más visitadas", ["Fase", "Atenciones completadas", "Postulantes"],
                   sorted(([d.nombre_fase(f), n, len(p)] for f, (n, p) in visitas.items()), key=lambda f: (-f[1], f[0]))),
            _tabla("Derivaciones entre fases", ["Desde", "Hacia", "Derivaciones"],
                   sorted(([d.nombre_fase(o), d.nombre_fase(h), n] for (o, h), n in derivaciones.items()),
                          key=lambda f: (-f[2], f[0], f[1]))),
        ],
        "fechas": "Las fechas filtran por el día de inicio del expediente.",
    }


def _flujos(d: _Datos) -> dict:
    expedientes = [e for e in d.expedientes if d.filtros.incluye_dia(e.creado_en)]
    resumen = {flujo_id: {"derivados": 0, "devueltos": 0, "cierres": 0} for flujo_id in d.flujos}
    entradas, cierres = {}, []
    for e in expedientes:
        cerradas = d.cerradas(e)
        vistos = set()
        for i, a in enumerate(cerradas):
            flujo_id = a.flujo_alternativo_id
            if flujo_id is None or (i and cerradas[i - 1].flujo_alternativo_id == flujo_id):
                continue
            # Entrada al flujo: desde qué fase del flujo principal y a qué fase del flujo
            vistos.add(flujo_id)
            clave = (flujo_id, cerradas[i - 1].fase_id if i else None, a.fase_id)
            entradas[clave] = entradas.get(clave, 0) + 1
            if any(b.flujo_alternativo_id is None for b in cerradas[i + 1:]):
                resumen[flujo_id]["devueltos"] += 1
        for flujo_id in vistos:
            resumen[flujo_id]["derivados"] += 1
        if e.tipo_cierre == wf.MANUAL and e.flujo_alternativo_id:
            resumen[e.flujo_alternativo_id]["cierres"] += 1
            final = next((a for a in reversed(cerradas) if a.fase_id == e.fase_cierre_id), None)
            cierres.append((e, final))
    cierres.sort(key=lambda par: par[0].matriculado_en)
    por_responsable_dia = {}
    for e, final in cierres:
        clave = (d.usuarios.get(final.atendido_por_id, "—") if final else "—",
                 e.matriculado_en.astimezone(tiempo.ZONA).date())
        por_responsable_dia[clave] = por_responsable_dia.get(clave, 0) + 1
    return {
        "indicadores": [("Derivados a flujos alternativos", sum(r["derivados"] for r in resumen.values())),
                        ("Cierres manuales", len(cierres))],
        "tablas": [
            _tabla("Por flujo alternativo", ["Flujo", "Derivados", "Devueltos al flujo principal", "Cierres manuales"],
                   [[d.flujos[f], r["derivados"], r["devueltos"], r["cierres"]] for f, r in
                    sorted(resumen.items(), key=lambda p: d.flujos[p[0]])], vacio="El proceso no tiene flujos alternativos."),
            _tabla("Entradas a los flujos", ["Flujo", "Desde (flujo principal)", "Fase de entrada", "Postulantes"],
                   sorted(([d.flujos[f], d.nombre_fase(o) if o else "Inicio", d.nombre_fase(fe), n]
                           for (f, o, fe), n in entradas.items()), key=lambda f: (f[0], -f[3]))),
            _tabla("Cierres manuales por responsable y día", ["Responsable", "Día", "Cierres manuales"],
                   [[r, dia.strftime("%d-%m-%Y"), n] for (r, dia), n in sorted(por_responsable_dia.items())]),
            _tabla("Detalle de los cierres manuales",
                   ["Fecha", "Número", "Postulante", "Flujo", "Fase final", "Responsable", "Detalle del cierre"],
                   [[tiempo.fecha_hora_local(e.matriculado_en), d.numero(e), d.postulante(e),
                     d.flujos[e.flujo_alternativo_id], d.nombre_fase(e.fase_cierre_id),
                     d.usuarios.get(final.atendido_por_id, "—") if final else "—",
                     (final.detalle_cierre if final else None) or "—"] for e, final in cierres], lista=True),
        ],
        "fechas": "Las fechas filtran por el día de inicio del expediente.",
    }


def _carreras(d: _Datos) -> dict:
    expedientes = [e for e in d.expedientes if d.filtros.incluye_dia(e.creado_en)]
    grupos = {}
    for e in expedientes:
        fila = grupos.setdefault(e.carrera1 or "Sin carrera", [0, 0, 0, 0])
        fila[0] += 1
        if e.estado == wf.MATRICULADO:
            fila[1] += 1
            fila[2] += e.con_observaciones
        else:
            fila[3] += 1
    filas = [[c, total, mat, obs, pend, _porcentaje(mat, total)]
             for c, (total, mat, obs, pend) in sorted(grupos.items())]
    return {
        "indicadores": [("Carreras", len(grupos))],
        "tablas": [_tabla("Por carrera de primera preferencia",
                          ["Carrera", "Expedientes", "Matriculados", "De ellos, con observaciones", "Pendientes",
                           "Matriculados (%)"], filas)],
        "fechas": "Las fechas filtran por el día de inicio del expediente. El filtro de carrera considera "
                  "las tres preferencias.",
    }


def _inicios(d: _Datos) -> dict:
    expedientes = [e for e in d.expedientes if d.filtros.incluye_dia(e.creado_en)]
    por_dia, por_hora = {}, {}
    for e in expedientes:
        local = e.creado_en.astimezone(tiempo.ZONA)
        for grupos, clave in ((por_dia, local.date()), (por_hora, local.hour)):
            fila = grupos.setdefault(clave, {"QR": 0, "STAFF": 0})
            fila[e.via_inicio] += 1
    qr = sum(1 for e in expedientes if e.via_inicio == "QR")
    columnas = ["QR", "Staff", "Total"]
    return {
        "indicadores": [("Inicios", len(expedientes)), ("Por QR", qr), ("Por staff", len(expedientes) - qr)],
        "tablas": [
            _tabla("Por día", ["Día", *columnas],
                   [[k.strftime("%d-%m-%Y"), v["QR"], v["STAFF"], v["QR"] + v["STAFF"]] for k, v in sorted(por_dia.items())]),
            _tabla("Por hora del día", ["Hora", *columnas],
                   [[f"{k:02d}:00 a {k:02d}:59", v["QR"], v["STAFF"], v["QR"] + v["STAFF"]]
                    for k, v in sorted(por_hora.items())]),
        ],
        "fechas": "Las fechas filtran por el día de inicio del expediente.",
    }


def _productividad(d: _Datos) -> dict:
    filas = {}

    def fila(usuario_id):
        return filas.setdefault(usuario_id, [0, 0, 0])

    for e in d.expedientes:
        cerradas = d.cerradas(e)
        for i, a in enumerate(cerradas):
            if not a.atendido_por_id or not a.fin_atencion or not d.filtros.incluye_dia(a.fin_atencion):
                continue
            if d.fases[a.fase_id].tipo == wf.INICIAL:
                continue  # su atención es el inicio de proceso, que se cuenta aparte
            if a.estado in wf.ESTADOS_COMPLETOS:
                fila(a.atendido_por_id)[0] += 1
            if a.estado in (*wf.ESTADOS_COMPLETOS, wf.DERIVADA) and i + 1 < len(cerradas):
                fila(a.atendido_por_id)[1] += 1
        if e.iniciado_por_id and d.filtros.incluye_dia(e.creado_en):
            fila(e.iniciado_por_id)[2] += 1
    return {
        "indicadores": [("Usuarios con actividad", len(filas))],
        "tablas": [_tabla("Por responsable", ["Responsable", "Atenciones", "Derivaciones", "Inicios de proceso"],
                          sorted(([d.usuarios.get(u, "—"), *v] for u, v in filas.items()), key=lambda f: f[0]),
                          nota="Atenciones: completadas en una fase distinta de la inicial. Derivaciones: veces que "
                               "pasó a un postulante a otra fase. Inicios: procesos iniciados por staff.")],
        "fechas": "Las fechas filtran por el día de cada atención o inicio.",
    }


def _sin_iniciar(d: _Datos) -> dict:
    con_expediente = m.Expediente.select(m.Expediente.postulante).where(m.Expediente.proceso == d.proceso.id)
    carrera1 = m.Carrera.alias()
    preferencia1 = m.PostulanteCarrera.alias()
    consulta = (
        m.Postulante.select(m.Postulante, carrera1.nombre.alias("carrera1"))
        .join(preferencia1, JOIN.LEFT_OUTER,
              on=((preferencia1.postulante == m.Postulante.id) & (preferencia1.preferencia == 1)))
        .join(carrera1, JOIN.LEFT_OUTER, on=(preferencia1.carrera == carrera1.id))
        .where(m.Postulante.activo & m.Postulante.origen.in_(list(ORIGENES))
               & m.Postulante.id.not_in(con_expediente))
        .order_by(m.Postulante.apellidos, m.Postulante.nombres)
    )
    if d.filtros.carrera:
        con_carrera = (m.PostulanteCarrera.select(m.PostulanteCarrera.postulante)
                       .join(m.Carrera).where(m.Carrera.codigo == d.filtros.carrera))
        consulta = consulta.where(m.Postulante.id.in_(con_carrera))
    filas = [[p.rut, Enlace(f"{p.nombres} {p.apellidos}", p.id), p.carrera1 or "—", ORIGENES[p.origen],
              tiempo.fecha_hora_local(p.creado_en)] for p in consulta.objects()]
    return {
        "indicadores": [("Sin iniciar", len(filas))],
        "tablas": [_tabla("Postulantes cargados que aún no inician el proceso",
                          ["RUT", "Postulante", "Carrera 1", "Origen", "Registrado"], filas,
                          vacio="Todos los postulantes cargados iniciaron el proceso.", lista=True)],
        "fechas": "No se filtra por fecha ni por tipo de ruta.",
    }


def _correos(d: _Datos) -> dict:
    from app.services.correo import TIPOS  # correo importa servicios que a su vez cargan este módulo

    del_proceso = m.Expediente.select(m.Expediente.id).where(m.Expediente.proceso == d.proceso.id)
    ultimo = (m.EnvioCorreo.select(fn.MAX(m.EnvioCorreo.id))
              .where(m.EnvioCorreo.expediente.in_(del_proceso)).group_by(m.EnvioCorreo.expediente))
    envios = (m.EnvioCorreo.select().where(m.EnvioCorreo.id.in_(ultimo) & (m.EnvioCorreo.estado == "ERROR"))
              .order_by(m.EnvioCorreo.creado_en.desc()))
    filas = []
    for envio in envios:
        e = d.por_id.get(envio.expediente_id)  # None: no cumple los filtros de carrera o ruta
        if e is None or not d.filtros.incluye_dia(envio.creado_en):
            continue
        filas.append([tiempo.fecha_hora_local(envio.creado_en), d.numero(e), d.postulante(e), envio.destinatario,
                      TIPOS.get(envio.tipo, envio.tipo), envio.detalle_error or "—"])
    return {
        "indicadores": [("Envíos fallidos", len(filas))],
        "tablas": [_tabla("Correos con envío fallido", ["Fecha", "Número", "Postulante", "Destinatario", "Tipo", "Error"],
                          filas, vacio="No hay correos con envío fallido.", lista=True,
                          nota="Se muestra el último envío de cada postulante, si falló. Para reintentarlo, abra la "
                               "ficha, corrija el correo si es necesario y use «Reenviar enlace».")],
        "fechas": "Las fechas filtran por el día del envío.",
    }


@dataclass(frozen=True)
class Reporte:
    slug: str
    titulo: str
    descripcion: str
    funcion: object
    filtros: tuple = ("fechas", "carrera", "ruta")


REPORTES = {r.slug: r for r in (
    Reporte("estado", "Estado de los postulantes",
            "Pendientes, matriculados y matriculados con observaciones, por tipo de cierre. Al cierre, la fase en "
            "que quedó detenido cada uno.", _estado, ("fechas", "carrera", "fase", "ruta")),
    Reporte("espera", "Postulantes en espera",
            "En tiempo real, por fase: número de atención, tiempo esperando y fase de la que vienen.", _espera,
            ("carrera", "fase", "ruta")),
    Reporte("atencion", "Atención por fase",
            "Cantidad de atenciones y tiempos de espera y de atención por fase, responsable, día y hora.", _atencion,
            ("fechas", "carrera", "fase", "ruta")),
    Reporte("abandonos", "Abandonos",
            "Por fase y por día, con el tiempo de espera antes del abandono y cuántos se reactivaron.", _abandonos,
            ("fechas", "carrera", "fase", "ruta")),
    Reporte("recorrido", "Tiempo total del recorrido",
            "Desde el inicio hasta la matrícula: ruta directa, con consultas y cada flujo alternativo.", _recorrido),
    Reporte("rutas", "Rutas y derivaciones",
            "Porcentaje con ruta directa, fases de consulta más visitadas y derivaciones entre fases.", _rutas),
    Reporte("flujos", "Flujos alternativos",
            "Derivados, entradas, devoluciones al flujo principal y detalle de los cierres manuales.", _flujos,
            ("fechas", "carrera")),
    Reporte("carreras", "Por carrera",
            "Matriculados y pendientes por carrera de primera preferencia.", _carreras, ("fechas", "carrera", "ruta")),
    Reporte("inicios", "Inicios de proceso", "Por vía (QR o staff), por día y por hora.", _inicios),
    Reporte("productividad", "Productividad por responsable",
            "Atenciones, derivaciones e inicios de proceso por usuario.", _productividad),
    Reporte("sin-iniciar", "Cargados sin iniciar",
            "Postulantes registrados que aún no inician el proceso.", _sin_iniciar, ("carrera",)),
    Reporte("correos", "Correos fallidos", "Último envío fallido de cada postulante, para reintentarlo.", _correos,
            ("fechas", "carrera")),
)}


def generar(slug, filtros: Filtros, ahora=None) -> dict:
    reporte = REPORTES[slug]
    datos = _Datos(filtros, ahora)
    resultado = reporte.funcion(datos)
    return {"reporte": reporte, "generado": tiempo.fecha_hora_local(datos.ahora), **resultado}


# ── Exportación ─────────────────────────────────────────────────────

_PELIGROSOS = ("=", "+", "-", "@", "\t", "\r")


def _valor(celda):
    """Valor exportable: duraciones en minutos y sin fórmulas (inyección en planillas)."""
    if isinstance(celda, Duracion):
        return round(celda.segundos / 60, 1)
    if isinstance(celda, (int, float)):
        return celda
    texto = str(celda)
    return "'" + texto if texto.startswith(_PELIGROSOS) else texto


def _columnas(tabla) -> list:
    columnas = list(tabla["columnas"])
    for i in range(len(columnas)):
        if any(isinstance(fila[i], Duracion) for fila in tabla["filas"]):
            columnas[i] += " (min)"
    return columnas


def _cabecera(resultado, filtros) -> list:
    return [[resultado["reporte"].titulo], *[[t] for t in filtros.como_texto()],
            [f"Generado: {resultado['generado']}"]]


def exportar(slug, filtros: Filtros, formato, usuario, ahora=None) -> tuple[bytes, str, str]:
    """(contenido, tipo MIME, nombre de archivo). Queda auditado (RF-24)."""
    resultado = generar(slug, filtros, ahora)
    nombre = f"reporte_{slug}_{filtros.proceso.codigo}_{datetime.now(tiempo.ZONA):%Y%m%d_%H%M}"
    if formato == "csv":
        salida = io.StringIO()
        salida.write("﻿")  # para que Excel lo abra en UTF-8
        escritor = csv.writer(salida, delimiter=";")
        escritor.writerows(_cabecera(resultado, filtros))
        for tabla in resultado["tablas"]:
            escritor.writerow([])
            escritor.writerow([tabla["titulo"]])
            escritor.writerow(_columnas(tabla))
            escritor.writerows([_valor(c) for c in fila] for fila in tabla["filas"])
        contenido, tipo, nombre = salida.getvalue().encode("utf-8"), "text/csv", nombre + ".csv"
    else:
        libro = openpyxl.Workbook()
        libro.active.title = "Resumen"
        hoja = libro.active
        for fila in _cabecera(resultado, filtros):
            hoja.append(fila)
        hoja["A1"].font = Font(bold=True, size=14)
        hoja.append([])
        for etiqueta, valor in resultado["indicadores"]:
            hoja.append([etiqueta, _valor(valor)])
        hoja.column_dimensions["A"].width = 40
        usados = {"Resumen"}
        for tabla in resultado["tablas"]:
            titulo = "".join(c for c in tabla["titulo"] if c not in "[]:*?/\\")[:31]
            base, n = titulo, 2
            while titulo in usados:
                titulo = f"{base[:28]} {n}"
                n += 1
            usados.add(titulo)
            hoja = libro.create_sheet(titulo)
            hoja.append(_columnas(tabla))
            for celda in hoja[1]:
                celda.font = Font(bold=True)
            for fila in tabla["filas"]:
                hoja.append([_valor(c) for c in fila])
            for i, columna in enumerate(tabla["columnas"], 1):
                hoja.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(12, min(40, len(columna) + 4))
            hoja.freeze_panes = "A2"
        salida = io.BytesIO()
        libro.save(salida)
        contenido = salida.getvalue()
        tipo, nombre = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", nombre + ".xlsx"
    auditoria.registrar("EXPORTAR_REPORTE", "proceso", filtros.proceso.id, usuario=usuario,
                        nuevo={"reporte": slug, "formato": formato, "filtros": filtros.como_dict()})
    return contenido, tipo, nombre
