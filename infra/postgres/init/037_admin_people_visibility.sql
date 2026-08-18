-- CHG-154 — Gestión admin de registros de personas: ocultamiento
-- reversible. Nada se borra: oculto ⇔ hidden_at IS NOT NULL; las
-- lecturas públicas excluyen ocultos. `updated_at` alimenta la señal
-- de cambios cuando el admin oculta, restaura o edita.

ALTER TABLE disaster_service.people
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS hidden_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS hidden_by TEXT;

-- Las lecturas públicas filtran por visibilidad en cada consulta; el
-- índice parcial mantiene baratas la tabla y el mapa con muchos
-- ocultos acumulados a la espera del borrado definitivo.
CREATE INDEX IF NOT EXISTS people_visible_created_idx
    ON disaster_service.people (created_at DESC)
    WHERE hidden_at IS NULL;
