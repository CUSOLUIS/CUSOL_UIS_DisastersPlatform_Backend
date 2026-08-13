-- CHG-015 — Proyección geográfica pública de la situación humana.
-- Separada de disaster_service.people: el id de esta tabla es el
-- identificador público opaco; person_id nunca se expone.
-- DEC-007 pendiente: la precisión pública jamás es 'exact' (CHECK).
-- Idempotente: IF NOT EXISTS / duplicate_object; re-ejecutable.

CREATE SCHEMA IF NOT EXISTS disaster_service;

DO $$
BEGIN
    CREATE TYPE disaster_service.projection_visibility AS ENUM (
        'pending',
        'published',
        'withdrawn'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

CREATE TABLE IF NOT EXISTS disaster_service.people_map_projection (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL UNIQUE
        REFERENCES disaster_service.people(id) ON DELETE CASCADE,
    location GEOGRAPHY(POINT, 4326) NOT NULL,
    coordinate_precision disaster_service.coordinate_precision NOT NULL,
    verification_status disaster_service.verification_status NOT NULL
        DEFAULT 'unverified',
    visibility disaster_service.projection_visibility NOT NULL
        DEFAULT 'pending',
    data_classification disaster_service.data_classification NOT NULL
        DEFAULT 'demonstrative',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- DEC-007: ninguna proyección pública puede ser exacta.
    CONSTRAINT people_map_projection_never_exact
        CHECK (coordinate_precision <> 'exact')
);

CREATE INDEX IF NOT EXISTS people_map_projection_location_gix
    ON disaster_service.people_map_projection USING GIST (location);

CREATE INDEX IF NOT EXISTS people_map_projection_visibility_idx
    ON disaster_service.people_map_projection (visibility);
