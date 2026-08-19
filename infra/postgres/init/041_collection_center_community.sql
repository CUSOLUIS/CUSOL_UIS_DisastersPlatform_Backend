-- CHG-165 — Centros de Acopio Local: comentarios, denuncias con motivo
-- y umbral de deshabilitación, y verificación administrativa.
--
-- Evoluciona lo que CHG-153 dejó: la denuncia gana un motivo por
-- categorías (la descripción sigue cifrada); el ciclo de denuncias se
-- vuelve reiniciable (la UNIQUE por denunciante pasa a índice parcial
-- sobre denuncias vivas, así reactivar archiva el ciclo sin borrar el
-- histórico y permite denunciar de nuevo en el ciclo siguiente); el
-- centro conoce cuándo quedó deshabilitado por denuncias y quién/cuándo
-- decidió su verificación. Crea la tabla de comentarios públicos.
-- Idempotente; preserva todos los datos existentes.

-- 1. Denuncias: motivo por categorías (las filas previas quedan NULL:
--    llegaron antes de que existiera el selector, no se inventa dato).
ALTER TABLE disaster_service.aid_location_reports
    ADD COLUMN IF NOT EXISTS reason_category TEXT;

-- 2. Ciclo reiniciable: la restricción de "una denuncia por
--    denunciante" aplica solo a denuncias vivas (archived_at IS NULL).
--    Reactivar un centro archiva sus denuncias → el mismo denunciante
--    puede volver a denunciar en el ciclo nuevo; el histórico queda.
ALTER TABLE disaster_service.aid_location_reports
    DROP CONSTRAINT IF EXISTS aid_location_report_one_per_denouncer;
CREATE UNIQUE INDEX IF NOT EXISTS aid_location_report_live_denouncer_idx
    ON disaster_service.aid_location_reports (location_id, denouncer_key)
    WHERE archived_at IS NULL;

-- 3. El centro: cuándo quedó deshabilitado por denuncias (NULL si no
--    lo está) y la decisión de verificación (quién y cuándo). El estado
--    de verificación reutiliza la columna/enum existentes
--    (unverified = pendiente, verified, rejected); estado operativo y
--    verificación son independientes (§25 del contrato).
ALTER TABLE disaster_service.aid_locations
    ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS verified_by UUID;

-- 4. Comentarios públicos del centro. account_id NULL = anónimo (no se
--    guarda "anonymous" como texto); el nombre visible se congela al
--    publicar (no expone correo ni datos privados). Misma política de
--    borrado que las denuncias: CASCADE con el centro.
CREATE TABLE IF NOT EXISTS disaster_service.aid_location_comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    location_id UUID NOT NULL
        REFERENCES disaster_service.aid_locations(id) ON DELETE CASCADE,
    account_id UUID,
    author_display_name TEXT,
    actor_kind disaster_service.contribution_actor_kind NOT NULL
        DEFAULT 'anonymous',
    content TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT aid_location_comment_not_blank CHECK (btrim(content) <> '')
);

-- Listado público: los más recientes primero (§6 del contrato).
CREATE INDEX IF NOT EXISTS aid_location_comments_recent_idx
    ON disaster_service.aid_location_comments (location_id, created_at DESC);
