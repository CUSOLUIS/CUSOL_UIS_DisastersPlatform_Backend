CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS disaster_service;

DO $$
BEGIN
    CREATE TYPE disaster_service.verification_status AS ENUM (
        'unverified',
        'under_review',
        'verified',
        'rejected'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE disaster_service.source_type AS ENUM (
        'official',
        'citizen',
        'sensor',
        'integration',
        'ai_inference'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

CREATE TABLE IF NOT EXISTS disaster_service.sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    source_type disaster_service.source_type NOT NULL,
    url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT source_name_not_blank CHECK (btrim(name) <> '')
);

CREATE TABLE IF NOT EXISTS disaster_service.disaster_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES disaster_service.sources(id),
    title TEXT NOT NULL,
    description TEXT,
    disaster_type TEXT NOT NULL,
    severity TEXT,
    verification_status disaster_service.verification_status NOT NULL
        DEFAULT 'unverified',
    location GEOGRAPHY(POINT, 4326),
    occurred_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT event_title_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT disaster_type_not_blank CHECK (btrim(disaster_type) <> '')
);

CREATE INDEX IF NOT EXISTS disaster_events_location_gix
    ON disaster_service.disaster_events USING GIST (location);

CREATE INDEX IF NOT EXISTS disaster_events_updated_at_idx
    ON disaster_service.disaster_events (updated_at DESC);

CREATE INDEX IF NOT EXISTS disaster_events_type_status_idx
    ON disaster_service.disaster_events (
        disaster_type,
        verification_status
    );
