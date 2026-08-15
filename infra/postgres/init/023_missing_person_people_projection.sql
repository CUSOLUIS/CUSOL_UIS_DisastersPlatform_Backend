-- CHG-084 — El caso publicado también alimenta la "situación humana":
-- dashboard de cifras (/people/overview) y capa humana del mapa
-- (/people/map-overview) leen de disaster_service.people y
-- people_map_projection, tablas que los reportes ciudadanos nunca
-- tocaban (hallazgo del Claude de producción). El caso publicado crea
-- su fila en people (enlazada) y, con coordenadas, su proyección
-- humana 'approximate' (DEC-007 prohíbe 'exact').

ALTER TABLE disaster_service.people
    ADD COLUMN IF NOT EXISTS missing_person_case_id UUID UNIQUE
        REFERENCES disaster_service.missing_person_cases(id);

-- Backfill idempotente de los casos ya publicados sin fila en people.
DO $$
DECLARE
    pending RECORD;
    person_row_id UUID;
BEGIN
    FOR pending IN
        SELECT c.id AS case_id, c.display_name, c.public_status,
               c.municipality, c.department,
               r.last_seen_latitude, r.last_seen_longitude
        FROM disaster_service.missing_person_cases c
        INNER JOIN disaster_service.missing_person_reports r
            ON r.public_case_code = c.public_case_code
        WHERE c.publication_status = 'published'
          AND NOT EXISTS (
              SELECT 1 FROM disaster_service.people p
              WHERE p.missing_person_case_id = c.id
          )
    LOOP
        INSERT INTO disaster_service.people (
            source_id, display_name, status, location, related_event,
            latitude, longitude, missing_person_case_id
        ) VALUES (
            '11111111-1111-4111-8111-111111111109',
            pending.display_name,
            (CASE pending.public_status::text
                WHEN 'found' THEN 'confirmed_alive'
                WHEN 'deceased' THEN 'reported_deceased'
                ELSE 'missing'
            END)::disaster_service.human_status,
            pending.municipality || ', ' || pending.department,
            'Reporte ciudadano de persona desaparecida',
            pending.last_seen_latitude,
            pending.last_seen_longitude,
            pending.case_id
        )
        RETURNING id INTO person_row_id;

        IF pending.last_seen_latitude IS NOT NULL
           AND pending.last_seen_longitude IS NOT NULL THEN
            INSERT INTO disaster_service.people_map_projection (
                person_id, location, coordinate_precision,
                verification_status, visibility, data_classification,
                updated_at
            ) VALUES (
                person_row_id,
                ST_SetSRID(
                    ST_MakePoint(
                        pending.last_seen_longitude,
                        pending.last_seen_latitude
                    ),
                    4326
                )::geography,
                'approximate', 'unverified', 'published',
                'operational', NOW()
            )
            ON CONFLICT (person_id) DO NOTHING;
        END IF;
    END LOOP;
END
$$;
