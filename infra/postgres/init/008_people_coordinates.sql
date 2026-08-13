-- CHG-016 — Coordenadas explícitas opcionales por persona.
-- Permiten originar la proyección pública del mapa (siempre degradada a
-- 'approximate'; DEC-007 impide publicar 'exact'). Columnas internas: no
-- se exponen crudas en people/overview.
-- Idempotente: ADD COLUMN IF NOT EXISTS + constraints condicionales.

ALTER TABLE disaster_service.people
    ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;

DO $$
BEGIN
    ALTER TABLE disaster_service.people
        ADD CONSTRAINT people_coordinates_pair CHECK (
            (latitude IS NULL) = (longitude IS NULL)
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    ALTER TABLE disaster_service.people
        ADD CONSTRAINT people_coordinates_range CHECK (
            (latitude IS NULL OR latitude BETWEEN -90 AND 90)
            AND (longitude IS NULL OR longitude BETWEEN -180 AND 180)
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
