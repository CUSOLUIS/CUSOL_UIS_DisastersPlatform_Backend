-- CHG-166 — Calificación por estrellas (1-5) en los comentarios de
-- Centros de Acopio Local. Nullable: los comentarios previos a la
-- mejora no inventan calificación y no cuentan en el promedio; los
-- nuevos la exigen desde la aplicación. El promedio del centro se
-- calcula al leer (AVG sobre comentarios con estrellas), sin columna
-- desnormalizada. Idempotente; preserva datos existentes.

ALTER TABLE disaster_service.aid_location_comments
    ADD COLUMN IF NOT EXISTS rating SMALLINT
        CHECK (rating BETWEEN 1 AND 5);
