"""Modelos Peewee de las tablas de la §8. Reflejan `migrations/`; no crean tablas."""

from datetime import datetime, timezone

from peewee import (
    BigAutoField,
    BooleanField,
    CharField,
    CompositeKey,
    DateField,
    DateTimeField,
    ForeignKeyField,
    IntegerField,
    Model,
    SmallIntegerField,
    TextField,
)
from playhouse.postgres_ext import BinaryJSONField

from app.db import db


def ahora():
    return datetime.now(timezone.utc)


class Base(Model):
    class Meta:
        database = db
        legacy_table_names = False


class Usuario(Base):
    email = CharField(max_length=254)
    nombre = CharField(max_length=200)
    rol = CharField(max_length=20)
    password_hash = CharField(max_length=100, null=True)
    must_change_password = BooleanField(default=True)
    puede_registrar_postulantes = BooleanField(default=False)
    google_sub = CharField(max_length=255, null=True)
    intentos_fallidos = IntegerField(default=0)
    bloqueado_hasta = DateTimeField(null=True)
    activo = BooleanField(default=True)
    creado_en = DateTimeField(default=ahora)


class Proceso(Base):
    codigo = CharField(max_length=30)
    nombre = CharField(max_length=200)
    descripcion = TextField(default="")
    fecha_inicio = DateField()
    fecha_fin = DateField()
    prefijo_atencion = CharField(max_length=10, default="M")
    enmascarar_datos_qr = BooleanField(default=True)
    estado = CharField(max_length=10, default="BORRADOR")
    ultimo_numero_atencion = IntegerField(default=0)
    fecha_cierre = DateTimeField(null=True)
    fecha_eliminacion = DateField()
    datos_eliminados_en = DateTimeField(null=True)
    creado_en = DateTimeField(default=ahora)


class Fase(Base):
    proceso = ForeignKeyField(Proceso, backref="fases", on_delete="CASCADE")
    identificador = CharField(max_length=20)
    nombre = CharField(max_length=100)
    descripcion = TextField(default="")
    tipo = CharField(max_length=15)
    orden = IntegerField()
    mostrar_en_qr = BooleanField(default=True)


class FlujoAlternativo(Base):
    proceso = ForeignKeyField(Proceso, backref="flujos_alternativos", on_delete="CASCADE")
    codigo = CharField(max_length=20)
    nombre = CharField(max_length=100)
    descripcion = TextField(default="")
    activo = BooleanField(default=True)


class FlujoAlternativoFase(Base):
    flujo_alternativo = ForeignKeyField(FlujoAlternativo, backref="fases", on_delete="CASCADE")
    posicion = IntegerField()
    fase = ForeignKeyField(Fase)

    class Meta:
        primary_key = CompositeKey("flujo_alternativo", "posicion")


class FaseResponsable(Base):
    fase = ForeignKeyField(Fase, backref="responsables", on_delete="CASCADE")
    usuario = ForeignKeyField(Usuario, backref="fases_asignadas", on_delete="CASCADE")

    class Meta:
        primary_key = CompositeKey("fase", "usuario")


class Carrera(Base):
    codigo = CharField(max_length=20)
    nombre = CharField(max_length=200)
    activo = BooleanField(default=True)


class CargaMasiva(Base):
    usuario = ForeignKeyField(Usuario)
    nombre_archivo = CharField(max_length=255)
    politica_existentes = CharField(max_length=15)
    total_filas = IntegerField(default=0)
    creados = IntegerField(default=0)
    actualizados = IntegerField(default=0)
    omitidos = IntegerField(default=0)
    con_error = IntegerField(default=0)
    creado_en = DateTimeField(default=ahora)


class Postulante(Base):
    rut = CharField(max_length=12)
    nombres = CharField(max_length=100)
    apellidos = CharField(max_length=100)
    telefono = CharField(max_length=20, null=True)
    correo_personal = CharField(max_length=254)
    origen = CharField(max_length=15)
    carga = ForeignKeyField(CargaMasiva, null=True, on_delete="SET NULL")
    activo = BooleanField(default=True)
    eliminar_en = DateField(null=True)
    creado_en = DateTimeField(default=ahora)
    actualizado_en = DateTimeField(default=ahora)


