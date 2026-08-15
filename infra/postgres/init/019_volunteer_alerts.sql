-- CHG-069 — Alertas ciudadanas de voluntariado ("Mi espacio").
-- Un usuario registrado activa una alerta de que se necesita gente en
-- un punto (dirección + coordenadas + descripción). La alerta proyecta
-- un marcador público en el mapa operativo con la nueva categoría
-- volunteers_needed y puede resolverse por su propia autora o autor.

ALTER TYPE disaster_service.operational_map_category
    ADD VALUE IF NOT EXISTS 'volunteers_needed';

INSERT INTO disaster_service.sources (id, name, source_type, url)
VALUES (
    '11111111-1111-4111-8111-111111111108',
    'Alerta ciudadana de voluntariado — plataforma CUSOL',
    'citizen',
    NULL
)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS disaster_service.volunteer_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL,
    description TEXT NOT NULL,
    address TEXT NOT NULL,
    latitude DOUBLE PRECISION NOT NULL
        CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL
        CHECK (longitude BETWEEN -180 AND 180),
    map_point_id UUID
        REFERENCES disaster_service.operational_map_points(id)
        ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'resolved')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT volunteer_alert_description_not_blank
        CHECK (btrim(description) <> ''),
    CONSTRAINT volunteer_alert_address_not_blank
        CHECK (btrim(address) <> '')
);

CREATE INDEX IF NOT EXISTS volunteer_alerts_account_idx
    ON disaster_service.volunteer_alerts (account_id, created_at DESC);
