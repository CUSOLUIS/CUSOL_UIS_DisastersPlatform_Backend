-- CHG-044 — Ofertas comunitarias de comida y alojamiento (FEATURE-010).
-- Expediente PRIVADO cifrado con clave propia (AID_OFFER_ENCRYPTION_KEY_FILE)
-- + detalle por tipo + proyección pública SEPARADA sin cuenta, contacto,
-- dirección ni coordenadas. Crear una oferta jamás publica; solo una
-- moderación autorizada podrá proyectar (bloqueada por DEC-020/DEC-021).
-- Idempotente: tipos con duplicate_object y tablas IF NOT EXISTS.

CREATE SCHEMA IF NOT EXISTS disaster_service;

DO $$
BEGIN
    CREATE TYPE disaster_service.aid_offer_kind AS ENUM (
        'community_meal',
        'temporary_shelter'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE disaster_service.aid_offer_availability AS ENUM (
        'scheduled',
        'active',
        'paused',
        'fulfilled',
        'withdrawn',
        'expired'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE disaster_service.aid_offer_moderation_status AS ENUM (
        'under_review',
        'needs_information',
        'accepted',
        'rejected',
        'archived'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE disaster_service.meal_distribution_mode AS ENUM (
        'pickup',
        'delivery',
        'both'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE disaster_service.aid_offer_capacity_unit AS ENUM (
        'servings',
        'spaces'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- Fuente ciudadana fija de la eventual proyección pública; jamás
-- incorpora identidad del oferente.
INSERT INTO disaster_service.sources (id, name, source_type, url)
VALUES (
    '11111111-1111-4111-8111-111111111107',
    'Oferta comunitaria — plataforma CUSOL',
    'citizen',
    NULL
)
ON CONFLICT (id) DO NOTHING;

-- Expediente privado. Los campos `*_encrypted` llegan cifrados por la
-- aplicación con la clave EXCLUSIVA de ofertas; `idempotency_key`
-- guarda solo el hash de la llave (nunca la llave en claro) y
-- `request_fingerprint` el hash del cuerpo para detectar reuso con
-- contenido distinto. `account_id` es identidad OPACA de
-- identity-service: sin FK entre servicios. Sin índices sobre columnas
-- cifradas.
CREATE TABLE IF NOT EXISTS disaster_service.aid_offers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tracking_code TEXT NOT NULL UNIQUE,
    kind disaster_service.aid_offer_kind NOT NULL,
    account_id UUID NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    related_disaster_id UUID
        REFERENCES disaster_service.disaster_events(id),
    title_encrypted BYTEA NOT NULL,
    description_encrypted BYTEA NOT NULL,
    area_reference_encrypted BYTEA NOT NULL,
    exact_address_encrypted BYTEA,
    latitude_encrypted BYTEA,
    longitude_encrypted BYTEA,
    contact_name_encrypted BYTEA NOT NULL,
    contact_phone_encrypted BYTEA,
    contact_email_encrypted BYTEA,
    department TEXT NOT NULL,
    municipality TEXT NOT NULL,
    available_from TIMESTAMPTZ NOT NULL,
    available_until TIMESTAMPTZ NOT NULL,
    availability_status disaster_service.aid_offer_availability
        NOT NULL DEFAULT 'scheduled',
    moderation_status disaster_service.aid_offer_moderation_status
        NOT NULL DEFAULT 'under_review',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    decided_by UUID,
    archived_at TIMESTAMPTZ,
    archived_by UUID,
    needs_information BOOLEAN NOT NULL DEFAULT FALSE,
    truth_confirmed BOOLEAN NOT NULL CHECK (truth_confirmed),
    contact_consent BOOLEAN NOT NULL CHECK (contact_consent),
    review_acknowledged BOOLEAN NOT NULL CHECK (review_acknowledged),
    public_summary_consent BOOLEAN NOT NULL
        CHECK (public_summary_consent),
    consent_recorded_at TIMESTAMPTZ NOT NULL,
    legal_text_version TEXT NOT NULL,
    CONSTRAINT aid_offer_idempotency_unique
        UNIQUE (account_id, idempotency_key),
    CONSTRAINT aid_offer_contact_required CHECK (
        contact_phone_encrypted IS NOT NULL
        OR contact_email_encrypted IS NOT NULL
    ),
    CONSTRAINT aid_offer_coordinates_pair CHECK (
        (latitude_encrypted IS NULL) = (longitude_encrypted IS NULL)
    ),
    CONSTRAINT aid_offer_window_valid
        CHECK (available_until > available_from)
);

-- Índices del handoff: propietario, moderación y expiración.
CREATE INDEX IF NOT EXISTS aid_offers_owner_idx
    ON disaster_service.aid_offers
    (account_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS aid_offers_moderation_idx
    ON disaster_service.aid_offers
    (moderation_status, received_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS aid_offers_expiration_idx
    ON disaster_service.aid_offers
    (availability_status, available_until);

-- Detalle privado de comida comunitaria.
CREATE TABLE IF NOT EXISTS disaster_service.community_meal_offer_details (
    offer_id UUID PRIMARY KEY
        REFERENCES disaster_service.aid_offers(id) ON DELETE CASCADE,
    servings_available INTEGER NOT NULL CHECK (servings_available >= 0),
    distribution_mode disaster_service.meal_distribution_mode NOT NULL,
    meal_description_encrypted BYTEA NOT NULL,
    allergen_information_encrypted BYTEA,
    food_safety_confirmed BOOLEAN NOT NULL
        CHECK (food_safety_confirmed)
);

-- Detalle privado de alojamiento temporal.
CREATE TABLE IF NOT EXISTS disaster_service.temporary_shelter_offer_details (
    offer_id UUID PRIMARY KEY
        REFERENCES disaster_service.aid_offers(id) ON DELETE CASCADE,
    spaces_available INTEGER NOT NULL CHECK (spaces_available >= 0),
    shared_space BOOLEAN NOT NULL,
    accepts_pets BOOLEAN,
    accessibility_notes_encrypted BYTEA,
    shelter_safety_confirmed BOOLEAN NOT NULL
        CHECK (shelter_safety_confirmed)
);

-- Constraint trigger DIFERIBLE: al cerrar la transacción cada oferta
-- debe tener exactamente UNA tabla de detalle y esta debe corresponder
-- al `kind`; jamás pueden coexistir ambas.
CREATE OR REPLACE FUNCTION disaster_service.enforce_aid_offer_detail()
RETURNS trigger AS $$
DECLARE
    target_id UUID;
    offer RECORD;
    meal_count INTEGER;
    shelter_count INTEGER;
BEGIN
    IF TG_TABLE_NAME = 'aid_offers' THEN
        target_id := NEW.id;
    ELSIF TG_OP = 'DELETE' THEN
        target_id := OLD.offer_id;
    ELSE
        target_id := NEW.offer_id;
    END IF;
    SELECT id, kind INTO offer
    FROM disaster_service.aid_offers
    WHERE id = target_id;
    IF offer.id IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT COUNT(*) INTO meal_count
    FROM disaster_service.community_meal_offer_details
    WHERE offer_id = offer.id;
    SELECT COUNT(*) INTO shelter_count
    FROM disaster_service.temporary_shelter_offer_details
    WHERE offer_id = offer.id;
    IF meal_count + shelter_count <> 1 THEN
        RAISE EXCEPTION
            'La oferta % debe tener exactamente un detalle (CHG-044).',
            offer.id;
    END IF;
    IF offer.kind = 'community_meal' AND meal_count <> 1 THEN
        RAISE EXCEPTION
            'La oferta % de comida requiere su detalle (CHG-044).',
            offer.id;
    END IF;
    IF offer.kind = 'temporary_shelter' AND shelter_count <> 1 THEN
        RAISE EXCEPTION
            'La oferta % de alojamiento requiere su detalle (CHG-044).',
            offer.id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS aid_offer_detail_consistency
    ON disaster_service.aid_offers;
CREATE CONSTRAINT TRIGGER aid_offer_detail_consistency
    AFTER INSERT ON disaster_service.aid_offers
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION disaster_service.enforce_aid_offer_detail();

DROP TRIGGER IF EXISTS aid_offer_meal_detail_consistency
    ON disaster_service.community_meal_offer_details;
CREATE CONSTRAINT TRIGGER aid_offer_meal_detail_consistency
    AFTER INSERT OR DELETE ON disaster_service.community_meal_offer_details
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION disaster_service.enforce_aid_offer_detail();

DROP TRIGGER IF EXISTS aid_offer_shelter_detail_consistency
    ON disaster_service.temporary_shelter_offer_details;
CREATE CONSTRAINT TRIGGER aid_offer_shelter_detail_consistency
    AFTER INSERT OR DELETE
    ON disaster_service.temporary_shelter_offer_details
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION disaster_service.enforce_aid_offer_detail();

-- Proyección pública SEPARADA: identificador y código públicos propios,
-- texto saneado por allowlist y SOLO zona aproximada. La tabla no tiene
-- cuenta, contacto, dirección, coordenadas ni archivos privados;
-- `offer_id` es un enlace interno que jamás sale en respuestas.
CREATE TABLE IF NOT EXISTS disaster_service.aid_offer_publications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    offer_id UUID NOT NULL UNIQUE
        REFERENCES disaster_service.aid_offers(id) ON DELETE CASCADE,
    public_offer_code TEXT NOT NULL UNIQUE,
    kind disaster_service.aid_offer_kind NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    department TEXT NOT NULL,
    municipality TEXT NOT NULL,
    area_reference TEXT NOT NULL,
    availability_status disaster_service.aid_offer_availability
        NOT NULL DEFAULT 'scheduled',
    available_from TIMESTAMPTZ NOT NULL,
    available_until TIMESTAMPTZ NOT NULL,
    available_units INTEGER NOT NULL CHECK (available_units >= 0),
    capacity_unit disaster_service.aid_offer_capacity_unit NOT NULL,
    distribution_mode disaster_service.meal_distribution_mode,
    meal_description TEXT,
    allergen_information TEXT,
    shared_space BOOLEAN,
    accepts_pets BOOLEAN,
    accessibility_notes TEXT,
    verification_status disaster_service.verification_status NOT NULL
        DEFAULT 'verified',
    source_id UUID NOT NULL
        REFERENCES disaster_service.sources(id),
    publication_status disaster_service.case_publication_status
        NOT NULL DEFAULT 'under_review',
    data_classification disaster_service.data_classification NOT NULL
        DEFAULT 'demonstrative',
    published_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT aid_offer_publication_kind_unit CHECK (
        (kind = 'community_meal' AND capacity_unit = 'servings')
        OR (kind = 'temporary_shelter' AND capacity_unit = 'spaces')
    ),
    CONSTRAINT aid_offer_publication_meal_fields CHECK (
        kind <> 'community_meal'
        OR (
            distribution_mode IS NOT NULL
            AND meal_description IS NOT NULL
            AND shared_space IS NULL
            AND accepts_pets IS NULL
            AND accessibility_notes IS NULL
        )
    ),
    CONSTRAINT aid_offer_publication_shelter_fields CHECK (
        kind <> 'temporary_shelter'
        OR (
            shared_space IS NOT NULL
            AND distribution_mode IS NULL
            AND meal_description IS NULL
            AND allergen_information IS NULL
        )
    )
);

-- Índice del directorio. La búsqueda textual usa lower(unaccent(...))
-- en la consulta, como el resto del directorio: unaccent() no es
-- IMMUTABLE y no puede indexarse funcionalmente sin un wrapper.
CREATE INDEX IF NOT EXISTS aid_offer_publications_directory_idx
    ON disaster_service.aid_offer_publications
    (kind, publication_status, availability_status,
     updated_at DESC, id DESC);
