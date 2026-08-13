-- CHG-007 — Búsqueda pública y reportes privados de personas desaparecidas.

CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE SCHEMA IF NOT EXISTS disaster_service;

DO $$
BEGIN
    CREATE TYPE disaster_service.case_publication_status AS ENUM (
        'under_review',
        'published',
        'rejected',
        'withdrawn'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- Proyección pública moderada: únicamente campos autorizados para publicación.
CREATE TABLE IF NOT EXISTS disaster_service.missing_person_cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    public_case_code TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    aliases TEXT[] NOT NULL DEFAULT '{}',
    approximate_age INTEGER
        CHECK (approximate_age BETWEEN 0 AND 120),
    last_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_area TEXT NOT NULL,
    municipality TEXT NOT NULL,
    department TEXT NOT NULL,
    clothing_description TEXT,
    physical_description TEXT,
    distinctive_marks TEXT,
    public_photo_url TEXT,
    map_point_id UUID
        REFERENCES disaster_service.operational_map_points(id),
    publication_status disaster_service.case_publication_status
        NOT NULL DEFAULT 'under_review',
    data_classification disaster_service.data_classification
        NOT NULL DEFAULT 'demonstrative',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT case_code_not_blank CHECK (btrim(public_case_code) <> ''),
    CONSTRAINT case_display_name_not_blank CHECK (btrim(display_name) <> '')
);

CREATE INDEX IF NOT EXISTS missing_person_cases_status_updated_idx
    ON disaster_service.missing_person_cases
    (publication_status, updated_at DESC);

-- Reporte privado: nunca se expone por la búsqueda pública.
-- Documento, salud y contactos se guardan cifrados por la aplicación.
CREATE TABLE IF NOT EXISTS disaster_service.missing_person_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT NOT NULL UNIQUE,
    public_case_code TEXT NOT NULL UNIQUE,
    status disaster_service.verification_status
        NOT NULL DEFAULT 'under_review',
    first_names TEXT NOT NULL,
    last_names TEXT NOT NULL,
    aliases TEXT,
    birth_date DATE,
    approximate_age INTEGER,
    gender_identity TEXT,
    nationality TEXT,
    document_type_encrypted BYTEA,
    document_number_encrypted BYTEA,
    height_cm INTEGER,
    build TEXT,
    skin_tone TEXT,
    hair_description TEXT,
    eye_description TEXT,
    distinctive_marks TEXT,
    medical_information_encrypted BYTEA,
    last_seen_date DATE NOT NULL,
    last_seen_time TEXT,
    department TEXT NOT NULL,
    municipality TEXT NOT NULL,
    last_seen_area TEXT NOT NULL,
    clothing_description TEXT NOT NULL,
    circumstances TEXT NOT NULL,
    additional_description TEXT,
    reporter_name_encrypted BYTEA NOT NULL,
    reporter_relationship TEXT NOT NULL,
    reporter_phone_encrypted BYTEA,
    reporter_email_encrypted BYTEA,
    official_report_number TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS missing_person_reports_received_idx
    ON disaster_service.missing_person_reports (received_at DESC);

-- Fotografías: solo referencias opacas; los binarios viven fuera de la base.
CREATE TABLE IF NOT EXISTS disaster_service.missing_person_report_photos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID NOT NULL
        REFERENCES disaster_service.missing_person_reports(id)
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
    CONSTRAINT photo_position_valid CHECK (position BETWEEN 1 AND 5)
);

-- Auditoría sin contenido sensible.
CREATE TABLE IF NOT EXISTS disaster_service.missing_person_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type TEXT NOT NULL,
    report_id UUID,
    detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
