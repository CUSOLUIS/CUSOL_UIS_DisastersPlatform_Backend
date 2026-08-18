-- CHG-153 — Reestructuración logística: cuatro tipos de puntos.
--
-- Evoluciona disaster_service.aid_locations (NO tabla nueva): amplía el
-- enum de tipo con `receiver_center` (acopio receptor) y
-- `distribution_point` (distribución), sumándose a los ya existentes
-- `collection_center` (acopio local) y `collection_point` (recolección).
-- Añade coordenadas (hoy no había), dependencia `parent_id`, estado
-- operativo, y datos de la ficha. Crea la tabla de denuncias con dedup
-- por denunciante (cuentas y anónimos por fingerprint) para el umbral
-- de 10 → EN_OBSERVACION. Idempotente.

-- 1. Tipos logísticos nuevos (P3: evolucionar 2 + añadir 2).
ALTER TYPE disaster_service.aid_location_kind
    ADD VALUE IF NOT EXISTS 'receiver_center';
ALTER TYPE disaster_service.aid_location_kind
    ADD VALUE IF NOT EXISTS 'distribution_point';

-- 2. Estado operativo amable (ABIERTO/CERRADO/CAPACIDAD_COMPLETA/
--    EN_OBSERVACION/INACTIVO). `under_observation` lo fija el umbral de
--    denuncias, nunca borra.
DO $$
BEGIN
    CREATE TYPE disaster_service.aid_location_operational_status AS ENUM (
        'open',
        'closed',
        'at_capacity',
        'under_observation',
        'inactive'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- 3. Evolución de aid_locations: coordenadas, dependencia y ficha.
ALTER TABLE disaster_service.aid_locations
    -- Alta idempotente (reintento seguro en despliegues). Los seed
    -- previos quedan con NULL (múltiples NULL permitidos en UNIQUE).
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT UNIQUE,
    -- Cuenta creadora (para auditoría/revisión admin); NULL si anónima.
    ADD COLUMN IF NOT EXISTS created_by_account_id UUID,
    -- Coordenadas para el mapa (antes solo había texto de ubicación).
    ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION
        CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION
        CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
    -- Dependencia jerárquica: recolección→acopio local,
    -- distribución→acopio receptor. RESTRICT: un centro con hijos no se
    -- borra físicamente (se prefiere el manejo lógico/moderación;
    -- CHG-153 §25 "no cascade delete destructivo").
    ADD COLUMN IF NOT EXISTS parent_id UUID
        REFERENCES disaster_service.aid_locations(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS address TEXT,
    ADD COLUMN IF NOT EXISTS schedule TEXT,
    ADD COLUMN IF NOT EXISTS contact TEXT,
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS operational_status
        disaster_service.aid_location_operational_status
        NOT NULL DEFAULT 'open';

-- Las coordenadas viajan en pareja o ninguna.
DO $$
BEGIN
    ALTER TABLE disaster_service.aid_locations
        ADD CONSTRAINT aid_location_coords_paired
        CHECK ((latitude IS NULL) = (longitude IS NULL));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- Un punto dependiente no puede referirse a sí mismo.
DO $$
BEGIN
    ALTER TABLE disaster_service.aid_locations
        ADD CONSTRAINT aid_location_parent_not_self
        CHECK (parent_id IS NULL OR parent_id <> id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- Búsqueda del padre por ciudad al validar dependencias / proyectar.
CREATE INDEX IF NOT EXISTS aid_locations_parent_idx
    ON disaster_service.aid_locations (parent_id);
CREATE INDEX IF NOT EXISTS aid_locations_kind_city_idx
    ON disaster_service.aid_locations (kind, municipality);

-- 4. Denuncias sobre lugares de ayuda (CHG-153 §11-13). Clon del patrón
--    de valoraciones: dual anónimo/autenticado, moderación, dedup.
--    `denouncer_key` = id de cuenta (autenticado) o hash de fingerprint
--    (anónimo) para que un mismo denunciante NO cuente dos veces y el
--    umbral de 10 sea resistente a abuso (P1: anónimos cuentan, con
--    antiabuso por fingerprint/IP + rate-limit + idempotencia).
CREATE TABLE IF NOT EXISTS disaster_service.aid_location_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    location_id UUID NOT NULL
        REFERENCES disaster_service.aid_locations(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL UNIQUE,
    actor_kind disaster_service.contribution_actor_kind NOT NULL,
    account_id UUID,
    -- Clave de deduplicación del denunciante (cuenta o fingerprint).
    denouncer_key TEXT NOT NULL,
    -- Motivo cifrado por la aplicación (Fernet); solo lo ve el admin.
    reason_encrypted BYTEA,
    moderation_status disaster_service.contribution_status NOT NULL
        DEFAULT 'under_review',
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Un denunciante, una denuncia viva por lugar (antiabuso del umbral).
    CONSTRAINT aid_location_report_one_per_denouncer
        UNIQUE (location_id, denouncer_key)
);

-- Conteo del umbral por lugar (denuncias no rechazadas/no archivadas).
CREATE INDEX IF NOT EXISTS aid_location_reports_active_idx
    ON disaster_service.aid_location_reports
    (location_id, moderation_status)
    WHERE archived_at IS NULL;
