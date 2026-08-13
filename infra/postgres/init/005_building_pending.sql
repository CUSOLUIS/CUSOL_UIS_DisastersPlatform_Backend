-- CHG-010 — Quinta categoría del mapa operativo: edificios sin inspección
-- registrada. Cambio aditivo: no altera valores existentes del enum.
-- "building_pending" significa "sin inspección registrada"; no implica
-- inseguridad, colapso ni presencia de personas.
-- Idempotente: ADD VALUE IF NOT EXISTS; re-ejecutable sobre la base local.

ALTER TYPE disaster_service.operational_map_category
    ADD VALUE IF NOT EXISTS 'building_pending';
