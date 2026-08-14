-- CHG-035 — Reporte ciudadano de edificio sin verificar.
-- Expediente PRIVADO: crear un reporte nunca escribe el mapa operativo;
-- solo una moderación humana autorizada puede proyectar `building_pending`
-- (proyección desactivada mientras DEC-012–DEC-014 sigan pendientes).
-- Idempotente: tipos con captura de duplicate_object y tablas IF NOT EXISTS.

CREATE SCHEMA IF NOT EXISTS disaster_service;

DO $$
BEGIN
    CREATE TYPE disaster_service.building_report_type AS ENUM (
        'residential',
        'commercial',
        'institutional',
        'industrial',
        'mixed_use',
        'other'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE disaster_service.building_search_status AS ENUM (
        'not_started',
        'interrupted',
        'incomplete',
        'unknown'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE disaster_service.building_occupancy_report AS ENUM (
        'unknown',
        'possibly_occupied',
        'reported_empty'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- Fuente ciudadana fija para la eventual proyección moderada del mapa.
INSERT INTO disaster_service.sources (id, name, source_type, url)
VALUES (
    '11111111-1111-4111-8111-111111111106',
    'Reporte ciudadano de edificio — plataforma CUSOL',
    'citizen',
    NULL
)
ON CONFLICT (id) DO NOTHING;

-- Expediente privado. Los campos `*_protected` llegan cifrados por la
-- aplicación; la llave de idempotencia solo se guarda hasheada. Sin
-- índices sobre contacto, dirección, coordenadas ni texto libre.
CREATE TABLE IF NOT EXISTS disaster_service.unverified_building_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    public_tracking_code TEXT NOT NULL UNIQUE,
    idempotency_key_hash TEXT NOT NULL UNIQUE,
    moderation_status disaster_service.contribution_status NOT NULL
        DEFAULT 'under_review',
    building_reference TEXT NOT NULL,
    building_type disaster_service.building_report_type NOT NULL,
    department TEXT NOT NULL,
    municipality TEXT NOT NULL,
    sector TEXT NOT NULL,
    location_reference_protected BYTEA NOT NULL,
    address_protected BYTEA,
    latitude_protected BYTEA,
    longitude_protected BYTEA,
    related_disaster_id UUID
        REFERENCES disaster_service.disaster_events(id),
    observed_date DATE NOT NULL,
    observed_time TEXT,
    search_status disaster_service.building_search_status NOT NULL,
    occupancy_report disaster_service.building_occupancy_report NOT NULL,
    pending_reasons TEXT[] NOT NULL,
    observed_conditions TEXT[] NOT NULL DEFAULT '{}',
    observation_description_protected BYTEA NOT NULL,
    reporter_name_protected BYTEA NOT NULL,
    reporter_role_protected BYTEA NOT NULL,
    reporter_organization_protected BYTEA,
    reporter_phone_protected BYTEA,
    reporter_email_protected BYTEA,
    official_report_number_protected BYTEA,
    truth_confirmed_at TIMESTAMPTZ NOT NULL,
    photo_authorization_confirmed_at TIMESTAMPTZ NOT NULL,
    review_acknowledged_at TIMESTAMPTZ NOT NULL,
    legal_text_version TEXT NOT NULL,
    actor_account_id UUID,
    map_point_id UUID
        REFERENCES disaster_service.operational_map_points(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    moderated_at TIMESTAMPTZ,
    moderated_by TEXT,
    moderation_reason_protected BYTEA,
    CONSTRAINT building_pending_reasons_not_empty
        CHECK (cardinality(pending_reasons) BETWEEN 1 AND 9),
    CONSTRAINT building_coordinates_pair
        CHECK (
            (latitude_protected IS NULL) = (longitude_protected IS NULL)
        )
);

CREATE INDEX IF NOT EXISTS unverified_building_reports_review_idx
    ON disaster_service.unverified_building_reports
    (moderation_status, created_at DESC);

-- Evidencia en cuarentena: solo referencias opacas; los binarios viven
-- fuera de la base, sin nombres originales y sin URL pública.
CREATE TABLE IF NOT EXISTS disaster_service.unverified_building_report_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID NOT NULL
        REFERENCES disaster_service.unverified_building_reports(id)
        ON DELETE CASCADE,
    position INTEGER NOT NULL,
    object_key TEXT NOT NULL UNIQUE,
    derived_object_key TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    sha256 TEXT NOT NULL,
    malware_scan TEXT NOT NULL,
    exif_removed BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT building_file_position_valid CHECK (position BETWEEN 1 AND 5)
);
