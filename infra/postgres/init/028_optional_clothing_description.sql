-- CHG-113 — La vestimenta deja de ser obligatoria en el reporte de
-- persona desaparecida.
--
-- Quien denuncia una desaparición muchas veces no sabe con qué ropa
-- salió la persona: no la vio salir, se lo contaron, o la última vez
-- que la vio fue días antes. La columna era NOT NULL, así que el
-- formulario tenía que exigir el dato y la base terminaba llena de
-- "no sé" y "desconocido" — texto que además entra en el índice de
-- búsqueda de casos, degradándola para todos.
--
-- La proyección pública (missing_person_cases) ya admitía NULL en su
-- propia columna; el que faltaba era el expediente privado.
--
-- Idempotente: quitar una restricción que ya no está no es error.
-- No se tocan los valores ya escritos: son evidencia ciudadana
-- recibida y se limpian uno a uno por moderación, nunca a ciegas.

ALTER TABLE disaster_service.missing_person_reports
    ALTER COLUMN clothing_description DROP NOT NULL;
