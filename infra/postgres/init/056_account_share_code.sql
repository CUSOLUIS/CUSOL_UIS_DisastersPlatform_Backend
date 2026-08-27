-- CHG-215 — ID compartible por cuenta para vincular contactos de
-- emergencia sin reescribir datos. Formato CUSOL-XXXXXX con alfabeto
-- sin ambigüedades (sin 0/O/1/I/L). Idempotente, como todo /init.

ALTER TABLE identity_service.accounts
    ADD COLUMN IF NOT EXISTS share_code text;

CREATE UNIQUE INDEX IF NOT EXISTS accounts_share_code_idx
    ON identity_service.accounts (share_code)
    WHERE share_code IS NOT NULL;

-- Relleno para cuentas existentes (una por una, con reintento ante la
-- colisión improbable). Las nuevas lo reciben del identity-service.
DO $$
DECLARE
    fila RECORD;
    alfabeto CONSTANT text := '23456789ABCDEFGHJKMNPQRSTUVWXYZ';
    codigo text;
    intento int;
BEGIN
    FOR fila IN
        SELECT id FROM identity_service.accounts WHERE share_code IS NULL
    LOOP
        FOR intento IN 1..20 LOOP
            SELECT 'CUSOL-' || string_agg(
                       substr(alfabeto, (floor(random() * 31) + 1)::int, 1),
                       ''
                   )
              INTO codigo
              FROM generate_series(1, 6);
            BEGIN
                UPDATE identity_service.accounts
                   SET share_code = codigo
                 WHERE id = fila.id;
                EXIT;
            EXCEPTION WHEN unique_violation THEN
                -- colisión: probar otro código
            END;
        END LOOP;
    END LOOP;
END
$$;

-- Nombre visible de un contacto vinculado directamente por ID (la vía
-- por candidato lo saca del candidato; la directa no tiene candidato y
-- el navegador nunca decide nombres: lo fija el gateway desde la
-- cuenta, patrón CHG-193).
ALTER TABLE disaster_service.emergency_contacts
    ADD COLUMN IF NOT EXISTS direct_display_name text;
