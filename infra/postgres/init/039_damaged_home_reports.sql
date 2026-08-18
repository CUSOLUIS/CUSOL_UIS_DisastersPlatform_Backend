-- CHG-162 — «Mi casita partida»: informe ciudadano de un hogar en muy
-- malas condiciones. Publicación inmediata (CHG-075) con retiro por
-- moderación en fase posterior; sale en el mapa con categoría propia.
-- Idempotente.

CREATE TABLE IF NOT EXISTS disaster_service.damaged_home_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key TEXT UNIQUE,
    -- Anónimo permitido, como los reportes ciudadanos.
    account_id UUID,
    description TEXT NOT NULL CHECK (btrim(description) <> ''),
    department TEXT NOT NULL,
    municipality TEXT NOT NULL,
    address TEXT NOT NULL,
    latitude DOUBLE PRECISION
        CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION
        CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
    -- Retiro lógico (moderación futura); nunca borrado físico aquí.
    visible BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS damaged_home_reports_visible_idx
    ON disaster_service.damaged_home_reports (visible, updated_at DESC);
