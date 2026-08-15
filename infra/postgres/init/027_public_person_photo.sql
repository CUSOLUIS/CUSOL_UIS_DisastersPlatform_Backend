-- CHG-105 — Fotografía pública del caso de persona desaparecida.
--
-- Hasta ahora `public_photo_url` existía pero nada la escribía: las
-- fotos se guardaban en el expediente privado y no había forma de
-- publicarlas, así que la ficha pública siempre mostraba el marcador
-- genérico. En una búsqueda de personas la fotografía es el principal
-- medio de identificación, así que el reportante autoriza compartirla
-- al enviar y la foto se publica con el caso.
--
-- Se guarda la clave del objeto DERIVADO (sin metadatos EXIF), nunca
-- la del original en cuarentena. Poner esta columna en NULL retira la
-- foto de la vista pública sin tocar el expediente, que es la vía de
-- retirada rápida para el equipo de revisión.

ALTER TABLE disaster_service.missing_person_cases
    ADD COLUMN IF NOT EXISTS public_photo_object_key TEXT,
    -- Quién y cuándo la retiró, para poder auditar la decisión.
    ADD COLUMN IF NOT EXISTS public_photo_withdrawn_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS public_photo_withdrawn_by TEXT;

-- Una foto retirada no puede seguir teniendo objeto publicado.
ALTER TABLE disaster_service.missing_person_cases
    DROP CONSTRAINT IF EXISTS public_photo_withdrawal_consistent;

ALTER TABLE disaster_service.missing_person_cases
    ADD CONSTRAINT public_photo_withdrawal_consistent CHECK (
        public_photo_withdrawn_at IS NULL
        OR public_photo_object_key IS NULL
    );
