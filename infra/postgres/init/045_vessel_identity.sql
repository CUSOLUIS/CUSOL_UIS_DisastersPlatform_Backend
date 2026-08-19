-- CHG-173 — «La Lanchera» con campos propios de embarcación.
-- Idempotente. Cierra la deuda nº 4 de CHG-171: la lancha ya no
-- reutiliza las columnas de placas del tractocamión con etiqueta de
-- matrícula, sino que tiene su propia identidad fluvial.
--
-- Los transportes existentes conservan lo suyo y quedan con las
-- columnas nuevas en NULL (misma regla del §62 de CHG-171: la
-- obligatoriedad, condicionada al tipo, la exige la validación de
-- negocio, no el esquema). No se borra ni se altera ninguna columna.

ALTER TABLE disaster_service.humanitarian_transports
    -- Matrícula del registro ante la autoridad fluvial: normalizada
    -- en mayúsculas y sin espacios ni guiones, 4-15 alfanuméricos
    -- (los registros fluviales no siguen el formato de placa
    -- terrestre, por eso no comparte columna con tractor_plate).
    ADD COLUMN IF NOT EXISTS vessel_registration TEXT,
    -- En los ríos la lancha se reconoce por el nombre del casco.
    ADD COLUMN IF NOT EXISTS vessel_name TEXT,
    -- Catálogo cerrado: Lancha, Chalupa, Bongo, Planchón, Ferri.
    ADD COLUMN IF NOT EXISTS vessel_type TEXT;
