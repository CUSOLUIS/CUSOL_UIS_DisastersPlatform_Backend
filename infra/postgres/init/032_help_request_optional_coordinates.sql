-- CHG-127 — Las coordenadas de «Necesitamos ayuda» dejan de ser
-- obligatorias: la dirección escrita basta. Si viajan, van las dos
-- (DEC-127-01); sin par no hay marcador en el mapa (DEC-127-02).
-- Migración aditiva e idempotente: no borra ni reescribe filas.

ALTER TABLE disaster_service.help_requests
    ALTER COLUMN latitude DROP NOT NULL;

ALTER TABLE disaster_service.help_requests
    ALTER COLUMN longitude DROP NOT NULL;

DO $$
BEGIN
    ALTER TABLE disaster_service.help_requests
        ADD CONSTRAINT help_request_coordinates_pair CHECK (
            (latitude IS NULL) = (longitude IS NULL)
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
