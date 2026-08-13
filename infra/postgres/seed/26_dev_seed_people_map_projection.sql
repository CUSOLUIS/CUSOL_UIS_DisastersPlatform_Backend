-- CHG-015 — Proyecciones geográficas SINTÉTICAS para las personas demo.
-- CHG-016 — v2: si la persona tiene coordenadas explícitas
-- (people.latitude/longitude) la proyección las usa con precisión
-- 'approximate'; si no, respaldo determinista por ancla de municipio.
-- Todo es demonstrative y approximate/municipality (DEC-007: nunca exact).
-- Idempotente: id y posición deterministas + ON CONFLICT DO UPDATE con
-- los mismos valores deterministas (refresca sin duplicar).
-- Regla intacta: para datos reales sin coordenadas NO se inventan
-- posiciones; esta semilla solo cubre personas demo con coordenadas o
-- etiqueta de municipio conocida.

BEGIN;

INSERT INTO disaster_service.people_map_projection (
    id, person_id, location, coordinate_precision, verification_status,
    visibility, data_classification, updated_at
)
SELECT
    md5('projection:' || p.id::text)::uuid,
    p.id,
    CASE WHEN p.latitude IS NOT NULL THEN
        ST_SetSRID(ST_MakePoint(p.longitude, p.latitude), 4326)::geography
    ELSE
        ST_SetSRID(ST_MakePoint(
            anchor.lon + (
                ((((('x' || substr(md5('lon:' || p.id::text), 1, 8))::bit(32)::int
                    % 1000) + 1000) % 1000) / 1000.0) - 0.5
            ) * anchor.spread,
            anchor.lat + (
                ((((('x' || substr(md5('lat:' || p.id::text), 1, 8))::bit(32)::int
                    % 1000) + 1000) % 1000) / 1000.0) - 0.5
            ) * anchor.spread
        ), 4326)::geography
    END,
    CASE WHEN p.latitude IS NOT NULL
        THEN 'approximate'::disaster_service.coordinate_precision
        ELSE anchor.coord_precision::disaster_service.coordinate_precision
    END,
    CASE WHEN p.source_id = '11111111-1111-4111-8111-111111111101'
        THEN 'verified'::disaster_service.verification_status
        ELSE 'under_review'::disaster_service.verification_status
    END,
    'published',
    'demonstrative',
    p.created_at
FROM disaster_service.people p
LEFT JOIN (VALUES
    ('Café Madrid, Bucaramanga',        7.1310, -73.1205, 0.020, 'approximate'),
    ('Girón, casco urbano',             7.0700, -73.1730, 0.020, 'approximate'),
    ('Vereda La Esperanza, Lebrija',    7.1132, -73.2181, 0.060, 'municipality'),
    ('Km 18 vía a Barrancabermeja',     7.0660, -73.3790, 0.020, 'approximate'),
    ('Piedecuesta, zona rural',         6.9500, -73.0300, 0.060, 'municipality'),
    ('Floridablanca, sector Bucarica',  7.0890, -73.1050, 0.020, 'approximate'),
    ('Los Santos, Santander',           6.8550, -73.1030, 0.060, 'municipality'),
    ('Barrio Colorados, Bucaramanga',   7.1310, -73.1150, 0.020, 'approximate'),
    ('Piedecuesta, casco urbano',       6.9870, -73.0490, 0.020, 'approximate'),
    ('Floridablanca, cerros orientales', 7.0800, -73.0700, 0.060, 'municipality')
) AS anchor(label, lat, lon, spread, coord_precision)
    ON anchor.label = p.location
WHERE p.latitude IS NOT NULL OR anchor.label IS NOT NULL
ON CONFLICT (person_id) DO UPDATE SET
    location = EXCLUDED.location,
    coordinate_precision = EXCLUDED.coordinate_precision,
    verification_status = EXCLUDED.verification_status,
    updated_at = EXCLUDED.updated_at;

COMMIT;
