-- CHG-208 — Monitoreo sísmico, intensidad instrumental y red privada de
-- emergencia. Subsistema de notificación sísmica rápida POSTERIOR a la
-- detección (no predice): el SGC caracteriza el evento, CUSOL lo consume,
-- deriva zonas de sacudida (polígonos = información; círculos animados =
-- solo representación) y activa una red privada de hasta cinco contactos
-- por usuario, con opt-in explícito y autorización resuelta en backend.
--
-- Idempotente; no borra ni altera ninguna fila existente.

-- 1) El evento sísmico. Una fila por terremoto real o simulado; las
--    revisiones del SGC NO crean eventos nuevos (idempotencia por
--    source + source_event_id) sino filas en seismic_event_revisions.
CREATE TABLE IF NOT EXISTS disaster_service.seismic_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- 'SGC' para el catálogo oficial; 'SIMULATED' para el generador
    -- del Super Admin (spec §66: identificable siempre).
    source TEXT NOT NULL DEFAULT 'SGC'
        CHECK (source IN ('SGC', 'SIMULATED')),
    source_event_id TEXT NOT NULL,
    source_location_solution_id TEXT,
    source_magnitude_solution_id TEXT,
    origin_time_utc TIMESTAMPTZ NOT NULL,
    magnitude DOUBLE PRECISION NOT NULL
        CHECK (magnitude BETWEEN -2 AND 10),
    depth_km DOUBLE PRECISION
        CHECK (depth_km IS NULL OR depth_km BETWEEN 0 AND 800),
    latitude DOUBLE PRECISION NOT NULL
        CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL
        CHECK (longitude BETWEEN -180 AND 180),
    epicenter GEOGRAPHY(Point, 4326) NOT NULL,
    municipality_code TEXT,
    department_code TEXT,
    magnitude_source TEXT,
    location_source TEXT,
    -- Descripción libre solo para simulacros (spec §63).
    description TEXT,
    first_detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_payload JSONB,
    is_simulated BOOLEAN NOT NULL DEFAULT FALSE,
    simulated_by_account_id UUID,
    -- Spec §67: un simulacro jamás alerta cuentas reales salvo orden
    -- explícita del administrador que lo genera.
    notify_real_users BOOLEAN NOT NULL DEFAULT TRUE,
    -- Fase de los DATOS (spec §11): preliminar hasta que llegue
    -- intensidad instrumental. Los círculos son animación; esto no.
    processing_status TEXT NOT NULL DEFAULT 'SEISMIC_DATA_PRELIMINARY'
        CHECK (processing_status IN (
            'SEISMIC_DATA_PRELIMINARY', 'SEISMIC_DATA_INSTRUMENTAL'
        )),
    -- Un simulacro retirado no se borra: queda para auditoría.
    deactivated_at TIMESTAMPTZ,
    CONSTRAINT seismic_event_source_unique
        UNIQUE (source, source_event_id),
    CONSTRAINT seismic_event_simulated_coherent
        CHECK (
            (is_simulated AND source = 'SIMULATED')
            OR (NOT is_simulated AND source <> 'SIMULATED')
        )
);

CREATE INDEX IF NOT EXISTS seismic_events_origin_time_idx
    ON disaster_service.seismic_events (origin_time_utc DESC);
CREATE INDEX IF NOT EXISTS seismic_events_active_idx
    ON disaster_service.seismic_events (deactivated_at)
    WHERE deactivated_at IS NULL;

-- 2) Histórico de soluciones (spec §10): nunca sobrescribir en
--    silencio. Cada corrección del SGC deja la versión anterior aquí.
CREATE TABLE IF NOT EXISTS disaster_service.seismic_event_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seismic_event_id UUID NOT NULL
        REFERENCES disaster_service.seismic_events (id)
        ON DELETE CASCADE,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    magnitude DOUBLE PRECISION NOT NULL,
    depth_km DOUBLE PRECISION,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    origin_time_utc TIMESTAMPTZ NOT NULL,
    source_location_solution_id TEXT,
    source_magnitude_solution_id TEXT,
    source_payload JSONB,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT seismic_revision_unique
        UNIQUE (seismic_event_id, revision_number)
);

-- 3) Productos instrumentales del SGC (spec §77): PGA, PGV, grillas,
--    KML, shapes. La arquitectura los registra aunque el adaptador de
--    descarga llegue después (D5 del expediente).
CREATE TABLE IF NOT EXISTS disaster_service.seismic_instrumental_products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seismic_event_id UUID NOT NULL
        REFERENCES disaster_service.seismic_events (id)
        ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'SGC',
    product_type TEXT NOT NULL
        CHECK (product_type IN (
            'INTENSITY', 'PGA', 'PGV', 'GRID', 'KML', 'SHAPE'
        )),
    source_url TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    checksum TEXT,
    raw_metadata JSONB,
    processing_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (processing_status IN ('PENDING', 'PROCESSED', 'FAILED'))
);

