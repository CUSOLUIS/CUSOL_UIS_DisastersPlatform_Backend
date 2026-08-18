-- CHG-148 — Voluntarios anónimos de una solicitud de ayuda.
--
-- Quien NO tiene cuenta puede ofrecerse como voluntario desde el
-- detalle de la solicitud en el mapa. A diferencia de la atención
-- autenticada (help_request_attenders, deduplicada por cuenta), aquí
-- no hay cuenta: cada envío es un voluntario. Sus datos personales
-- (nombre, teléfono, correo, foto) son PRIVADOS —solo los ve el
-- super_admin (DEC-148-01, patrón CHG-066)— y viajan cifrados con
-- Fernet, igual que la instantánea del reportante. El público solo ve
-- el contador de personas atendiendo, que suma atenciones + voluntarios.
--
-- La expiración de la solicitud NUNCA borra filas (DEC-125-02): el
-- ON DELETE CASCADE solo actúa si un super_admin elimina la solicitud.

CREATE TABLE IF NOT EXISTS disaster_service.help_request_volunteers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    help_request_id UUID NOT NULL
        REFERENCES disaster_service.help_requests(id) ON DELETE CASCADE,
    -- CHG-148 / CHG-101: reintento seguro en ventanas de despliegue —
    -- la misma Idempotency-Key no crea un segundo voluntario ni suma
    -- dos veces al contador.
    idempotency_key TEXT NOT NULL UNIQUE,
    -- PII cifrada (Fernet): nunca en claro en la base.
    name_encrypted BYTEA NOT NULL,
    phone_encrypted BYTEA,
    email_encrypted BYTEA,
    -- Foto opcional del voluntario, con claves opacas fuera de la base
    -- (mismo patrón que las fotos de reporte/solicitud).
    photo_storage_key TEXT,
    photo_derived_storage_key TEXT,
    photo_content_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS help_request_volunteers_by_request_idx
    ON disaster_service.help_request_volunteers
    (help_request_id, created_at DESC);
