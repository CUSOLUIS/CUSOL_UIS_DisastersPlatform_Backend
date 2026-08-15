-- CHG-077 — Verificación comunitaria del estado de una persona.
-- (a) Cuentas del sector salud: campos declarados al crear las
--     credenciales; la bandera efectiva exige profesión + registro.
-- (b) Novedades: se guarda si el reportante era del sector salud al
--     momento de reportar (la cuenta puede cambiar después).

ALTER TABLE identity_service.accounts
    ADD COLUMN IF NOT EXISTS health_profession TEXT,
    ADD COLUMN IF NOT EXISTS health_license_number TEXT,
    ADD COLUMN IF NOT EXISTS health_institution TEXT;

ALTER TABLE disaster_service.person_status_reports
    ADD COLUMN IF NOT EXISTS reporter_health_sector BOOLEAN
        NOT NULL DEFAULT FALSE;

-- El umbral comunitario y la prioridad del sector salud consultan por
-- persona y desenlace sobre novedades no rechazadas ni archivadas.
CREATE INDEX IF NOT EXISTS person_status_reports_outcome_idx
    ON disaster_service.person_status_reports
    (person_id, claimed_outcome, moderation_status);
