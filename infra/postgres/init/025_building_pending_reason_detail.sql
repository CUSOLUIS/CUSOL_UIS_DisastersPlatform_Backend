-- CHG-093 — Detalle obligatorio del motivo "Otro" en el reporte de
-- edificio sin verificar. Texto libre de un canal ciudadano: se guarda
-- cifrado por la aplicación, como la descripción de la observación.

ALTER TABLE disaster_service.unverified_building_reports
    ADD COLUMN IF NOT EXISTS pending_reason_detail_protected BYTEA;
