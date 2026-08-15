-- CHG-091 — Búsqueda difusa de personas para prevenir duplicados.
--
-- pg_trgm aporta similarity(); unaccent no es IMMUTABLE (depende del
-- diccionario), así que para indexar se envuelve en una función que sí
-- lo declara, fijando el diccionario por esquema calificado.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE OR REPLACE FUNCTION disaster_service.immutable_unaccent(text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
STRICT
RETURN lower(public.unaccent('public.unaccent'::regdictionary, $1));

-- Índice de trigramas sobre el nombre público normalizado: acelera
-- similarity() y los LIKE '%...%' de la búsqueda existente.
CREATE INDEX IF NOT EXISTS missing_person_cases_display_name_trgm_idx
    ON disaster_service.missing_person_cases
    USING gin (
        disaster_service.immutable_unaccent(display_name)
        gin_trgm_ops
    );
