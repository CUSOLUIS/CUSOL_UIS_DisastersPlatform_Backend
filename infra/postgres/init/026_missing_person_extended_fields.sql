-- CHG-094 — Ampliación del reporte de persona desaparecida.
--
-- Criterio de clasificación (el mismo de 004): en claro lo que sirve
-- para reconocer y buscar; cifrado lo que es dato de salud o
-- identifica a un tercero.

ALTER TABLE disaster_service.missing_person_reports
    -- Identificación física detallada (en claro, como distinctive_marks).
    ADD COLUMN IF NOT EXISTS tattoo_description TEXT,
    ADD COLUMN IF NOT EXISTS scars_description TEXT,
    ADD COLUMN IF NOT EXISTS prosthetics_description TEXT,
    ADD COLUMN IF NOT EXISTS piercings_and_moles TEXT,
    -- Alertas médicas: datos de salud, cifrados por la aplicación.
    ADD COLUMN IF NOT EXISTS mental_health_condition_encrypted BYTEA,
    ADD COLUMN IF NOT EXISTS vital_medication_encrypted BYTEA,
    ADD COLUMN IF NOT EXISTS severe_allergies_encrypted BYTEA,
    -- Contexto del desplazamiento.
    ADD COLUMN IF NOT EXISTS belongings_description TEXT,
    ADD COLUMN IF NOT EXISTS transport_mode TEXT,
    -- La placa identifica a un tercero: cifrada.
    ADD COLUMN IF NOT EXISTS vehicle_details_encrypted BYTEA,
    ADD COLUMN IF NOT EXISTS companions_description TEXT,
    -- Estado institucional de la denuncia.
    ADD COLUMN IF NOT EXISTS official_authority_name TEXT,
    -- Consentimiento del reportante para compartir su contacto. El
    -- dato sigue cifrado; esto solo registra la autorización.
    ADD COLUMN IF NOT EXISTS reporter_phone_public BOOLEAN
        NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS reporter_email_public BOOLEAN
        NOT NULL DEFAULT FALSE;

DO $$
BEGIN
    CREATE TYPE disaster_service.person_transport_mode AS ENUM (
        'on_foot',
        'bicycle',
        'public_transport',
        'private_vehicle',
        'other'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

ALTER TABLE disaster_service.missing_person_reports
    DROP CONSTRAINT IF EXISTS person_transport_mode_known;

ALTER TABLE disaster_service.missing_person_reports
    ADD CONSTRAINT person_transport_mode_known CHECK (
        transport_mode IS NULL
        OR transport_mode::disaster_service.person_transport_mode
            IS NOT NULL
    );

DO $$
BEGIN
    CREATE TYPE disaster_service.person_photo_category AS ENUM (
        'recent_face',
        'full_body',
        'distinctive_mark',
        'other'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- Categoría por fotografía: el canal multipart no admite metadatos por
-- parte, así que llega en un arreglo paralelo alineado por posición.
ALTER TABLE disaster_service.missing_person_report_photos
    ADD COLUMN IF NOT EXISTS category
        disaster_service.person_photo_category;
