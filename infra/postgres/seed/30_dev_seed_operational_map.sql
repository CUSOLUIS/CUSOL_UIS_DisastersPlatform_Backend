-- CHG-006 — Datos semilla del mapa operativo para desarrollo local.
-- Datos SINTÉTICOS y demostrativos: ubicaciones y casos ficticios; no
-- representan emergencias reales ni contienen datos personales.
-- Los puntos missing_person describen zonas públicas de búsqueda, sin
-- identidad y sin precisión exacta (DEC-007 pendiente).
-- Idempotente: UUID fijos + ON CONFLICT DO NOTHING; re-ejecutable sin duplicar.
-- Autocontenido: declara las fuentes y eventos que referencia
-- (mismos UUID que dev_seed.sql).

BEGIN;

INSERT INTO disaster_service.sources (id, name, source_type, url) VALUES
    (
        '11111111-1111-4111-8111-111111111101',
        'UNGRD — Unidad Nacional para la Gestión del Riesgo de Desastres',
        'official',
        'https://www.gestiondelriesgo.gov.co'
    ),
    (
        '11111111-1111-4111-8111-111111111103',
        'IDEAM — Instituto de Hidrología, Meteorología y Estudios Ambientales',
        'official',
        'https://www.ideam.gov.co'
    ),
    (
        '11111111-1111-4111-8111-111111111104',
        'Reporte ciudadano — plataforma CUSOL',
        'citizen',
        NULL
    ),
    (
        '11111111-1111-4111-8111-111111111105',
        'Clasificador CUSOL de medios locales',
        'ai_inference',
        NULL
    )
ON CONFLICT (id) DO NOTHING;

INSERT INTO disaster_service.disaster_events (
    id, source_id, title, description, disaster_type, severity,
    verification_status, location, occurred_at, updated_at
) VALUES
    (
        '22222222-2222-4222-8222-222222222201',
        '11111111-1111-4111-8111-111111111103',
        'Inundación en el norte de Bucaramanga',
        'Desbordamiento de quebrada tras lluvias intensas; afectación en vías y viviendas ribereñas.',
        'inundacion',
        'alta',
        'verified',
        ST_SetSRID(ST_MakePoint(-73.1198, 7.1254), 4326)::geography,
        '2026-08-09T22:30:00Z',
        '2026-08-10T14:00:00Z'
    ),
    (
        '22222222-2222-4222-8222-222222222202',
        '11111111-1111-4111-8111-111111111101',
        'Deslizamiento sobre la vía Bucaramanga—Barrancabermeja',
        'Remoción en masa con cierre parcial de la calzada; maquinaria en el punto.',
        'deslizamiento',
        'media',
        'verified',
        ST_SetSRID(ST_MakePoint(-73.3852, 7.0653), 4326)::geography,
        '2026-08-11T06:15:00Z',
        '2026-08-11T18:45:00Z'
    )
ON CONFLICT (id) DO NOTHING;

