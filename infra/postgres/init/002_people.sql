-- CHG-003 — Registros de personas para el panel de situación humana.

CREATE SCHEMA IF NOT EXISTS disaster_service;

DO $$
BEGIN
    CREATE TYPE disaster_service.human_status AS ENUM (
        'missing',
        'reported_deceased',
        'confirmed_alive',
        'confirmed_deceased'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

CREATE TABLE IF NOT EXISTS disaster_service.people (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES disaster_service.sources(id),
    display_name TEXT NOT NULL,
    status disaster_service.human_status NOT NULL,
    location TEXT NOT NULL,
    related_event TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT person_display_name_not_blank CHECK (btrim(display_name) <> ''),
    CONSTRAINT person_location_not_blank CHECK (btrim(location) <> ''),
    CONSTRAINT person_related_event_not_blank CHECK (btrim(related_event) <> '')
);

CREATE INDEX IF NOT EXISTS people_created_at_idx
    ON disaster_service.people (created_at DESC);

CREATE INDEX IF NOT EXISTS people_status_idx
    ON disaster_service.people (status);
