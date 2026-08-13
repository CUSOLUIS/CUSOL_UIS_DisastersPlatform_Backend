-- CHG-015 — Coordenadas privadas opcionales del último avistamiento en el
-- reporte de persona desaparecida. Permanecen en el expediente privado:
-- nunca se publican automáticamente; solo tras moderación pueden originar
-- una proyección pública degradada en people_map_projection.
-- Idempotente: ADD COLUMN IF NOT EXISTS + constraint condicional.

ALTER TABLE disaster_service.missing_person_reports
    ADD COLUMN IF NOT EXISTS last_seen_latitude DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS last_seen_longitude DOUBLE PRECISION;

DO $$
BEGIN
    ALTER TABLE disaster_service.missing_person_reports
        ADD CONSTRAINT report_last_seen_coordinates_pair CHECK (
            (last_seen_latitude IS NULL) = (last_seen_longitude IS NULL)
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    ALTER TABLE disaster_service.missing_person_reports
        ADD CONSTRAINT report_last_seen_coordinates_range CHECK (
            (last_seen_latitude IS NULL
             OR last_seen_latitude BETWEEN -90 AND 90)
            AND (last_seen_longitude IS NULL
             OR last_seen_longitude BETWEEN -180 AND 180)
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
