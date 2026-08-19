-- CHG-162 (F2) — «Mi casita partida»: fotos del daño y moderación.
--
-- El informe nace publicado (CHG-075) y ahora, además, entra en la
-- bandeja administrativa del tema Infraestructura (CHG-159): rechazar o
-- archivar lo retira del mapa (`visible = FALSE`) sin borrar nada, y
-- aceptar o restaurar lo devuelve. Las fotografías siguen el mismo
-- patrón de evidencia que el reporte de edificio sin verificar: claves
-- opacas fuera de la base, original en cuarentena y derivado sin EXIF.
-- Idempotente.

ALTER TABLE disaster_service.damaged_home_reports
    ADD COLUMN IF NOT EXISTS moderation_status TEXT NOT NULL
        DEFAULT 'under_review',
    ADD COLUMN IF NOT EXISTS needs_information BOOLEAN NOT NULL
        DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS archived_by UUID,
    ADD COLUMN IF NOT EXISTS moderated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS moderated_by TEXT;

DO $$
BEGIN
    ALTER TABLE disaster_service.damaged_home_reports
        ADD CONSTRAINT damaged_home_moderation_status_valid
        CHECK (
            moderation_status IN ('under_review', 'accepted', 'rejected')
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

CREATE INDEX IF NOT EXISTS damaged_home_reports_moderation_idx
    ON disaster_service.damaged_home_reports
    (moderation_status, created_at DESC);

CREATE TABLE IF NOT EXISTS disaster_service.damaged_home_report_photos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID NOT NULL
        REFERENCES disaster_service.damaged_home_reports(id)
        ON DELETE CASCADE,
    position INTEGER NOT NULL,
    object_key TEXT NOT NULL UNIQUE,
    derived_object_key TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    sha256 TEXT NOT NULL,
    malware_scan TEXT NOT NULL,
    exif_removed BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT damaged_home_photo_position_valid
        CHECK (position BETWEEN 1 AND 5)
);

CREATE INDEX IF NOT EXISTS damaged_home_report_photos_report_idx
    ON disaster_service.damaged_home_report_photos (report_id, position);
