-- RF-24: cada registro de auditoría guarda el rol del usuario al momento de la acción.
ALTER TABLE auditoria ADD COLUMN rol VARCHAR(20)
    CHECK (rol IN ('ADMIN', 'GESTOR', 'USUARIO_FASE'));
CREATE INDEX auditoria_usuario ON auditoria (usuario_id, fecha_hora);
