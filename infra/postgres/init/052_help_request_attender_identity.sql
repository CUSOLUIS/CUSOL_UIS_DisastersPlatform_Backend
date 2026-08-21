-- CHG-193 — Quien pidió ayuda puede ver quién la atiende, con el
-- consentimiento de cada persona y nunca hacia atrás.
--
-- Hasta CHG-148 los datos del voluntario eran privados y solo los veía
-- el super_admin (DEC-148-01), porque eso es lo que le prometía el
-- formulario: «Tus datos son privados: solo los ve el equipo que
-- coordina». El usuario decide ahora que la dueña de la solicitud vea
-- NOMBRE, TELÉFONO y FOTO de quien va en camino —coordinar un rescate
-- sin saber quién viene es difícil—, pero esa promesa no se puede
-- cambiar retroactivamente: quien se ofreció antes no queda expuesto.
--
-- Por eso el consentimiento se guarda fila a fila y por defecto es
-- FALSO: las filas que ya existen —las que se registraron bajo la
-- promesa vieja— se quedan sin compartir nada. El correo sigue siendo
-- privado del super_admin en ambos casos.

-- Voluntarios sin cuenta: el formulario nuevo advierte qué se comparte.
ALTER TABLE disaster_service.help_request_volunteers
    ADD COLUMN IF NOT EXISTS shares_contact BOOLEAN NOT NULL DEFAULT false;

-- Atención con cuenta: hasta ahora solo se guardaba el identificador.
-- Quien acepta compartir deja su nombre y su teléfono cifrados (Fernet,
-- igual que el voluntario): es la instantánea de lo que consintió en
-- ese momento, y no depende de que después cambie su perfil.
ALTER TABLE disaster_service.help_request_attenders
    ADD COLUMN IF NOT EXISTS shares_identity BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE disaster_service.help_request_attenders
    ADD COLUMN IF NOT EXISTS name_encrypted BYTEA;
ALTER TABLE disaster_service.help_request_attenders
    ADD COLUMN IF NOT EXISTS phone_encrypted BYTEA;

-- Sin consentimiento no puede haber instantánea guardada.
ALTER TABLE disaster_service.help_request_attenders
    DROP CONSTRAINT IF EXISTS help_request_attender_identity_needs_consent;
ALTER TABLE disaster_service.help_request_attenders
    ADD CONSTRAINT help_request_attender_identity_needs_consent
    CHECK (shares_identity OR (name_encrypted IS NULL AND phone_encrypted IS NULL));

-- La dueña lista a quienes la atienden por orden de llegada.
CREATE INDEX IF NOT EXISTS help_request_attenders_by_request_idx
    ON disaster_service.help_request_attenders (help_request_id, created_at DESC);
