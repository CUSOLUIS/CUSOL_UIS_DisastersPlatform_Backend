-- CHG-205 — «Ofrecer alojamiento temporal»: gemela pública de la oferta
-- de comida (CHG-163/176). Mismo ciclo —creación anónima o con cuenta,
-- vigencia calculada en servidor, expiración por filtro y nunca por
-- borrado— con lo propio de una casa que se abre: cuánta gente cabe, si
-- el espacio se comparte, si entran mascotas y qué barreras tiene.
--
-- NO se construye sobre `aid_offers` (015, CHG-044): aquella publica
-- solo tras moderación y su aceptación sigue bloqueada por DEC-020 y
-- DEC-021, así que una oferta enviada allí no se vería en ninguna parte.
-- Aquella tabla queda intacta.
--
-- Idempotente; no borra ni altera ninguna fila existente.

-- 1) La oferta.
CREATE TABLE IF NOT EXISTS disaster_service.shelter_offers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    public_code TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    reporter_account_id UUID,
    description TEXT NOT NULL,
    address TEXT NOT NULL,
    latitude DOUBLE PRECISION
        CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION
        CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
    duration_hours INTEGER NOT NULL
        CHECK (duration_hours BETWEEN 1 AND 720),
    notification_radius_km INTEGER,
    -- Cuántas personas pueden dormir. El tope alto (1000) cubre un
    -- coliseo o un salón parroquial, no solo una habitación.
    spaces_available INTEGER NOT NULL
        CHECK (spaces_available BETWEEN 1 AND 1000),
    -- Si el espacio se comparte con quien lo ofrece u otras personas.
    -- Se publica porque cambia la decisión de quien lo necesita.
    shared_space BOOLEAN NOT NULL,
    accepts_pets BOOLEAN NOT NULL DEFAULT FALSE,
    -- Barreras o facilidades reales; público y opcional.
    accessibility_notes TEXT,
    -- Umbral de denuncias: deja de publicarse sin borrarse (espejo de
    -- food_offers.disabled_at).
    disabled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT shelter_offer_description_not_blank
        CHECK (btrim(description) <> ''),
    CONSTRAINT shelter_offer_address_not_blank
        CHECK (btrim(address) <> ''),
    CONSTRAINT shelter_offer_expires_after_creation
        CHECK (expires_at > created_at),
    CONSTRAINT shelter_offer_coordinates_pair
        CHECK ((latitude IS NULL) = (longitude IS NULL)),
    CONSTRAINT shelter_offer_radius_range
        CHECK (
            notification_radius_km IS NULL
            OR notification_radius_km BETWEEN 1 AND 100
        ),
    CONSTRAINT shelter_offer_radius_needs_coordinates
        CHECK (notification_radius_km IS NULL OR latitude IS NOT NULL),
    CONSTRAINT shelter_offer_accessibility_notes_length
        CHECK (
            accessibility_notes IS NULL
            OR char_length(btrim(accessibility_notes)) BETWEEN 1 AND 500
        )
);

-- Listados activos: filtro por vigencia + orden por creación.
CREATE INDEX IF NOT EXISTS shelter_offers_active_idx
    ON disaster_service.shelter_offers (expires_at, created_at DESC, id DESC);

-- 2) Comunidad: QUINTO objetivo de las tablas comunitarias (acopios
--    CHG-165, ofertas de comida CHG-176, solicitudes CHG-180, casitas
--    CHG-182). Esta es la versión más ancha y por eso va sin condición:
--    es la que debe mandar al final de una pasada completa (CHG-204).
ALTER TABLE disaster_service.aid_location_comments
    ADD COLUMN IF NOT EXISTS shelter_offer_id UUID
        REFERENCES disaster_service.shelter_offers(id) ON DELETE CASCADE;

ALTER TABLE disaster_service.aid_location_comments
    DROP CONSTRAINT IF EXISTS aid_location_comment_single_target;
ALTER TABLE disaster_service.aid_location_comments
    ADD CONSTRAINT aid_location_comment_single_target
    CHECK (
        num_nonnulls(
            location_id, food_offer_id, help_request_id, damaged_home_id,
            shelter_offer_id
        ) = 1
    );

CREATE INDEX IF NOT EXISTS aid_location_comments_shelter_offer_idx
    ON disaster_service.aid_location_comments
    (shelter_offer_id, created_at DESC)
    WHERE shelter_offer_id IS NOT NULL;

ALTER TABLE disaster_service.aid_location_reports
    ADD COLUMN IF NOT EXISTS shelter_offer_id UUID
        REFERENCES disaster_service.shelter_offers(id) ON DELETE CASCADE;

ALTER TABLE disaster_service.aid_location_reports
    DROP CONSTRAINT IF EXISTS aid_location_report_single_target;
ALTER TABLE disaster_service.aid_location_reports
    ADD CONSTRAINT aid_location_report_single_target
    CHECK (
        num_nonnulls(
            location_id, food_offer_id, help_request_id, damaged_home_id,
            shelter_offer_id
        ) = 1
    );

CREATE UNIQUE INDEX IF NOT EXISTS shelter_offer_report_live_denouncer_idx
    ON disaster_service.aid_location_reports (shelter_offer_id, denouncer_key)
    WHERE archived_at IS NULL AND shelter_offer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS shelter_offer_reports_active_idx
    ON disaster_service.aid_location_reports
    (shelter_offer_id, moderation_status)
    WHERE archived_at IS NULL AND shelter_offer_id IS NOT NULL;
