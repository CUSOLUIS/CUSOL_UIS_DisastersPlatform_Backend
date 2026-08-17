-- CHG-131 — Radio de aviso de «Necesitamos ayuda»: a cuántos km a la
-- redonda del punto de la solicitud se alerta en la app instalada.
-- Nulo cuando la solicitud no lo define; solo tiene sentido con
-- coordenadas (la solicitud de dirección escrita sola no puede medir
-- distancias). Aditivo e idempotente.

ALTER TABLE disaster_service.help_requests
    ADD COLUMN IF NOT EXISTS notification_radius_km INTEGER;

DO $$
BEGIN
    ALTER TABLE disaster_service.help_requests
        ADD CONSTRAINT help_request_radius_range CHECK (
            notification_radius_km IS NULL
            OR notification_radius_km BETWEEN 1 AND 100
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    ALTER TABLE disaster_service.help_requests
        ADD CONSTRAINT help_request_radius_needs_coordinates CHECK (
            notification_radius_km IS NULL OR latitude IS NOT NULL
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
