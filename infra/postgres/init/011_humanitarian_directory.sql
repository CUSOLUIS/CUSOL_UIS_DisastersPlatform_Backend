-- CHG-034 — Directorio humanitario y aportes con evidencia.
-- Proyecciones públicas de lugares de ayuda (centros y puntos de
-- recolección), novedades privadas sobre personas y valoraciones
-- privadas de lugares. Nada de lo que aporta la ciudadanía cambia una
-- proyección pública sin moderación humana autorizada (DEC-009).
-- Idempotente: tipos con captura de duplicate_object y tablas IF NOT EXISTS.

CREATE SCHEMA IF NOT EXISTS disaster_service;

DO $$
BEGIN
    CREATE TYPE disaster_service.aid_location_kind AS ENUM (
        'collection_center',
        'collection_point'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE disaster_service.aid_location_availability AS ENUM (
        'active',
        'inactive',
        'unknown'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE disaster_service.public_person_status AS ENUM (
        'missing',
        'found',
        'deceased'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE disaster_service.contribution_status AS ENUM (
        'under_review',
        'accepted',
        'rejected',
        'withdrawn'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE disaster_service.contribution_actor_kind AS ENUM (
        'anonymous',
        'authenticated'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- Estado público moderado y fuente de la tarjeta del directorio.
-- `public_status` solo cambia al aceptar una novedad (nunca al crearla).
ALTER TABLE disaster_service.missing_person_cases
    ADD COLUMN IF NOT EXISTS public_status
        disaster_service.public_person_status NOT NULL DEFAULT 'missing',
    ADD COLUMN IF NOT EXISTS source_id UUID
        REFERENCES disaster_service.sources(id);

-- Proyección pública de lugares de ayuda. `collection_point` entra al
-- dominio sin reinterpretar los centros existentes del mapa operativo;
-- publicarlo en el mapa requiere un cambio coordinado posterior.
CREATE TABLE IF NOT EXISTS disaster_service.aid_locations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind disaster_service.aid_location_kind NOT NULL,
    name TEXT NOT NULL,
    location_label TEXT NOT NULL,
    municipality TEXT NOT NULL,
    department TEXT NOT NULL,
    verification_status disaster_service.verification_status NOT NULL
        DEFAULT 'unverified',
    availability_status disaster_service.aid_location_availability NOT NULL
        DEFAULT 'unknown',
    open_now BOOLEAN,
    accepted_supplies TEXT[] NOT NULL DEFAULT '{}',
    -- Agregado transaccional sobre valoraciones aceptadas únicamente.
    average_rating NUMERIC(3, 2)
        CHECK (average_rating BETWEEN 1 AND 5),
    ratings_count INTEGER NOT NULL DEFAULT 0
        CHECK (ratings_count >= 0),
    source_id UUID NOT NULL REFERENCES disaster_service.sources(id),
    publication_status disaster_service.case_publication_status NOT NULL
        DEFAULT 'under_review',
    data_classification disaster_service.data_classification NOT NULL
        DEFAULT 'demonstrative',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT aid_location_name_not_blank CHECK (btrim(name) <> ''),
    CONSTRAINT aid_location_label_not_blank
        CHECK (btrim(location_label) <> ''),
    CONSTRAINT aid_location_rating_consistency
        CHECK (
            (ratings_count = 0 AND average_rating IS NULL)
            OR (ratings_count > 0 AND average_rating IS NOT NULL)
        )
);

CREATE INDEX IF NOT EXISTS aid_locations_directory_idx
    ON disaster_service.aid_locations
    (kind, publication_status, updated_at DESC);

-- Novedad ciudadana sobre una persona: expediente privado, jamás
-- expuesto por búsquedas. La descripción va cifrada por la aplicación.
CREATE TABLE IF NOT EXISTS disaster_service.person_status_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL
        REFERENCES disaster_service.missing_person_cases(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    claimed_outcome disaster_service.public_person_status NOT NULL
        CHECK (claimed_outcome IN ('found', 'deceased')),
    evidence_description_encrypted BYTEA NOT NULL,
    occurred_at TIMESTAMPTZ,
    location_description_encrypted BYTEA,
    actor_kind disaster_service.contribution_actor_kind NOT NULL,
    account_id UUID,
    moderation_status disaster_service.contribution_status NOT NULL
        DEFAULT 'under_review',
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    decided_by_role TEXT,
    CONSTRAINT status_report_actor_account
        CHECK (
            (actor_kind = 'authenticated' AND account_id IS NOT NULL)
            OR (actor_kind = 'anonymous' AND account_id IS NULL)
        )
);

CREATE INDEX IF NOT EXISTS person_status_reports_person_idx
    ON disaster_service.person_status_reports
    (person_id, moderation_status);

-- Evidencia fotográfica de novedades: solo referencias opacas; los
-- binarios viven fuera de la base y nunca reciben URL pública
-- automática (en particular la evidencia de fallecimiento).
CREATE TABLE IF NOT EXISTS disaster_service.person_status_report_photos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID NOT NULL
        REFERENCES disaster_service.person_status_reports(id)
        ON DELETE CASCADE,
    position INTEGER NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    derived_storage_key TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    sha256 TEXT NOT NULL,
    exif_removed BOOLEAN NOT NULL,
    malware_scan TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT status_report_photo_position_valid
        CHECK (position BETWEEN 1 AND 5)
);

-- Valoración ciudadana de un lugar de ayuda: privada hasta moderación;
-- solo las aceptadas afectan el promedio público.
CREATE TABLE IF NOT EXISTS disaster_service.aid_location_ratings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    location_id UUID NOT NULL
        REFERENCES disaster_service.aid_locations(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    rating SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    evidence_description_encrypted BYTEA NOT NULL,
    actor_kind disaster_service.contribution_actor_kind NOT NULL,
    account_id UUID,
    moderation_status disaster_service.contribution_status NOT NULL
        DEFAULT 'under_review',
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    decided_by_role TEXT,
    CONSTRAINT rating_actor_account
        CHECK (
            (actor_kind = 'authenticated' AND account_id IS NOT NULL)
            OR (actor_kind = 'anonymous' AND account_id IS NULL)
        )
);

CREATE INDEX IF NOT EXISTS aid_location_ratings_location_idx
    ON disaster_service.aid_location_ratings
    (location_id, moderation_status);

CREATE TABLE IF NOT EXISTS disaster_service.aid_location_rating_photos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rating_id UUID NOT NULL
        REFERENCES disaster_service.aid_location_ratings(id)
        ON DELETE CASCADE,
    position INTEGER NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    derived_storage_key TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    sha256 TEXT NOT NULL,
    exif_removed BOOLEAN NOT NULL,
    malware_scan TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT rating_photo_position_valid CHECK (position BETWEEN 1 AND 3)
);

-- Auditoría sin contenido sensible: nunca payloads, descripciones,
-- nombres de archivo originales ni identificadores de cuenta en claro.
CREATE TABLE IF NOT EXISTS disaster_service.community_contribution_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type TEXT NOT NULL,
    contribution_kind TEXT NOT NULL,
    contribution_id UUID,
    detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
