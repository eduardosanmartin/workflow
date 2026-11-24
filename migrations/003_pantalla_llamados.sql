-- 003 · Pantalla de llamados del hall de espera

-- Módulo (puesto de atención) donde trabaja hoy el usuario; él mismo lo indica en la bandeja
ALTER TABLE usuario ADD COLUMN modulo VARCHAR(20);

-- Llamado de un postulante en espera a un módulo. La pantalla repite el aviso cada 30 s
-- mientras siga abierto; se cierra al atenderlo, al cancelarlo o con cualquier otra acción
-- sobre el expediente.
CREATE TABLE llamado (
    id          SERIAL PRIMARY KEY,
    atencion_id INTEGER     NOT NULL REFERENCES atencion (id) ON DELETE CASCADE,
    usuario_id  INTEGER REFERENCES usuario (id) ON DELETE SET NULL,
    modulo      VARCHAR(20) NOT NULL,
    llamado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    cerrado_en  TIMESTAMPTZ
);
-- Un solo llamado abierto por atención
CREATE UNIQUE INDEX llamado_uno_abierto ON llamado (atencion_id) WHERE cerrado_en IS NULL;
CREATE INDEX llamado_atencion ON llamado (atencion_id, llamado_en);
