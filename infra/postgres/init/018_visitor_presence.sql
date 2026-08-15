-- CHG-066 — Presencia de visitantes con consentimiento explícito.
-- Cada dispositivo que ACEPTA compartir ubicación mientras usa la app
-- reporta su última posición (un registro por dispositivo, upsert).
-- Solo la consola super_admin puede leerla; el ingreso es anónimo (el
-- account_id es identidad OPACA opcional, sin FK). Retención corta:
-- las filas con más de 24 horas se purgan al listar.
-- Idempotente: IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS disaster_service.visitor_presence (
    presence_id UUID PRIMARY KEY,
    account_id UUID,
    latitude DOUBLE PRECISION NOT NULL
        CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL
        CHECK (longitude BETWEEN -180 AND 180),
    accuracy_meters REAL CHECK (accuracy_meters >= 0),
    platform TEXT NOT NULL DEFAULT 'web'
        CHECK (platform IN ('web', 'android', 'ios')),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS visitor_presence_updated_idx
    ON disaster_service.visitor_presence (updated_at DESC);

-- CHG-066: instantánea de la ubicación del reportante al momento de
-- enviar un reporte (anónimo o con cuenta). Cifrada; solo la consola
-- super_admin puede descifrarla.
ALTER TABLE disaster_service.missing_person_reports
    ADD COLUMN IF NOT EXISTS reporter_snapshot_latitude_encrypted BYTEA,
    ADD COLUMN IF NOT EXISTS reporter_snapshot_longitude_encrypted BYTEA;

ALTER TABLE disaster_service.unverified_building_reports
    ADD COLUMN IF NOT EXISTS reporter_snapshot_latitude_protected BYTEA,
    ADD COLUMN IF NOT EXISTS reporter_snapshot_longitude_protected BYTEA;
