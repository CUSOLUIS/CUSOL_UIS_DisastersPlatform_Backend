-- CHG-002 — Datos semilla para desarrollo local.
-- Datos SINTÉTICOS: se inspiran en eventos y entidades reales de Colombia,
-- pero no representan información oficial vigente.
-- Idempotente: UUID fijos + ON CONFLICT DO NOTHING; re-ejecutable sin duplicar.

BEGIN;

INSERT INTO disaster_service.sources (id, name, source_type, url) VALUES
    (
        '11111111-1111-4111-8111-111111111101',
        'UNGRD — Unidad Nacional para la Gestión del Riesgo de Desastres',
        'official',
        'https://www.gestiondelriesgo.gov.co'
    ),
    (
        '11111111-1111-4111-8111-111111111102',
        'SGC — Servicio Geológico Colombiano',
        'official',
        'https://www.sgc.gov.co'
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
    ),
    (
        '22222222-2222-4222-8222-222222222203',
        '11111111-1111-4111-8111-111111111102',
        'Sismo de magnitud 4.8 con epicentro en Los Santos, Santander',
        'Evento sísmico del nido de Bucaramanga, percibido en varios municipios; sin daños reportados.',
        'sismo',
        'baja',
        'verified',
        ST_SetSRID(ST_MakePoint(-73.1004, 6.7550), 4326)::geography,
        '2026-08-12T03:05:00Z',
        '2026-08-12T04:10:00Z'
    ),
    (
        '22222222-2222-4222-8222-222222222204',
        '11111111-1111-4111-8111-111111111104',
        'Creciente súbita en quebrada de Piedecuesta',
        'Vecinos reportan aumento repentino del caudal cerca de la zona urbana.',
        'avenida_torrencial',
        NULL,
        'unverified',
        ST_SetSRID(ST_MakePoint(-73.0498, 6.9870), 4326)::geography,
        '2026-08-12T11:20:00Z',
        '2026-08-12T11:35:00Z'
    ),
    (
        '22222222-2222-4222-8222-222222222205',
        '11111111-1111-4111-8111-111111111105',
        'Posible incendio forestal en cerros de Floridablanca',
        'Clasificación automática a partir de medios locales; columna de humo visible. Requiere confirmación.',
        'incendio_forestal',
        'media',
        'under_review',
        ST_SetSRID(ST_MakePoint(-73.0870, 7.0620), 4326)::geography,
        '2026-08-12T13:00:00Z',
        '2026-08-12T13:25:00Z'
    ),
    (
        '22222222-2222-4222-8222-222222222206',
        '11111111-1111-4111-8111-111111111104',
        'Reporte de inundación sin ubicación precisa',
        'Reporte ciudadano recibido sin coordenadas; pendiente de georreferenciación.',
        'inundacion',
        NULL,
        'under_review',
        NULL,
        NULL,
        '2026-08-12T09:00:00Z'
    ),
    (
        '22222222-2222-4222-8222-222222222207',
        '11111111-1111-4111-8111-111111111105',
        'Supuesto colapso de puente en Girón',
        'La verificación con fuentes oficiales descartó el evento; se conserva como rechazado para trazabilidad.',
        'inundacion',
        NULL,
        'rejected',
        ST_SetSRID(ST_MakePoint(-73.1702, 7.0700), 4326)::geography,
        '2026-08-10T16:00:00Z',
        '2026-08-11T08:30:00Z'
    )
ON CONFLICT (id) DO NOTHING;

COMMIT;
