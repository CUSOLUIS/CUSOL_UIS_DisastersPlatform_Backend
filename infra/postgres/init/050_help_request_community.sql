-- CHG-180 — «Necesitamos ayuda» gana lo mismo que un Centro de Acopio
-- Local: comentarios con estrellas, denuncias con su umbral y borrado
-- administrativo de comentarios.
--
-- Tercer objetivo sobre el MISMO modelo comunitario (CHG-165 para los
-- acopios, CHG-176 para las ofertas de comida): las dos tablas admiten
-- ahora tres clases de objetivo y la restricción sigue obligando a que
-- cada fila apunte exactamente a uno. Un solo camino de código para el
-- promedio, la deduplicación de denunciantes, los umbrales y el
-- borrado. Idempotente; no borra ni altera ninguna fila existente.

-- 1) Comentarios.
ALTER TABLE disaster_service.aid_location_comments
    ADD COLUMN IF NOT EXISTS help_request_id UUID
        REFERENCES disaster_service.help_requests(id) ON DELETE CASCADE;

-- CHG-204: solo mientras 051 no haya ampliado esto a cuatro objetivos.
-- Sin esta guardia, en cuanto existía un comentario sobre una casita
-- —que la versión de TRES no admite— este bloque borraba la restricción
-- y no lograba recrearla: el despliegue moría y la tabla se quedaba sin
-- ninguna. La presencia de `damaged_home_id` dice que 051 ya pasó.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'disaster_service'
          AND table_name = 'aid_location_comments'
          AND column_name = 'damaged_home_id'
    ) THEN
        ALTER TABLE disaster_service.aid_location_comments
            DROP CONSTRAINT IF EXISTS aid_location_comment_single_target;
        ALTER TABLE disaster_service.aid_location_comments
            ADD CONSTRAINT aid_location_comment_single_target
            CHECK (
                num_nonnulls(location_id, food_offer_id, help_request_id) = 1
            );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS aid_location_comments_help_request_idx
    ON disaster_service.aid_location_comments
    (help_request_id, created_at DESC)
    WHERE help_request_id IS NOT NULL;

-- 2) Denuncias, con la misma deduplicación por denunciante dentro del
--    ciclo vivo que ya tienen acopios y ofertas.
ALTER TABLE disaster_service.aid_location_reports
    ADD COLUMN IF NOT EXISTS help_request_id UUID
        REFERENCES disaster_service.help_requests(id) ON DELETE CASCADE;

-- CHG-204: misma guardia para la gemela de denuncias, que tenía el
-- mismo defecto esperando al primer reporte sobre una casita.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'disaster_service'
          AND table_name = 'aid_location_reports'
          AND column_name = 'damaged_home_id'
    ) THEN
        ALTER TABLE disaster_service.aid_location_reports
            DROP CONSTRAINT IF EXISTS aid_location_report_single_target;
        ALTER TABLE disaster_service.aid_location_reports
            ADD CONSTRAINT aid_location_report_single_target
            CHECK (
                num_nonnulls(location_id, food_offer_id, help_request_id) = 1
            );
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS help_request_report_live_denouncer_idx
    ON disaster_service.aid_location_reports
    (help_request_id, denouncer_key)
    WHERE archived_at IS NULL AND help_request_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS help_request_reports_active_idx
    ON disaster_service.aid_location_reports
    (help_request_id, moderation_status)
    WHERE archived_at IS NULL AND help_request_id IS NOT NULL;

-- 3) Qué significa «deshabilitar» una solicitud: como la oferta de
--    comida, la solicitud no tiene estado operativo —solo caduca—, así
--    que al alcanzar el umbral deja de publicarse y sale del mapa. El
--    histórico se conserva entero: nada se borra.
ALTER TABLE disaster_service.help_requests
    ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMPTZ;
