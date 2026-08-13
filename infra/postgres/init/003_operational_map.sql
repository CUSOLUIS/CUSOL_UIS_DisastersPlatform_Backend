-- CHG-006 — Puntos del mapa operativo de respuesta.

CREATE SCHEMA IF NOT EXISTS disaster_service;

DO $$
BEGIN
    CREATE TYPE disaster_service.operational_map_category AS ENUM (
        'missing_person',
        'collection_center',
        'rubble_reviewed',
        'rubble_pending'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE disaster_service.coordinate_precision AS ENUM (
        'exact',
        'approximate',
        'municipality'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE disaster_service.data_classification AS ENUM (
        'demonstrative',
        'operational'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

CREATE TABLE IF NOT EXISTS disaster_service.operational_map_points (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category disaster_service.operational_map_category NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    location_label TEXT NOT NULL,
    location GEOGRAPHY(POINT, 4326) NOT NULL,
    coordinate_precision disaster_service.coordinate_precision NOT NULL,
    verification_status disaster_service.verification_status NOT NULL
        DEFAULT 'unverified',
    related_disaster_id UUID
        REFERENCES disaster_service.disaster_events(id),
    source_id UUID NOT NULL REFERENCES disaster_service.sources(id),
    data_classification disaster_service.data_classification NOT NULL
        DEFAULT 'demonstrative',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT map_point_title_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT map_point_location_label_not_blank
        CHECK (btrim(location_label) <> '')
);

CREATE INDEX IF NOT EXISTS operational_map_points_updated_at_idx
    ON disaster_service.operational_map_points (updated_at DESC);

CREATE INDEX IF NOT EXISTS operational_map_points_category_idx
    ON disaster_service.operational_map_points (category);

CREATE INDEX IF NOT EXISTS operational_map_points_location_gix
    ON disaster_service.operational_map_points USING GIST (location);
