-- CHG-007 — Datos semilla de casos públicos de personas desaparecidas.
-- Datos SINTÉTICOS y demostrativos: nombres ficticios; no corresponden a
-- personas reales. Solo los casos `published` aparecen en la búsqueda; se
-- incluye uno `under_review` para verificar que no se indexa.
-- Idempotente: UUID fijos + ON CONFLICT DO NOTHING.

BEGIN;

INSERT INTO disaster_service.missing_person_cases (
    id, public_case_code, display_name, aliases, approximate_age,
    last_seen_at, last_seen_area, municipality, department,
    clothing_description, physical_description, distinctive_marks,
    public_photo_url, map_point_id, publication_status,
    data_classification, updated_at
) VALUES
    ('55555555-5555-4555-8555-555555555501', 'MP-2026-DEMO01',
     'Camila Rueda (caso demo)', ARRAY['Cami'], 34,
     '2026-08-10T18:30:00Z', 'Sector Café Madrid',
     'Bucaramanga', 'Santander',
     'Chaqueta azul y jean oscuro',
     'Estatura media, contextura delgada, cabello castaño largo',
     'Cicatriz pequeña en la ceja izquierda',
     NULL, '44444444-4444-4444-8444-444444444401', 'published',
     'demonstrative', '2026-08-12T10:00:00Z'),
    ('55555555-5555-4555-8555-555555555502', 'MP-2026-DEMO02',
     'Andrés Peña (caso demo)', ARRAY['El profe'], 58,
     '2026-08-11T07:00:00Z', 'Corredor vial al Magdalena Medio',
     'Lebrija', 'Santander',
     'Camisa blanca y sombrero',
     'Estatura alta, contextura gruesa, cabello canoso corto',
     'Usa gafas de fórmula permanentes',
     NULL, '44444444-4444-4444-8444-444444444402', 'published',
     'demonstrative', '2026-08-12T11:30:00Z'),
    ('55555555-5555-4555-8555-555555555503', 'MP-2026-DEMO03',
     'Valentina Gómez (caso demo)', ARRAY[]::TEXT[], 17,
     '2026-08-11T16:45:00Z', 'Zona rural de Piedecuesta',
     'Piedecuesta', 'Santander',
     'Sudadera gris con capota',
     'Estatura baja, cabello negro recogido',
     NULL,
     NULL, NULL, 'published',
     'demonstrative', '2026-08-12T12:15:00Z'),
    ('55555555-5555-4555-8555-555555555504', 'MP-2026-DEMO04',
     'Caso en revisión (no público)', ARRAY[]::TEXT[], NULL,
     '2026-08-12T08:00:00Z', 'Pendiente de moderación',
     'Girón', 'Santander',
     NULL, NULL, NULL,
     NULL, NULL, 'under_review',
     'demonstrative', '2026-08-12T13:00:00Z')
ON CONFLICT (id) DO NOTHING;

COMMIT;
