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

-- Backfill de los casos creados antes de este cambio. Sus fotografías
-- estaban guardadas y con la misma autorización del reportante, pero
-- sin camino hacia la vista pública, así que la ficha mostraba el
-- marcador genérico. Se aplica el mismo criterio que el código: se
-- prefiere la marcada como rostro reciente y, en su defecto, la
-- primera; siempre el objeto derivado, nunca el original en cuarentena.
--
-- Idempotente y respetuoso de las decisiones ya tomadas: solo toca
-- casos publicados que aún no tienen fotografía publicada y a los que
-- nadie se la ha retirado.
UPDATE disaster_service.missing_person_cases mc
SET public_photo_object_key = elegida.derived_storage_key,
    updated_at = NOW()
FROM (
    SELECT DISTINCT ON (r.public_case_code)
           r.public_case_code,
           p.derived_storage_key
    FROM disaster_service.missing_person_reports r
    JOIN disaster_service.missing_person_report_photos p
        ON p.report_id = r.id
    WHERE p.malware_scan = 'clean'
    ORDER BY r.public_case_code,
             (p.category = 'recent_face') DESC NULLS LAST,
             p.position ASC
) AS elegida
WHERE mc.public_case_code = elegida.public_case_code
  AND mc.publication_status = 'published'
  AND mc.public_photo_object_key IS NULL
  AND mc.public_photo_withdrawn_at IS NULL;
