-- CHG-036 — Consola de superadministración.
-- 1) Auditoría administrativa append-only en un esquema propio, escrita
--    por disaster-service e identity-service (misma base, esquemas
--    separados). Un trigger impide UPDATE/DELETE incluso para el usuario
--    de aplicación; nunca guarda secretos ni valores cambiados.
-- 2) Control de concurrencia (version) y archivo lógico en los recursos
--    administrables existentes; jamás borrado físico.
-- Idempotente: IF NOT EXISTS / duplicate_object; re-ejecutable.

CREATE SCHEMA IF NOT EXISTS administration;

CREATE TABLE IF NOT EXISTS administration.audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_account_id UUID NOT NULL,
    actor_display_name TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_kind TEXT NOT NULL,
    resource_id UUID,
    result TEXT NOT NULL CHECK (result IN ('success', 'denied', 'failed')),
    -- Motivo cifrado por la aplicación; el resumen de campos cambiados
    -- lista solo claves, nunca valores.
    reason_protected BYTEA,
    changed_fields TEXT[] NOT NULL DEFAULT '{}',
    request_correlation_id UUID,
    CONSTRAINT audit_action_not_blank CHECK (btrim(action) <> ''),
    CONSTRAINT audit_resource_kind_not_blank
        CHECK (btrim(resource_kind) <> '')
);

CREATE INDEX IF NOT EXISTS audit_events_occurred_idx
    ON administration.audit_events (occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS audit_events_actor_idx
    ON administration.audit_events (actor_account_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS audit_events_resource_idx
    ON administration.audit_events (resource_kind, resource_id);

-- Append-only: el rol único de aplicación es dueño de la tabla, así que
-- la inmutabilidad se garantiza con un trigger, no con GRANT/REVOKE.
CREATE OR REPLACE FUNCTION administration.forbid_audit_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'La auditoría administrativa es append-only (CHG-036).';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_events_append_only
    ON administration.audit_events;
CREATE TRIGGER audit_events_append_only
    BEFORE UPDATE OR DELETE ON administration.audit_events
    FOR EACH ROW EXECUTE FUNCTION administration.forbid_audit_mutation();

-- Recursos administrables: version para concurrencia optimista,
-- archivo lógico (archived_*) y bandera de solicitud de cambios.
ALTER TABLE disaster_service.missing_person_reports
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS archived_by UUID,
    ADD COLUMN IF NOT EXISTS needs_information BOOLEAN NOT NULL
        DEFAULT FALSE;

ALTER TABLE disaster_service.person_status_reports
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS archived_by UUID,
    ADD COLUMN IF NOT EXISTS needs_information BOOLEAN NOT NULL
        DEFAULT FALSE;

ALTER TABLE disaster_service.aid_location_ratings
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS archived_by UUID,
    ADD COLUMN IF NOT EXISTS needs_information BOOLEAN NOT NULL
        DEFAULT FALSE;

ALTER TABLE disaster_service.unverified_building_reports
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS archived_by UUID,
    ADD COLUMN IF NOT EXISTS needs_information BOOLEAN NOT NULL
        DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS missing_person_reports_admin_idx
    ON disaster_service.missing_person_reports (received_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS person_status_reports_admin_idx
    ON disaster_service.person_status_reports (received_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS aid_location_ratings_admin_idx
    ON disaster_service.aid_location_ratings (received_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS unverified_building_reports_admin_idx
    ON disaster_service.unverified_building_reports
    (created_at DESC, id DESC);