INSERT INTO disaster_service.operational_map_points (
    id, category, title, description, location_label, location,
    coordinate_precision, verification_status, related_disaster_id,
    source_id, data_classification, updated_at
) VALUES
    ('44444444-4444-4444-8444-444444444401', 'missing_person',
     'Zona de búsqueda — sector Café Madrid',
     'Zona pública de búsqueda asociada a reportes de personas desaparecidas tras la inundación. Sin identidad publicada.',
     'Café Madrid, Bucaramanga',
     ST_SetSRID(ST_MakePoint(-73.12, 7.13), 4326)::geography,
     'approximate', 'under_review', '22222222-2222-4222-8222-222222222201',
     '11111111-1111-4111-8111-111111111104', 'demonstrative',
     '2026-08-12T14:00:00Z'),
    ('44444444-4444-4444-8444-444444444402', 'missing_person',
     'Zona de búsqueda — corredor vial al Magdalena Medio',
     'Reportes ciudadanos de personas no localizadas cerca del deslizamiento; búsqueda coordinada en el municipio.',
     'Lebrija, Santander',
     ST_SetSRID(ST_MakePoint(-73.2181, 7.1132), 4326)::geography,
     'municipality', 'unverified', '22222222-2222-4222-8222-222222222202',
     '11111111-1111-4111-8111-111111111104', 'demonstrative',
     '2026-08-12T14:15:00Z'),
    ('44444444-4444-4444-8444-444444444403', 'collection_center',
     'Centro de acopio — Coliseo Bicentenario',
     'Recepción de agua potable, alimentos no perecederos y kits de aseo.',
     'Coliseo Bicentenario, Bucaramanga',
     ST_SetSRID(ST_MakePoint(-73.1289, 7.1069), 4326)::geography,
     'exact', 'verified', '22222222-2222-4222-8222-222222222201',
     '11111111-1111-4111-8111-111111111101', 'demonstrative',
     '2026-08-12T14:30:00Z'),
    ('44444444-4444-4444-8444-444444444404', 'collection_center',
     'Centro de acopio — Campus UIS',
     'Punto universitario de recolección de ropa y elementos de abrigo.',
     'Universidad Industrial de Santander, Bucaramanga',
     ST_SetSRID(ST_MakePoint(-73.1227, 7.1400), 4326)::geography,
     'exact', 'verified', NULL,
     '11111111-1111-4111-8111-111111111101', 'demonstrative',
     '2026-08-12T14:45:00Z'),
    ('44444444-4444-4444-8444-444444444405', 'rubble_reviewed',
     'Revisión registrada — ribera norte',
     'Cuadrilla oficial registró revisión de escombros en viviendas ribereñas. No implica que el lugar sea seguro.',
     'Barrio Colorados, Bucaramanga',
     ST_SetSRID(ST_MakePoint(-73.1150, 7.1310), 4326)::geography,
     'exact', 'verified', '22222222-2222-4222-8222-222222222201',
     '11111111-1111-4111-8111-111111111101', 'demonstrative',
     '2026-08-12T15:00:00Z'),
    ('44444444-4444-4444-8444-444444444406', 'rubble_reviewed',
     'Revisión registrada — talud km 17',
     'Registro de revisión del material removido sobre la calzada afectada.',
     'Km 17 vía Bucaramanga—Barrancabermeja',
     ST_SetSRID(ST_MakePoint(-73.3790, 7.0660), 4326)::geography,
     'approximate', 'verified', '22222222-2222-4222-8222-222222222202',
     '11111111-1111-4111-8111-111111111101', 'demonstrative',
     '2026-08-12T15:15:00Z'),
    ('44444444-4444-4444-8444-444444444407', 'rubble_pending',
     'Escombros por revisar — bodega colapsada',
     'Reporte ciudadano de estructura colapsada pendiente de revisión por cuadrillas.',
     'Girón, zona industrial',
     ST_SetSRID(ST_MakePoint(-73.1735, 7.0685), 4326)::geography,
     'approximate', 'unverified', NULL,
     '11111111-1111-4111-8111-111111111104', 'demonstrative',
     '2026-08-12T15:30:00Z'),
    ('44444444-4444-4444-8444-444444444408', 'rubble_pending',
     'Posibles escombros — margen de quebrada',
     'Clasificación automática a partir de imágenes de medios locales; requiere confirmación en terreno.',
     'Piedecuesta, margen de quebrada',
     ST_SetSRID(ST_MakePoint(-73.0510, 6.9895), 4326)::geography,
     'approximate', 'under_review', NULL,
     '11111111-1111-4111-8111-111111111105', 'demonstrative',
     '2026-08-12T15:45:00Z'),
    -- CHG-010: edificios sin inspección registrada. "Pendiente" no implica
    -- inseguridad ni colapso; sin residentes, propietarios ni direcciones
    -- privadas exactas.
    ('44444444-4444-4444-8444-444444444409', 'building_pending',
     'Edificio sin inspección registrada — sector comercial centro',
     'Edificación de uso comercial sin registro de inspección posterior a la inundación. Pendiente significa sin inspección registrada; no implica riesgo estructural.',
     'Centro, Bucaramanga',
     ST_SetSRID(ST_MakePoint(-73.1260, 7.1180), 4326)::geography,
     'approximate', 'unverified', '22222222-2222-4222-8222-222222222201',
     '11111111-1111-4111-8111-111111111104', 'demonstrative',
     '2026-08-12T16:00:00Z'),
    ('44444444-4444-4444-8444-444444444410', 'building_pending',
     'Edificio sin inspección registrada — corredor vial',
     'Estructura junto al corredor vial afectado por el deslizamiento, sin inspección registrada por cuadrillas. No implica declaración de seguridad ni de daño.',
     'Vía Bucaramanga—Barrancabermeja, Lebrija',
     ST_SetSRID(ST_MakePoint(-73.3600, 7.0700), 4326)::geography,
     'approximate', 'under_review', '22222222-2222-4222-8222-222222222202',
     '11111111-1111-4111-8111-111111111105', 'demonstrative',
     '2026-08-12T16:15:00Z')
ON CONFLICT (id) DO NOTHING;

COMMIT;