class PostulanteCarrera(Base):
    postulante = ForeignKeyField(Postulante, backref="carreras", on_delete="CASCADE")
    preferencia = SmallIntegerField()
    carrera = ForeignKeyField(Carrera)

    class Meta:
        primary_key = CompositeKey("postulante", "preferencia")


class Expediente(Base):
    proceso = ForeignKeyField(Proceso, backref="expedientes")
    postulante = ForeignKeyField(Postulante, backref="expedientes")
    via_inicio = CharField(max_length=5)
    iniciado_por = ForeignKeyField(Usuario, null=True, column_name="iniciado_por", on_delete="SET NULL")
    numero_atencion = IntegerField()
    token_seguimiento = CharField(max_length=64)
    creado_en = DateTimeField(default=ahora)
    cerrado_en = DateTimeField(null=True)
    estado = CharField(max_length=20, default="EN_CURSO")
    con_observaciones = BooleanField(default=False)
    matriculado_en = DateTimeField(null=True)
    flujo_alternativo = ForeignKeyField(FlujoAlternativo, null=True)
    tipo_cierre = CharField(max_length=10, null=True)
    fase_cierre = ForeignKeyField(Fase, null=True, column_name="fase_cierre_id")
    fase_detenida = ForeignKeyField(Fase, null=True, column_name="fase_detenida_id")


class Atencion(Base):
    expediente = ForeignKeyField(Expediente, backref="atenciones", on_delete="CASCADE")
    fase = ForeignKeyField(Fase)
    secuencia = IntegerField()
    estado = CharField(max_length=30)
    flujo_alternativo = ForeignKeyField(FlujoAlternativo, null=True)
    observacion = TextField(null=True)
    detalle_cierre = TextField(null=True)
    planificada_por = ForeignKeyField(Usuario, null=True, column_name="planificada_por", on_delete="SET NULL")
    derivada_desde = ForeignKeyField("self", null=True, on_delete="SET NULL")
    en_espera_desde = DateTimeField(null=True)
    inicio_atencion = DateTimeField(null=True)
    fin_atencion = DateTimeField(null=True)
    atendido_por = ForeignKeyField(Usuario, null=True, column_name="atendido_por", on_delete="SET NULL")
    sin_tiempo_atencion = BooleanField(default=False)
    version = IntegerField(default=1)


class EnvioCorreo(Base):
    expediente = ForeignKeyField(Expediente, backref="envios", on_delete="CASCADE")
    tipo = CharField(max_length=12)
    destinatario = CharField(max_length=254)
    estado = CharField(max_length=10)
    detalle_error = TextField(null=True)
    enviado_por = ForeignKeyField(Usuario, null=True, column_name="enviado_por", on_delete="SET NULL")
    creado_en = DateTimeField(default=ahora)


class ProcesoResumen(Base):
    proceso = ForeignKeyField(Proceso, on_delete="CASCADE")
    dimension = CharField(max_length=10)
    carrera = ForeignKeyField(Carrera, null=True)
    fase = ForeignKeyField(Fase, null=True)
    matriculados = IntegerField(default=0)
    matriculados_cierre_manual = IntegerField(default=0)
    proceso_pendiente = IntegerField(default=0)
    calculado_en = DateTimeField(default=ahora)


class Auditoria(Base):
    id = BigAutoField()
    fecha_hora = DateTimeField(default=ahora)
    usuario = ForeignKeyField(Usuario, null=True, on_delete="SET NULL")
    rol = CharField(max_length=20, null=True)
    actor = CharField(max_length=10)
    accion = CharField(max_length=50)
    entidad = CharField(max_length=30)
    entidad_id = IntegerField(null=True)
    valor_anterior = BinaryJSONField(null=True)
    valor_nuevo = BinaryJSONField(null=True)
    ip = CharField(max_length=45, null=True)
    user_agent = CharField(max_length=500, null=True)


TODOS = [
    Usuario, Proceso, Fase, FlujoAlternativo, FlujoAlternativoFase, FaseResponsable, Carrera,
    CargaMasiva, Postulante, PostulanteCarrera, Expediente, Atencion, EnvioCorreo,
    ProcesoResumen, Auditoria,
]
