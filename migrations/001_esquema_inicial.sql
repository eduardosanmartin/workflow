-- 001 · Esquema inicial (SPEC v0.8, §8)

-- ── Usuarios del staff (RF-22, RF-23) ──
CREATE TABLE usuario (
    id                          SERIAL PRIMARY KEY,
    email                       VARCHAR(254) NOT NULL,
    nombre                      VARCHAR(200) NOT NULL,
    rol                         VARCHAR(20)  NOT NULL CHECK (rol IN ('ADMIN', 'GESTOR', 'USUARIO_FASE')),
    password_hash               VARCHAR(100),
    must_change_password        BOOLEAN      NOT NULL DEFAULT true,
    puede_registrar_postulantes BOOLEAN      NOT NULL DEFAULT false,
    google_sub                  VARCHAR(255) UNIQUE,
    intentos_fallidos           INTEGER      NOT NULL DEFAULT 0,
    bloqueado_hasta             TIMESTAMPTZ,
    activo                      BOOLEAN      NOT NULL DEFAULT true,
    creado_en                   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX usuario_email_unico ON usuario (lower(email));

-- ── Procesos (RF-01, RF-19, RF-26) ──
CREATE TABLE proceso (
    id                     SERIAL PRIMARY KEY,
    codigo                 VARCHAR(30)  NOT NULL UNIQUE,
    nombre                 VARCHAR(200) NOT NULL,
    descripcion            TEXT         NOT NULL DEFAULT '',
    fecha_inicio           DATE         NOT NULL,
    fecha_fin              DATE         NOT NULL,
    prefijo_atencion       VARCHAR(10)  NOT NULL DEFAULT 'M',
    enmascarar_datos_qr    BOOLEAN      NOT NULL DEFAULT true,
    estado                 VARCHAR(10)  NOT NULL DEFAULT 'BORRADOR'
                           CHECK (estado IN ('BORRADOR', 'ACTIVO', 'CERRADO')),
    ultimo_numero_atencion INTEGER      NOT NULL DEFAULT 0,
    fecha_cierre           TIMESTAMPTZ,
    fecha_eliminacion      DATE         NOT NULL,
    datos_eliminados_en    TIMESTAMPTZ,
    creado_en              TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (fecha_fin >= fecha_inicio),
    CHECK (fecha_eliminacion >= fecha_fin)
);
-- Solo un proceso ACTIVO a la vez (RF-01)
CREATE UNIQUE INDEX proceso_un_solo_activo ON proceso ((true)) WHERE estado = 'ACTIVO';

-- ── Fases (RF-02, RF-03) ──
CREATE TABLE fase (
    id            SERIAL PRIMARY KEY,
    proceso_id    INTEGER      NOT NULL REFERENCES proceso (id) ON DELETE CASCADE,
    identificador VARCHAR(20)  NOT NULL,
    nombre        VARCHAR(100) NOT NULL,
    descripcion   TEXT         NOT NULL DEFAULT '',
    tipo          VARCHAR(15)  NOT NULL CHECK (tipo IN ('INICIAL', 'CONSULTA', 'CIERRE_DIGITAL')),
    orden         INTEGER      NOT NULL,
    mostrar_en_qr BOOLEAN      NOT NULL DEFAULT true,
    UNIQUE (proceso_id, identificador),
    -- diferible para poder reordenar fases en una sola transacción
    CONSTRAINT fase_orden_unico UNIQUE (proceso_id, orden) DEFERRABLE INITIALLY DEFERRED
);
CREATE UNIQUE INDEX fase_una_inicial ON fase (proceso_id) WHERE tipo = 'INICIAL';
CREATE UNIQUE INDEX fase_una_cierre_digital ON fase (proceso_id) WHERE tipo = 'CIERRE_DIGITAL';

-- ── Flujos alternativos manuales (RF-29) ──
CREATE TABLE flujo_alternativo (
    id          SERIAL PRIMARY KEY,
    proceso_id  INTEGER      NOT NULL REFERENCES proceso (id) ON DELETE CASCADE,
    codigo      VARCHAR(20)  NOT NULL,
    nombre      VARCHAR(100) NOT NULL,
    descripcion TEXT         NOT NULL DEFAULT '',
    activo      BOOLEAN      NOT NULL DEFAULT true,
    UNIQUE (proceso_id, codigo)
);

-- La última posición es la fase final manual
CREATE TABLE flujo_alternativo_fase (
    flujo_alternativo_id INTEGER NOT NULL REFERENCES flujo_alternativo (id) ON DELETE CASCADE,
    posicion             INTEGER NOT NULL CHECK (posicion >= 1),
    fase_id              INTEGER NOT NULL REFERENCES fase (id) ON DELETE RESTRICT,
    CONSTRAINT flujo_alternativo_fase_pk PRIMARY KEY (flujo_alternativo_id, posicion)
        DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (flujo_alternativo_id, fase_id)
);

-- ── Responsables (RF-04) ──
CREATE TABLE fase_responsable (
    fase_id    INTEGER NOT NULL REFERENCES fase (id) ON DELETE CASCADE,
    usuario_id INTEGER NOT NULL REFERENCES usuario (id) ON DELETE CASCADE,
    PRIMARY KEY (fase_id, usuario_id)
);
CREATE INDEX fase_responsable_usuario ON fase_responsable (usuario_id);

-- ── Catálogo de carreras (RF-25) ──
CREATE TABLE carrera (
    id     SERIAL PRIMARY KEY,
    codigo VARCHAR(20)  NOT NULL UNIQUE,
    nombre VARCHAR(200) NOT NULL,
    activo BOOLEAN      NOT NULL DEFAULT true
);

-- ── Carga masiva (RF-08) ──
CREATE TABLE carga_masiva (
    id                  SERIAL PRIMARY KEY,
    usuario_id          INTEGER      NOT NULL REFERENCES usuario (id),
    nombre_archivo      VARCHAR(255) NOT NULL,
    politica_existentes VARCHAR(15)  NOT NULL
                        CHECK (politica_existentes IN ('NO_MODIFICAR', 'COMPLETAR', 'SOBRESCRIBIR')),
    total_filas         INTEGER      NOT NULL DEFAULT 0,
    creados             INTEGER      NOT NULL DEFAULT 0,
    actualizados        INTEGER      NOT NULL DEFAULT 0,
    omitidos            INTEGER      NOT NULL DEFAULT 0,
    con_error           INTEGER      NOT NULL DEFAULT 0,
    creado_en           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ── Postulantes (RF-06) ──
CREATE TABLE postulante (
    id              SERIAL PRIMARY KEY,
    rut             VARCHAR(12)  NOT NULL UNIQUE,   -- normalizado: cuerpo-DV, sin puntos
    nombres         VARCHAR(100) NOT NULL,
    apellidos       VARCHAR(100) NOT NULL,
    telefono        VARCHAR(20),
    correo_personal VARCHAR(254) NOT NULL,
    origen          VARCHAR(15)  NOT NULL
                    CHECK (origen IN ('CARGA_MASIVA', 'CARGA_MANUAL', 'INICIO_STAFF', 'INICIO_QR')),
    carga_id        INTEGER REFERENCES carga_masiva (id) ON DELETE SET NULL,
    activo          BOOLEAN      NOT NULL DEFAULT true,
    eliminar_en     DATE,                           -- solo sin expediente (RF-26)
    creado_en       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    actualizado_en  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE postulante_carrera (
    postulante_id INTEGER  NOT NULL REFERENCES postulante (id) ON DELETE CASCADE,
    preferencia   SMALLINT NOT NULL CHECK (preferencia BETWEEN 1 AND 3),
    carrera_id    INTEGER  NOT NULL REFERENCES carrera (id),
    PRIMARY KEY (postulante_id, preferencia),
    UNIQUE (postulante_id, carrera_id)
);

-- ── Expedientes (RF-11, RF-16, RF-19, RF-20) ──
CREATE TABLE expediente (
    id                   SERIAL PRIMARY KEY,
    proceso_id           INTEGER     NOT NULL REFERENCES proceso (id),
    postulante_id        INTEGER     NOT NULL REFERENCES postulante (id),
    via_inicio           VARCHAR(5)  NOT NULL CHECK (via_inicio IN ('STAFF', 'QR')),
    iniciado_por         INTEGER REFERENCES usuario (id) ON DELETE SET NULL,
    numero_atencion      INTEGER     NOT NULL,
    token_seguimiento    VARCHAR(64) NOT NULL UNIQUE,
    creado_en            TIMESTAMPTZ NOT NULL DEFAULT now(),
    cerrado_en           TIMESTAMPTZ,
    estado               VARCHAR(20) NOT NULL DEFAULT 'EN_CURSO'
                         CHECK (estado IN ('EN_CURSO', 'ABANDONO', 'MATRICULADO', 'PROCESO_PENDIENTE')),
    con_observaciones    BOOLEAN     NOT NULL DEFAULT false,
    matriculado_en       TIMESTAMPTZ,
    flujo_alternativo_id INTEGER REFERENCES flujo_alternativo (id),   -- flujo en curso; NULL = principal
    tipo_cierre          VARCHAR(10) CHECK (tipo_cierre IN ('DIGITAL', 'MANUAL')),
    fase_cierre_id       INTEGER REFERENCES fase (id),
    fase_detenida_id     INTEGER REFERENCES fase (id),
    UNIQUE (proceso_id, postulante_id),
    UNIQUE (proceso_id, numero_atencion),
    CHECK ((estado = 'MATRICULADO') = (tipo_cierre IS NOT NULL))
);
CREATE INDEX expediente_proceso_estado ON expediente (proceso_id, estado);

-- ── Atenciones: la ruta del expediente, ordenada por secuencia (RF-12, RF-13) ──
CREATE TABLE atencion (
    id                   SERIAL PRIMARY KEY,
    expediente_id        INTEGER     NOT NULL REFERENCES expediente (id) ON DELETE CASCADE,
    fase_id              INTEGER     NOT NULL REFERENCES fase (id),
    secuencia            INTEGER     NOT NULL,
    estado               VARCHAR(30) NOT NULL CHECK (estado IN (
                             'PLANIFICADA', 'EN_ESPERA', 'EN_ATENCION', 'COMPLETADO',
                             'COMPLETADO_CON_OBSERVACIONES', 'DERIVADA', 'ABANDONADA')),
    flujo_alternativo_id INTEGER REFERENCES flujo_alternativo (id),
    observacion          TEXT,
    detalle_cierre       TEXT,
    planificada_por      INTEGER REFERENCES usuario (id) ON DELETE SET NULL,
    derivada_desde_id    INTEGER REFERENCES atencion (id) ON DELETE SET NULL,
    en_espera_desde      TIMESTAMPTZ,
    inicio_atencion      TIMESTAMPTZ,
    fin_atencion         TIMESTAMPTZ,
    atendido_por         INTEGER REFERENCES usuario (id) ON DELETE SET NULL,
    sin_tiempo_atencion  BOOLEAN     NOT NULL DEFAULT false,
    version              INTEGER     NOT NULL DEFAULT 1,
    -- diferible: insertar o quitar atenciones renumera la ruta en la misma transacción
    CONSTRAINT atencion_secuencia_unica UNIQUE (expediente_id, secuencia) DEFERRABLE INITIALLY DEFERRED,
    CHECK (estado <> 'COMPLETADO_CON_OBSERVACIONES' OR observacion IS NOT NULL)
);
-- Una sola atención activa por expediente (RF-13)
CREATE UNIQUE INDEX atencion_una_activa ON atencion (expediente_id)
    WHERE estado IN ('EN_ESPERA', 'EN_ATENCION');
-- Bandeja y reporte de espera (RF-17, RF-18): polling cada 10 s
CREATE INDEX atencion_bandeja ON atencion (fase_id, estado, en_espera_desde)
    WHERE estado IN ('EN_ESPERA', 'EN_ATENCION', 'PLANIFICADA');

-- ── Correos (RF-21) ──
CREATE TABLE envio_correo (
    id            SERIAL PRIMARY KEY,
    expediente_id INTEGER      NOT NULL REFERENCES expediente (id) ON DELETE CASCADE,
    tipo          VARCHAR(12)  NOT NULL CHECK (tipo IN ('INICIO', 'MATRICULADO', 'REENVIO', 'REENVIO_QR')),
    destinatario  VARCHAR(254) NOT NULL,
    estado        VARCHAR(10)  NOT NULL CHECK (estado IN ('ENVIADO', 'ERROR')),
    detalle_error TEXT,
    enviado_por   INTEGER REFERENCES usuario (id) ON DELETE SET NULL,
    creado_en     TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX envio_correo_expediente ON envio_correo (expediente_id, creado_en);

-- ── Totales agregados que sobreviven a la eliminación de datos (RF-26) ──
CREATE TABLE proceso_resumen (
    id                        SERIAL PRIMARY KEY,
    proceso_id                INTEGER     NOT NULL REFERENCES proceso (id) ON DELETE CASCADE,
    dimension                 VARCHAR(10) NOT NULL CHECK (dimension IN ('TOTAL', 'CARRERA', 'FASE')),
    carrera_id                INTEGER REFERENCES carrera (id),
    fase_id                   INTEGER REFERENCES fase (id),
    matriculados              INTEGER     NOT NULL DEFAULT 0,
    matriculados_cierre_manual INTEGER    NOT NULL DEFAULT 0,
    proceso_pendiente         INTEGER     NOT NULL DEFAULT 0,
    calculado_en              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Auditoría (RF-24) ──
CREATE TABLE auditoria (
    id             BIGSERIAL PRIMARY KEY,
    fecha_hora     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    usuario_id     INTEGER REFERENCES usuario (id) ON DELETE SET NULL,
    actor          VARCHAR(10)  NOT NULL CHECK (actor IN ('USUARIO', 'PUBLICO', 'SISTEMA')),
    accion         VARCHAR(50)  NOT NULL,
    entidad        VARCHAR(30)  NOT NULL,
    entidad_id     INTEGER,
    valor_anterior JSONB,
    valor_nuevo    JSONB,
    ip             VARCHAR(45),
    user_agent     VARCHAR(500)
);
CREATE INDEX auditoria_fecha ON auditoria (fecha_hora);
CREATE INDEX auditoria_entidad ON auditoria (entidad, entidad_id);
