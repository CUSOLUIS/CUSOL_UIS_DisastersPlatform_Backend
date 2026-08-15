-- CHG-054 — Vincular el reporte de persona desaparecida a la cuenta
-- del reportante cuando existe sesión al enviarlo. El UUID cruza el
-- límite del servicio solo como identidad OPACA (sin FK a
-- identity_service.accounts); habilita las notificaciones de avance y
-- la prioridad de revisión de los reportes con cuenta. Los demás tipos
-- (edificios, novedades, valoraciones, ofertas) ya guardaban cuenta.
-- Idempotente: ADD COLUMN IF NOT EXISTS.

ALTER TABLE disaster_service.missing_person_reports
    ADD COLUMN IF NOT EXISTS reporter_account_id UUID;
