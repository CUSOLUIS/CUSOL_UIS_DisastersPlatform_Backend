-- CHG-176 — Las ofertas de «Ofrecer comida» ganan lo mismo que los
-- centros de acopio: comentarios con estrellas, denuncias con sus
-- umbrales y borrado administrativo.
--
-- Se EXTIENDE el modelo comunitario existente en vez de clonarlo: las
-- dos tablas pasan a admitir dos clases de objetivo y una restricción
-- obliga a que cada fila apunte exactamente a uno. Así el promedio, la
-- deduplicación de denunciantes, los umbrales y el borrado siguen
-- siendo un solo camino de código.
--
-- El nombre histórico de las tablas se conserva a propósito:
-- renombrarlas obligaría a reescribir todas las consultas vigentes sin
-- ganar nada. Idempotente; no borra ni altera ninguna fila existente.

-- 1) Comentarios: objetivo opcional por lado, exactamente uno presente.
ALTER TABLE disaster_service.aid_location_comments
    ALTER COLUMN location_id DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS food_offer_id UUID
        REFERENCES disaster_service.food_offers(id) ON DELETE CASCADE;

-- CHG-204: además de «si no existe», hace falta «si nadie la amplió
-- todavía». 050 y 051 rehacen esta misma restricción con tres y cuatro
-- objetivos; si una pasada posterior la dejara caída (por datos que la
-- versión estrecha ya no admite), esta migración volvería a intentar la
-- de DOS y el despliegue entero moriría aquí. La presencia de
-- `help_request_id` dice que 050 ya pasó.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'aid_location_comment_single_target'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'disaster_service'
          AND table_name = 'aid_location_comments'
          AND column_name = 'help_request_id'
    ) THEN
        ALTER TABLE disaster_service.aid_location_comments
            ADD CONSTRAINT aid_location_comment_single_target
            CHECK (num_nonnulls(location_id, food_offer_id) = 1);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS aid_location_comments_food_offer_idx
    ON disaster_service.aid_location_comments (food_offer_id, created_at DESC)
    WHERE food_offer_id IS NOT NULL;

-- 2) Denuncias: mismo tratamiento, incluida la deduplicación por
--    denunciante dentro del ciclo vivo (espejo del índice de acopios).
ALTER TABLE disaster_service.aid_location_reports
    ALTER COLUMN location_id DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS food_offer_id UUID
        REFERENCES disaster_service.food_offers(id) ON DELETE CASCADE;

-- CHG-204: misma cautela que arriba, para la gemela de denuncias.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'aid_location_report_single_target'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'disaster_service'
          AND table_name = 'aid_location_reports'
          AND column_name = 'help_request_id'
    ) THEN
        ALTER TABLE disaster_service.aid_location_reports
            ADD CONSTRAINT aid_location_report_single_target
            CHECK (num_nonnulls(location_id, food_offer_id) = 1);
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS food_offer_report_live_denouncer_idx
    ON disaster_service.aid_location_reports (food_offer_id, denouncer_key)
    WHERE archived_at IS NULL AND food_offer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS food_offer_reports_active_idx
    ON disaster_service.aid_location_reports
    (food_offer_id, moderation_status)
    WHERE archived_at IS NULL AND food_offer_id IS NOT NULL;

-- 3) Qué significa «deshabilitar» una oferta: una oferta no tiene
--    estado operativo, solo caduca. Al llegar al umbral de denuncias
--    deja de publicarse, igual que un acopio sale del mapa. El histórico
--    queda en NULL: ninguna oferta anterior se deshabilita sola.
ALTER TABLE disaster_service.food_offers
    ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMPTZ;
