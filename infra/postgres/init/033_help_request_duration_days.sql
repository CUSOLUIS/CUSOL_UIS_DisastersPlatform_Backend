-- CHG-130 — La vigencia de «Necesitamos ayuda» admite horas (1-72) o
-- días (1-30, expresados en horas por el cliente): el tope de la
-- columna pasa de 72 a 720 horas. Aditivo e idempotente; expires_at
-- sigue calculándose en servidor.

ALTER TABLE disaster_service.help_requests
    DROP CONSTRAINT IF EXISTS help_requests_duration_hours_check;

DO $$
BEGIN
    ALTER TABLE disaster_service.help_requests
        ADD CONSTRAINT help_requests_duration_hours_check CHECK (
            duration_hours BETWEEN 1 AND 720
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;
