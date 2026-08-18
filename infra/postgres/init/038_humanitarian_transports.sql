-- CHG-161 — «La mulera» y «La lanchera»: transporte de insumos entre
-- un centro de acopio local (origen) y un centro de acopio receptor
-- (destino), con cuenta obligatoria y última posición para el rastreo
-- (F2/F3). Idempotente.

CREATE TABLE IF NOT EXISTS disaster_service.humanitarian_transports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Reintento seguro del alta.
    idempotency_key TEXT UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('mule', 'boat')),
    -- Trazabilidad: el transporte siempre tiene responsable con cuenta.
    account_id UUID NOT NULL,
    origin_municipality TEXT NOT NULL,
    destination_municipality TEXT NOT NULL,
    origin_location_id UUID NOT NULL
        REFERENCES disaster_service.aid_locations(id) ON DELETE RESTRICT,
    destination_location_id UUID NOT NULL
        REFERENCES disaster_service.aid_locations(id) ON DELETE RESTRICT,
    supplies_summary TEXT,
    status TEXT NOT NULL DEFAULT 'registered'
        CHECK (status IN ('registered', 'in_transit', 'arrived', 'cancelled')),
    -- Última posición conocida (la alimentará el rastreo de F2/F3).
    last_latitude DOUBLE PRECISION
        CHECK (last_latitude IS NULL OR last_latitude BETWEEN -90 AND 90),
    last_longitude DOUBLE PRECISION
        CHECK (last_longitude IS NULL OR last_longitude BETWEEN -180 AND 180),
    last_position_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS humanitarian_transports_status_idx
    ON disaster_service.humanitarian_transports (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS humanitarian_transports_account_idx
    ON disaster_service.humanitarian_transports (account_id, created_at DESC);
