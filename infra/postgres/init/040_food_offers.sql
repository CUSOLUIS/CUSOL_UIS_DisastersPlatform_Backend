-- CHG-163 — «Ofrecer comida»: ofertas comunitarias de alimentos con
-- las reglas de «Necesitamos ayuda» (CHG-125/127/130/131): creación
-- anónima o con cuenta, vigencia 1-720 horas calculada en servidor,
-- coordenadas opcionales en par y radio de aviso que exige punto.
-- La expiración NUNCA borra ni muta filas: toda consulta filtra
-- expires_at > NOW() en servidor. No se proyecta a
-- operational_map_points: el mapa las pinta desde el endpoint
-- dedicado, que sí conoce la vigencia (patrón DEC-125-10).

CREATE TABLE IF NOT EXISTS disaster_service.food_offers (
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT food_offer_description_not_blank
        CHECK (btrim(description) <> ''),
    CONSTRAINT food_offer_address_not_blank
        CHECK (btrim(address) <> ''),
    CONSTRAINT food_offer_expires_after_creation
        CHECK (expires_at > created_at),
    CONSTRAINT food_offer_coordinates_pair
        CHECK ((latitude IS NULL) = (longitude IS NULL)),
    CONSTRAINT food_offer_radius_range
        CHECK (
            notification_radius_km IS NULL
            OR notification_radius_km BETWEEN 1 AND 100
        ),
    CONSTRAINT food_offer_radius_needs_coordinates
        CHECK (notification_radius_km IS NULL OR latitude IS NOT NULL)
);

-- Listados activos: filtro por vigencia + orden por creación.
CREATE INDEX IF NOT EXISTS food_offers_active_idx
    ON disaster_service.food_offers (expires_at, created_at DESC, id DESC);
