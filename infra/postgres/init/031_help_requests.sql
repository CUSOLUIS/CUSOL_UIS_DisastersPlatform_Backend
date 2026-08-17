-- CHG-125 — «Necesitamos ayuda»: solicitudes públicas de emergencia
-- con vigencia en horas. La expiración NUNCA borra ni muta filas
-- (DEC-125-02): toda consulta filtra expires_at > NOW() en servidor.
-- No se proyecta a operational_map_points (DEC-125-10): el mapa las
-- pinta desde el endpoint dedicado, que sí conoce la vigencia.

CREATE TABLE IF NOT EXISTS disaster_service.help_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    public_code TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    reporter_account_id UUID,
    description TEXT NOT NULL,
    address TEXT NOT NULL,
    latitude DOUBLE PRECISION NOT NULL
        CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL
        CHECK (longitude BETWEEN -180 AND 180),
    duration_hours INTEGER NOT NULL
        CHECK (duration_hours BETWEEN 1 AND 72),
    photo_storage_key TEXT,
    photo_derived_storage_key TEXT,
    photo_content_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT help_request_description_not_blank
        CHECK (btrim(description) <> ''),
    CONSTRAINT help_request_address_not_blank
        CHECK (btrim(address) <> ''),
    CONSTRAINT help_request_expires_after_creation
        CHECK (expires_at > created_at)
);

-- Listados activos: filtro por vigencia + orden por creación.
CREATE INDEX IF NOT EXISTS help_requests_active_idx
    ON disaster_service.help_requests (expires_at, created_at DESC, id DESC);

-- DEC-125-03 — atención idempotente: la clave primaria compuesta
-- impide que la misma cuenta quede registrada dos veces.
CREATE TABLE IF NOT EXISTS disaster_service.help_request_attenders (
    help_request_id UUID NOT NULL
        REFERENCES disaster_service.help_requests(id) ON DELETE CASCADE,
    account_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (help_request_id, account_id)
);
