-- CHG-014 — Volumen demostrativo: 2.000 personas SINTÉTICAS para probar el
-- panel de situación humana con datos suficientes.
-- Datos ficticios rotulados "demo"; no representan personas reales.
-- Idempotente: UUID deterministas por índice + ON CONFLICT DO NOTHING;
-- re-ejecutable sin duplicar.
-- Autocontenido: declara las fuentes que usa (mismos UUID que otras semillas).
-- Distribución determinista por módulo del índice:
--   estados  ~ 30% missing, 40% confirmed_alive,
--              15% reported_deceased, 15% confirmed_deceased
--   fechas   ~ ventana 2026-08-09T00:00Z en adelante, un registro cada 3 min

BEGIN;

INSERT INTO disaster_service.sources (id, name, source_type, url) VALUES
    (
        '11111111-1111-4111-8111-111111111101',
        'UNGRD — Unidad Nacional para la Gestión del Riesgo de Desastres',
        'official',
        'https://www.gestiondelriesgo.gov.co'
    ),
    (
        '11111111-1111-4111-8111-111111111104',
        'Reporte ciudadano — plataforma CUSOL',
        'citizen',
        NULL
    )
ON CONFLICT (id) DO NOTHING;

INSERT INTO disaster_service.people (
    id, source_id, display_name, status, location, related_event, created_at
)
SELECT
    ('55555555-5555-4555-8555-' || lpad(n::text, 12, '0'))::uuid,
    CASE WHEN n % 3 = 0
        THEN '11111111-1111-4111-8111-111111111101'::uuid
        ELSE '11111111-1111-4111-8111-111111111104'::uuid
    END,
    'Persona demo ' || lpad(n::text, 4, '0') || ' — '
        || (ARRAY['A','C','D','E','J','L','M','N','P','R','S','T'])[(n % 12) + 1]
        || '.'
        || (ARRAY['B','G','H','M','O','P','Q','R','S','T','V','Z'])[((n / 12) % 12) + 1]
        || '.',
    (ARRAY[
        'missing', 'confirmed_alive', 'missing', 'confirmed_alive',
        'reported_deceased', 'confirmed_alive', 'missing',
        'confirmed_deceased', 'confirmed_alive', 'reported_deceased',
        'missing', 'confirmed_deceased', 'confirmed_alive',
        'confirmed_alive', 'missing', 'confirmed_deceased',
        'reported_deceased', 'confirmed_alive', 'missing',
        'confirmed_alive'
    ])[(n % 20) + 1]::disaster_service.human_status,
    (ARRAY[
        'Café Madrid, Bucaramanga',
        'Girón, casco urbano',
        'Vereda La Esperanza, Lebrija',
        'Km 18 vía a Barrancabermeja',
        'Piedecuesta, zona rural',
        'Floridablanca, sector Bucarica',
        'Los Santos, Santander',
        'Barrio Colorados, Bucaramanga',
        'Piedecuesta, casco urbano',
        'Floridablanca, cerros orientales'
    ])[(n % 10) + 1],
    (ARRAY[
        'Inundación en el norte de Bucaramanga',
        'Deslizamiento sobre la vía Bucaramanga—Barrancabermeja',
        'Creciente súbita en quebrada de Piedecuesta',
        'Posible incendio forestal en cerros de Floridablanca',
        'Sismo de magnitud 4.8 con epicentro en Los Santos, Santander'
    ])[(n % 5) + 1],
    '2026-08-09T00:00:00Z'::timestamptz + (n * interval '3 minutes')
FROM generate_series(1, 2000) AS n
ON CONFLICT (id) DO NOTHING;

COMMIT;
