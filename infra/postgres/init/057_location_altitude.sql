-- CHG-220 — Altitud del GPS junto a la ubicación: en la presencia del
-- visitante y congelada en la instantánea de la alerta sísmica que ve el
-- contacto autorizado. Metros sobre el elipsoide WGS84; NULL sin fix real.
-- Idempotente, como todo /init.

ALTER TABLE disaster_service.visitor_presence
    ADD COLUMN IF NOT EXISTS altitude_meters REAL
        CHECK (altitude_meters IS NULL
               OR altitude_meters BETWEEN -500 AND 10000),
    ADD COLUMN IF NOT EXISTS altitude_accuracy_meters REAL
        CHECK (altitude_accuracy_meters IS NULL
               OR altitude_accuracy_meters >= 0);

ALTER TABLE disaster_service.seismic_user_alerts
    ADD COLUMN IF NOT EXISTS event_altitude REAL
        CHECK (event_altitude IS NULL
               OR event_altitude BETWEEN -500 AND 10000),
    ADD COLUMN IF NOT EXISTS event_altitude_accuracy REAL
        CHECK (event_altitude_accuracy IS NULL
               OR event_altitude_accuracy >= 0);
