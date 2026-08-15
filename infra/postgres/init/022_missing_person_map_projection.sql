-- CHG-081 — El caso publicado de persona desaparecida se proyecta al
-- mapa operativo. Omisión estructural detectada en producción: los
-- casos nacían con map_point_id NULL y ningún flujo creaba su punto,
-- así que jamás podían aparecer en el mapa (a diferencia de edificios
-- y alertas de voluntariado, que sí crean el suyo).

INSERT INTO disaster_service.sources (id, name, source_type, url)
VALUES (
    '11111111-1111-4111-8111-111111111109',
    'Reporte ciudadano de persona desaparecida — plataforma CUSOL',
    'citizen',
    NULL
)
ON CONFLICT (id) DO NOTHING;

-- Backfill idempotente: casos ya publicados con coordenadas del
-- último avistamiento y sin punto en el mapa.
DO $$
DECLARE
    pending RECORD;
    point_id UUID;
BEGIN
    FOR pending IN
        SELECT c.id AS case_id, c.display_name, c.last_seen_area,
               c.municipality, c.department,
               r.last_seen_latitude, r.last_seen_longitude
        FROM disaster_service.missing_person_cases c
        INNER JOIN disaster_service.missing_person_reports r
            ON r.public_case_code = c.public_case_code
        WHERE c.publication_status = 'published'
          AND c.map_point_id IS NULL
          AND r.last_seen_latitude IS NOT NULL
          AND r.last_seen_longitude IS NOT NULL
    LOOP
        INSERT INTO disaster_service.operational_map_points (
            category, title, description, location_label, location,
            coordinate_precision, verification_status, source_id,
            data_classification, updated_at
        ) VALUES (
            'missing_person',
            pending.display_name,
            'Vista por última vez en ' || pending.last_seen_area,
            pending.municipality || ', ' || pending.department,
            ST_SetSRID(
                ST_MakePoint(
                    pending.last_seen_longitude,
                    pending.last_seen_latitude
                ),
                4326
            )::geography,
            'exact', 'unverified',
            '11111111-1111-4111-8111-111111111109',
            'operational', NOW()
        )
        RETURNING id INTO point_id;

        UPDATE disaster_service.missing_person_cases
        SET map_point_id = point_id, updated_at = NOW()
        WHERE id = pending.case_id;
    END LOOP;
END
$$;