CREATE INDEX IF NOT EXISTS seismic_products_event_idx
    ON disaster_service.seismic_instrumental_products (seismic_event_id);

-- 4) Zonas de sacudida (spec §15-§20): MULTIPOLYGON real, jamás radios
--    convertidos en verdad. El origen de cada zona queda declarado:
--    instrumental oficial, estimación propia provisional o simulacro.
CREATE TABLE IF NOT EXISTS disaster_service.seismic_intensity_zones (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seismic_event_id UUID NOT NULL
        REFERENCES disaster_service.seismic_events (id)
        ON DELETE CASCADE,
    source TEXT NOT NULL
        CHECK (source IN (
            'SGC_INSTRUMENTAL', 'PROVISIONAL_ESTIMATE', 'SIMULATED'
        )),
    intensity_min DOUBLE PRECISION,
    intensity_max DOUBLE PRECISION,
    pga_min DOUBLE PRECISION,
    pga_max DOUBLE PRECISION,
    pgv_min DOUBLE PRECISION,
    pgv_max DOUBLE PRECISION,
    -- Tres niveles operativos para el ciudadano (spec §16-18).
    severity_level TEXT NOT NULL
        CHECK (severity_level IN ('STRONG', 'MODERATE', 'LIGHT')),
    geometry GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_revision INTEGER,
    -- Cuando llega la intensidad instrumental, las zonas provisionales
    -- se reemplazan SIN borrarse (spec §23: auditoría de ambos).
    superseded_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS seismic_zones_event_idx
    ON disaster_service.seismic_intensity_zones (seismic_event_id)
    WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS seismic_zones_geometry_idx
    ON disaster_service.seismic_intensity_zones USING GIST (geometry);

-- 5) Opt-in por usuario (spec §24): OFF por defecto, siempre.
CREATE TABLE IF NOT EXISTS disaster_service.user_seismic_settings (
    account_id UUID PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    -- Instantánea del nombre visible, rellenada por el gateway desde
    -- la sesión (patrón CHG-193): el navegador no elige cómo figura
    -- nadie. Es lo que ven SOLO los contactos aceptados en el panel.
    display_name TEXT,
    -- Cuentas semilla del simulador (spec §67-68); jamás se marca
    -- desde la API pública.
    is_test_account BOOLEAN NOT NULL DEFAULT FALSE,
    enabled_at TIMESTAMPTZ,
    disabled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6) Contacto provisional (spec §28): la persona elegida aún no tiene
--    cuenta. El documento va cifrado (Fernet de reportes) y el matching
--    usa hashes/normalizados, nunca el dato en claro ni el nombre solo.
CREATE TABLE IF NOT EXISTS disaster_service.emergency_contact_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_by_account_id UUID NOT NULL,
    first_names TEXT NOT NULL CHECK (btrim(first_names) <> ''),
    last_names TEXT NOT NULL CHECK (btrim(last_names) <> ''),
    document_type TEXT NOT NULL,
    document_encrypted BYTEA NOT NULL,
    -- SHA-256 del documento normalizado: permite casar sin descifrar.
    document_hash TEXT NOT NULL,
    phone_normalized TEXT NOT NULL CHECK (btrim(phone_normalized) <> ''),
    -- Nombre normalizado (minúsculas, sin tildes, espacios colapsados)
    -- SOLO para verificar coherencia de una coincidencia fuerte.
    name_normalized TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'UNREGISTERED'
        CHECK (status IN ('UNREGISTERED', 'MATCHED')),
    matched_account_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    matched_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS emergency_candidates_document_idx
    ON disaster_service.emergency_contact_candidates (document_hash)
    WHERE status = 'UNREGISTERED';
CREATE INDEX IF NOT EXISTS emergency_candidates_phone_idx
    ON disaster_service.emergency_contact_candidates (phone_normalized)
    WHERE status = 'UNREGISTERED';

-- 7) El vínculo direccional (spec §35-36): A→B no implica B→A. Activo
--    únicamente tras aceptación explícita de B.
CREATE TABLE IF NOT EXISTS disaster_service.emergency_contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_account_id UUID NOT NULL,
    contact_account_id UUID,
    candidate_id UUID
        REFERENCES disaster_service.emergency_contact_candidates (id),
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'ACCEPTED', 'REJECTED', 'REVOKED')),
    accepted_at TIMESTAMPTZ,
    rejected_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- O apunta a una cuenta o a un candidato provisional; nunca a nada.
    CONSTRAINT emergency_contact_target
        CHECK (contact_account_id IS NOT NULL OR candidate_id IS NOT NULL),
    CONSTRAINT emergency_contact_not_self
        CHECK (contact_account_id IS NULL
               OR contact_account_id <> owner_account_id)
);

