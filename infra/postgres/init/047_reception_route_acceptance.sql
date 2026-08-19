-- CHG-175 — Etapa 2 del acuerdo de ruta: Mulera ↔ Centro de Acopio
-- Receptor. Idempotente. EXTIENDE la fila que ya crea CHG-174 en vez de
-- levantar una tabla paralela (§54, §86 del contrato): una sola fila por
-- transporte guarda las dos etapas, distinguibles y con códigos
-- distintos.
--
-- No se borra ni se altera nada existente. Los transportes históricos y
-- los que aún no llegaron a esta etapa quedan con estas columnas en
-- NULL: no se les inventa ninguna aceptación (§84-§85).

ALTER TABLE disaster_service.transport_route_acceptances
    -- Centro de destino que emite el código de la etapa 2.
    ADD COLUMN IF NOT EXISTS reception_center_id UUID
        REFERENCES disaster_service.aid_locations(id),
    -- Código propio del receptor: distinto del de la etapa 1 y no
    -- intercambiable con él (§25-§26).
    ADD COLUMN IF NOT EXISTS reception_confirmation_code TEXT,
    ADD COLUMN IF NOT EXISTS reception_code_used_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS reception_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS reception_started_by UUID,
    ADD COLUMN IF NOT EXISTS reception_mule_code_validated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS reception_mule_accepted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS reception_mule_accepted_by UUID,
    -- Sello del estado global: solo se escribe cuando las DOS
    -- relaciones están completas (§45-§46). No es un booleano que
    -- resuma el proceso: los estados de cada etapa se siguen
    -- consultando por separado (§51).
    ADD COLUMN IF NOT EXISTS route_accepted_at TIMESTAMPTZ;

-- El código del receptor tampoco puede repetirse entre transportes.
-- Parcial, porque queda NULL hasta que esa etapa arranca.
CREATE UNIQUE INDEX IF NOT EXISTS
    transport_route_acceptances_reception_code_idx
    ON disaster_service.transport_route_acceptances
    (reception_confirmation_code)
    WHERE reception_confirmation_code IS NOT NULL;
