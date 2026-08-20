-- CHG-182 — «Mi casita destruida»: publicación con cuenta, ayuda
-- directa a la familia y comunidad completa.
--
-- Reconstruye el informe de CHG-162 sobre una regla distinta: ahora
-- solo publica quien tiene cuenta, se cuenta cuántas personas viven en
-- la casa y se puede dejar un medio para recibir transferencias. La
-- casita gana además el CUARTO objetivo de las tablas comunitarias
-- (acopios CHG-165, ofertas CHG-176, solicitudes CHG-180) y un sello
-- para saber qué comentarios ya vio su dueño.
--
-- Idempotente; no borra ni altera ninguna fila existente. Las
-- publicaciones anteriores conservan sus datos: los campos nuevos
-- quedan en NULL y el histórico anónimo sigue visible.

-- 1) Datos propios de la casita.
ALTER TABLE disaster_service.damaged_home_reports
    -- Código público para citarla en avisos y correos, como el resto
    -- de publicaciones (CASA-2026-XXXXXXXX).
    ADD COLUMN IF NOT EXISTS public_code TEXT,
    -- Cuántas personas viven en la casa (§ pedido del usuario).
    ADD COLUMN IF NOT EXISTS household_size INTEGER
        CHECK (household_size IS NULL
               OR household_size BETWEEN 1 AND 60),
    -- Medio para recibir ayuda directa. Catálogo cerrado + referencia
    -- corta; ambos públicos a propósito (de nada sirven escondidos),
    -- pero la plataforma NO los verifica ni intermedia.
    ADD COLUMN IF NOT EXISTS donation_channel TEXT
        CHECK (donation_channel IS NULL OR donation_channel IN (
            'Nequi', 'Daviplata', 'Bancolombia', 'Movii', 'Otro'
        )),
    ADD COLUMN IF NOT EXISTS donation_reference TEXT
        CHECK (donation_reference IS NULL
               OR char_length(btrim(donation_reference)) BETWEEN 4 AND 60),
    -- Umbral de denuncias: deja de publicarse sin borrarse.
    ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMPTZ,
    -- Hasta cuándo el dueño ya leyó los comentarios de su casita.
    ADD COLUMN IF NOT EXISTS comments_seen_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS damaged_home_reports_public_code_idx
    ON disaster_service.damaged_home_reports (public_code)
    WHERE public_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS damaged_home_reports_account_idx
    ON disaster_service.damaged_home_reports (account_id, created_at DESC)
    WHERE account_id IS NOT NULL;

-- 2) Comunidad: cuarto objetivo de las mismas tablas.
ALTER TABLE disaster_service.aid_location_comments
    ADD COLUMN IF NOT EXISTS damaged_home_id UUID
        REFERENCES disaster_service.damaged_home_reports(id)
        ON DELETE CASCADE;

ALTER TABLE disaster_service.aid_location_comments
    DROP CONSTRAINT IF EXISTS aid_location_comment_single_target;
ALTER TABLE disaster_service.aid_location_comments
    ADD CONSTRAINT aid_location_comment_single_target
    CHECK (
        num_nonnulls(
            location_id, food_offer_id, help_request_id, damaged_home_id
        ) = 1
    );

CREATE INDEX IF NOT EXISTS aid_location_comments_damaged_home_idx
    ON disaster_service.aid_location_comments
    (damaged_home_id, created_at DESC)
    WHERE damaged_home_id IS NOT NULL;

ALTER TABLE disaster_service.aid_location_reports
    ADD COLUMN IF NOT EXISTS damaged_home_id UUID
        REFERENCES disaster_service.damaged_home_reports(id)
        ON DELETE CASCADE;

ALTER TABLE disaster_service.aid_location_reports
    DROP CONSTRAINT IF EXISTS aid_location_report_single_target;
ALTER TABLE disaster_service.aid_location_reports
    ADD CONSTRAINT aid_location_report_single_target
    CHECK (
        num_nonnulls(
            location_id, food_offer_id, help_request_id, damaged_home_id
        ) = 1
    );

CREATE UNIQUE INDEX IF NOT EXISTS damaged_home_report_live_denouncer_idx
    ON disaster_service.aid_location_reports
    (damaged_home_id, denouncer_key)
    WHERE archived_at IS NULL AND damaged_home_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS damaged_home_reports_active_idx
    ON disaster_service.aid_location_reports
    (damaged_home_id, moderation_status)
    WHERE archived_at IS NULL AND damaged_home_id IS NOT NULL;
