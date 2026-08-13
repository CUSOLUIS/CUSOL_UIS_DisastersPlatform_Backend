-- CHG-018 — Índice para la paginación estable de la tabla de personas
-- (ORDER BY created_at DESC, id DESC). Idempotente.

CREATE INDEX IF NOT EXISTS people_created_at_id_idx
    ON disaster_service.people (created_at DESC, id DESC);
