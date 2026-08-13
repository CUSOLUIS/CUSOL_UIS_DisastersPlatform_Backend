-- CHG-003 — Datos semilla de personas para desarrollo local.
-- Datos SINTÉTICOS: nombres y casos ficticios con fines demostrativos;
-- no representan personas ni información oficial.
-- Idempotente: UUID fijos + ON CONFLICT DO NOTHING; re-ejecutable sin duplicar.
-- Autocontenido: declara las fuentes que usa (mismos UUID que dev_seed.sql).

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
) VALUES
    ('33333333-3333-4333-8333-333333333301', '11111111-1111-4111-8111-111111111104',
     'Persona demo 01 — M.R.', 'missing',
     'Café Madrid, Bucaramanga', 'Inundación en el norte de Bucaramanga',
     '2026-08-10T15:10:00Z'),
    ('33333333-3333-4333-8333-333333333302', '11111111-1111-4111-8111-111111111104',
     'Persona demo 02 — J.T.', 'missing',
     'Vereda La Esperanza, Lebrija', 'Deslizamiento sobre la vía Bucaramanga—Barrancabermeja',
     '2026-08-11T09:40:00Z'),
    ('33333333-3333-4333-8333-333333333303', '11111111-1111-4111-8111-111111111101',
     'Persona demo 03 — A.C.', 'confirmed_alive',
     'Girón, casco urbano', 'Inundación en el norte de Bucaramanga',
     '2026-08-11T11:05:00Z'),
    ('33333333-3333-4333-8333-333333333304', '11111111-1111-4111-8111-111111111101',
     'Persona demo 04 — L.P.', 'reported_deceased',
     'Km 18 vía a Barrancabermeja', 'Deslizamiento sobre la vía Bucaramanga—Barrancabermeja',
     '2026-08-11T13:30:00Z'),
    ('33333333-3333-4333-8333-333333333305', '11111111-1111-4111-8111-111111111104',
     'Persona demo 05 — C.G.', 'missing',
     'Piedecuesta, zona rural', 'Creciente súbita en quebrada de Piedecuesta',
     '2026-08-12T07:20:00Z'),
    ('33333333-3333-4333-8333-333333333306', '11111111-1111-4111-8111-111111111101',
     'Persona demo 06 — D.M.', 'confirmed_alive',
     'Floridablanca, sector Bucarica', 'Posible incendio forestal en cerros de Floridablanca',
     '2026-08-12T08:05:00Z'),
    ('33333333-3333-4333-8333-333333333307', '11111111-1111-4111-8111-111111111101',
     'Persona demo 07 — E.S.', 'confirmed_deceased',
     'Café Madrid, Bucaramanga', 'Inundación en el norte de Bucaramanga',
     '2026-08-12T08:45:00Z'),
    ('33333333-3333-4333-8333-333333333308', '11111111-1111-4111-8111-111111111104',
     'Persona demo 08 — R.V.', 'missing',
     'Los Santos, Santander', 'Sismo de magnitud 4.8 con epicentro en Los Santos, Santander',
     '2026-08-12T09:15:00Z'),
    ('33333333-3333-4333-8333-333333333309', '11111111-1111-4111-8111-111111111104',
     'Persona demo 09 — S.O.', 'reported_deceased',
     'Piedecuesta, zona rural', 'Creciente súbita en quebrada de Piedecuesta',
     '2026-08-12T10:00:00Z'),
    ('33333333-3333-4333-8333-333333333310', '11111111-1111-4111-8111-111111111101',
     'Persona demo 10 — N.H.', 'confirmed_alive',
     'Girón, casco urbano', 'Inundación en el norte de Bucaramanga',
     '2026-08-12T10:30:00Z'),
    ('33333333-3333-4333-8333-333333333311', '11111111-1111-4111-8111-111111111104',
     'Persona demo 11 — P.Q.', 'missing',
     'Floridablanca, cerros orientales', 'Posible incendio forestal en cerros de Floridablanca',
     '2026-08-12T11:10:00Z'),
    ('33333333-3333-4333-8333-333333333312', '11111111-1111-4111-8111-111111111101',
     'Persona demo 12 — T.B.', 'confirmed_deceased',
     'Km 18 vía a Barrancabermeja', 'Deslizamiento sobre la vía Bucaramanga—Barrancabermeja',
     '2026-08-12T12:00:00Z')
ON CONFLICT (id) DO NOTHING;

COMMIT;
