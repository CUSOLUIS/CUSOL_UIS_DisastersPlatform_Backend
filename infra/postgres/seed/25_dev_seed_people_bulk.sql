-- CHG-014 — Volumen demostrativo: 2.000 personas SINTÉTICAS para probar el
-- panel de situación humana con datos suficientes.
-- CHG-016 — v2: distribución por todo el país (todas las regiones) con
-- coordenadas explícitas deterministas por persona (people.latitude/longitude).
-- Datos ficticios rotulados "demo"; no representan personas reales.
-- Idempotente: UUID deterministas por índice + ON CONFLICT DO UPDATE con
-- valores deterministas; re-ejecutable sin duplicar y refresca ubicaciones.
-- Autocontenido: declara las fuentes que usa (mismos UUID que otras semillas).
-- Distribución determinista por módulo del índice:
--   estados   ~ 30% missing, 40% confirmed_alive,
--               15% reported_deceased, 15% confirmed_deceased
--   ciudades  ~ 40 posiciones ponderadas (Bogotá 6, Medellín 4, Cali 3, …)
--   fechas    ~ ventana 2026-08-09T00:00Z en adelante, un registro cada 3 min
--   posición  ~ ancla de ciudad + dispersión con módulos primos (1997/1499)

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

WITH cities AS (
    SELECT
        ARRAY[
            'Bogotá, D.C.', 'Bogotá, D.C.', 'Bogotá, D.C.',
            'Bogotá, D.C.', 'Bogotá, D.C.', 'Bogotá, D.C.',
            'Medellín, Antioquia', 'Medellín, Antioquia',
            'Medellín, Antioquia', 'Medellín, Antioquia',
            'Cali, Valle del Cauca', 'Cali, Valle del Cauca',
            'Cali, Valle del Cauca',
            'Barranquilla, Atlántico', 'Barranquilla, Atlántico',
            'Barranquilla, Atlántico',
            'Cartagena, Bolívar', 'Cartagena, Bolívar',
            'Bucaramanga, Santander', 'Bucaramanga, Santander',
            'Cúcuta, Norte de Santander',
            'Pereira, Risaralda',
            'Santa Marta, Magdalena',
            'Ibagué, Tolima',
            'Pasto, Nariño',
            'Manizales, Caldas',
            'Neiva, Huila',
            'Villavicencio, Meta',
            'Armenia, Quindío',
            'Montería, Córdoba',
            'Sincelejo, Sucre',
            'Popayán, Cauca',
            'Riohacha, La Guajira',
            'Quibdó, Chocó',
            'Tunja, Boyacá',
            'Florencia, Caquetá',
            'Leticia, Amazonas',
            'San José del Guaviare, Guaviare',
            'San Andrés, Archipiélago',
            'Yopal, Casanare'
        ] AS labels,
        ARRAY[
            4.7110, 4.7110, 4.7110, 4.7110, 4.7110, 4.7110,
            6.2442, 6.2442, 6.2442, 6.2442,
            3.4516, 3.4516, 3.4516,
            10.9685, 10.9685, 10.9685,
            10.3910, 10.3910,
            7.1193, 7.1193,
            7.8939,
            4.8087,
            11.2408,
            4.4389,
            1.2136,
            5.0703,
            2.9273,
            4.1420,
            4.5339,
            8.7479,
            9.3047,
            2.4448,
            11.5384,
            5.6947,
            5.5353,
            1.6144,
            -4.2153,
            2.5729,
            12.5847,
            5.3378
        ] AS lats,
        ARRAY[
            -74.0721, -74.0721, -74.0721, -74.0721, -74.0721, -74.0721,
            -75.5812, -75.5812, -75.5812, -75.5812,
            -76.5320, -76.5320, -76.5320,
            -74.7813, -74.7813, -74.7813,
            -75.4794, -75.4794,
            -73.1227, -73.1227,
            -72.5078,
            -75.6906,
            -74.1990,
            -75.2322,
            -77.2811,
            -75.5138,
            -75.2819,
            -73.6266,
            -75.6811,
            -75.8814,
            -75.3978,
            -76.6147,
            -72.9070,
            -76.6611,
            -73.3678,
            -75.6062,
            -69.9406,
            -72.6459,
            -81.7006,
            -72.3959
        ] AS lons,
        ARRAY[
            0.12, 0.12, 0.12, 0.12, 0.12, 0.12,
            0.10, 0.10, 0.10, 0.10,
            0.10, 0.10, 0.10,
            0.08, 0.08, 0.08,
            0.08, 0.08,
            0.06, 0.06,
            0.06,
            0.05,
            0.05,
            0.05,
            0.05,
            0.05,
            0.05,
            0.06,
            0.04,
            0.05,
            0.04,
            0.04,
            0.05,
            0.04,
            0.04,
            0.05,
            0.04,
            0.05,
            0.02,
            0.05
        ] AS spreads
)
INSERT INTO disaster_service.people (
    id, source_id, display_name, status, location,
    latitude, longitude, related_event, created_at
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
    c.labels[(n % 40) + 1],
    c.lats[(n % 40) + 1]
        + (((n * 7919) % 1997) / 1997.0 - 0.5) * c.spreads[(n % 40) + 1],
    c.lons[(n % 40) + 1]
        + (((n * 104729) % 1499) / 1499.0 - 0.5) * c.spreads[(n % 40) + 1],
    (ARRAY[
        'Inundación en el norte de Bucaramanga',
        'Deslizamiento sobre la vía Bucaramanga—Barrancabermeja',
        'Creciente súbita en quebrada de Piedecuesta',
        'Posible incendio forestal en cerros de Floridablanca',
        'Sismo de magnitud 4.8 con epicentro en Los Santos, Santander'
    ])[(n % 5) + 1],
    '2026-08-09T00:00:00Z'::timestamptz + (n * interval '3 minutes')
FROM generate_series(1, 2000) AS n, cities c
ON CONFLICT (id) DO UPDATE SET
    location = EXCLUDED.location,
    latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude;

COMMIT;