-- Un mismo par dueño→contacto no se duplica mientras esté vivo.
CREATE UNIQUE INDEX IF NOT EXISTS emergency_contacts_pair_live_idx
    ON disaster_service.emergency_contacts (owner_account_id, contact_account_id)
    WHERE contact_account_id IS NOT NULL
      AND status IN ('PENDING', 'ACCEPTED');
CREATE INDEX IF NOT EXISTS emergency_contacts_owner_idx
    ON disaster_service.emergency_contacts (owner_account_id);
CREATE INDEX IF NOT EXISTS emergency_contacts_contact_idx
    ON disaster_service.emergency_contacts (contact_account_id)
    WHERE contact_account_id IS NOT NULL;

-- 8) La alerta por usuario afectado (spec §38): guarda el SNAPSHOT de
--    la ubicación usada — si la persona luego se mueve, queda registro
--    de dónde estaba al activarse. Es "última ubicación conocida",
--    nunca "ubicación actual" (spec §37).
CREATE TABLE IF NOT EXISTS disaster_service.seismic_user_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seismic_event_id UUID NOT NULL
        REFERENCES disaster_service.seismic_events (id)
        ON DELETE CASCADE,
    account_id UUID NOT NULL,
    zone_id UUID
        REFERENCES disaster_service.seismic_intensity_zones (id),
    severity_level TEXT NOT NULL
        CHECK (severity_level IN ('STRONG', 'MODERATE', 'LIGHT')),
    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'SAFE_CONFIRMED', 'EXPIRED')),
    event_latitude DOUBLE PRECISION NOT NULL
        CHECK (event_latitude BETWEEN -90 AND 90),
    event_longitude DOUBLE PRECISION NOT NULL
        CHECK (event_longitude BETWEEN -180 AND 180),
    event_location_accuracy REAL
        CHECK (event_location_accuracy IS NULL
               OR event_location_accuracy >= 0),
    event_location_timestamp TIMESTAMPTZ,
    resolved_address TEXT,
    -- NULL cuando M >= 4.5: persiste hasta «ESTOY BIEN» (spec §52-53).
    expires_at TIMESTAMPTZ,
    safe_confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT seismic_alert_once_per_event
        UNIQUE (seismic_event_id, account_id)
);

CREATE INDEX IF NOT EXISTS seismic_alerts_account_idx
    ON disaster_service.seismic_user_alerts (account_id);
CREATE INDEX IF NOT EXISTS seismic_alerts_event_status_idx
    ON disaster_service.seismic_user_alerts (seismic_event_id, status);

-- 9) Auditoría de acceso a ubicación sensible (spec §79): qué contacto
--    consultó qué ubicación, cuándo y por qué vía.
CREATE TABLE IF NOT EXISTS disaster_service.emergency_location_access_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    viewer_account_id UUID NOT NULL,
    affected_account_id UUID NOT NULL,
    seismic_event_id UUID,
    alert_id UUID,
    accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    access_type TEXT NOT NULL
        CHECK (access_type IN ('PANEL', 'MARKER'))
);

CREATE INDEX IF NOT EXISTS emergency_access_log_affected_idx
    ON disaster_service.emergency_location_access_log
    (affected_account_id, accessed_at DESC);

-- 10) Notificaciones emitidas (spec §56-59): fila auditable siempre;
--     el push móvil real llega con las credenciales del VPS (D4).
CREATE TABLE IF NOT EXISTS disaster_service.seismic_notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id UUID NOT NULL
        REFERENCES disaster_service.seismic_user_alerts (id)
        ON DELETE CASCADE,
    recipient_account_id UUID NOT NULL,
    kind TEXT NOT NULL
        CHECK (kind IN ('ALERT_ACTIVATED', 'SAFE_CONFIRMED')),
    channel TEXT NOT NULL DEFAULT 'RECORD'
        CHECK (channel IN ('RECORD', 'EMAIL', 'PUSH')),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS seismic_notifications_recipient_idx
    ON disaster_service.seismic_notifications
    (recipient_account_id, created_at DESC);

-- 11) Checkpoint del poller (spec §7/§82): dónde va la ingesta, para
--     retomar tras una interrupción con ventana ampliada y sin
--     redescargar el catálogo histórico.
CREATE TABLE IF NOT EXISTS disaster_service.seismic_poll_checkpoint (
    source TEXT PRIMARY KEY,
    last_event_time TIMESTAMPTZ,
    last_run_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
