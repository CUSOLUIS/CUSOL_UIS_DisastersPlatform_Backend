-- CHG-034 — Datos semilla del directorio humanitario para desarrollo local.
-- Datos SINTÉTICOS y demostrativos: lugares y valoraciones ficticias; no
-- representan operaciones reales ni contienen datos personales.
-- Idempotente: UUID fijos + ON CONFLICT DO NOTHING; re-ejecutable.
-- Los centros reutilizan el UUID de su punto del mapa operativo para
-- mantener la trazabilidad; los puntos de recolección son nuevos y NO se
-- publican en el mapa (cambio coordinado posterior, ver handoff CHG-034).

BEGIN;

-- Fuente de la tarjeta pública de los casos sembrados por CHG-007.
UPDATE disaster_service.missing_person_cases
SET source_id = '11111111-1111-4111-8111-111111111104'
WHERE source_id IS NULL;

INSERT INTO disaster_service.aid_locations (
    id, kind, name, location_label, municipality, department,
    verification_status, availability_status, open_now,
    accepted_supplies, average_rating, ratings_count, source_id,
    publication_status, data_classification, updated_at
) VALUES
    ('44444444-4444-4444-8444-444444444403', 'collection_center',
     'Centro de acopio — Coliseo Bicentenario',
     'Coliseo Bicentenario, Bucaramanga', 'Bucaramanga', 'Santander',
     'verified', 'active', TRUE,
     ARRAY['water', 'food', 'medicine'], 4.50, 2,
     '11111111-1111-4111-8111-111111111101',
     'published', 'demonstrative', '2026-08-13T09:00:00Z'),
    ('44444444-4444-4444-8444-444444444404', 'collection_center',
     'Centro de acopio — Campus UIS',
     'Universidad Industrial de Santander, Bucaramanga',
     'Bucaramanga', 'Santander',
     'verified', 'active', NULL,
     ARRAY['clothing', 'shelter'], NULL, 0,
     '11111111-1111-4111-8111-111111111101',
     'published', 'demonstrative', '2026-08-13T09:15:00Z'),
    ('77777777-7777-4777-8777-777777777701', 'collection_point',
     'Punto de recolección — Parque San Pío',
     'Parque San Pío, Bucaramanga', 'Bucaramanga', 'Santander',
     'under_review', 'active', TRUE,
     ARRAY['water', 'food'], NULL, 0,
     '11111111-1111-4111-8111-111111111104',
     'published', 'demonstrative', '2026-08-13T09:30:00Z'),
    ('77777777-7777-4777-8777-777777777702', 'collection_point',
     'Punto de recolección — Alcaldía de Lebrija',
     'Alcaldía Municipal, Lebrija', 'Lebrija', 'Santander',
     'verified', 'inactive', FALSE,
     ARRAY['tools', 'other'], NULL, 0,
     '11111111-1111-4111-8111-111111111101',
     'published', 'demonstrative', '2026-08-13T09:45:00Z'),
    -- No publicado: verifica que la búsqueda no lo indexa.
    ('77777777-7777-4777-8777-777777777703', 'collection_point',
     'Punto en revisión (no público)',
     'Pendiente de moderación, Girón', 'Girón', 'Santander',
     'under_review', 'unknown', NULL,
     ARRAY[]::TEXT[], NULL, 0,
     '11111111-1111-4111-8111-111111111104',
     'under_review', 'demonstrative', '2026-08-13T10:00:00Z')
ON CONFLICT (id) DO NOTHING;

-- Valoraciones demostrativas: dos aceptadas sostienen el agregado 4.50/2
-- del Coliseo; una under_review demuestra que no afecta el promedio.
-- El texto cifrado es un marcador sintético (la API nunca lo descifra
-- para publicarlo).
INSERT INTO disaster_service.aid_location_ratings (
    id, location_id, idempotency_key, rating,
    evidence_description_encrypted, actor_kind, account_id,
    moderation_status, received_at, decided_at, decided_by_role
) VALUES
    ('88888888-8888-4888-8888-888888888801',
     '44444444-4444-4444-8444-444444444403',
     'seed-rating-coliseo-0001', 5,
     convert_to('seed-demo', 'UTF8'), 'anonymous', NULL,
     'accepted', '2026-08-13T08:00:00Z', '2026-08-13T08:30:00Z',
     'moderator'),
    ('88888888-8888-4888-8888-888888888802',
     '44444444-4444-4444-8444-444444444403',
     'seed-rating-coliseo-0002', 4,
     convert_to('seed-demo', 'UTF8'), 'anonymous', NULL,
     'accepted', '2026-08-13T08:10:00Z', '2026-08-13T08:35:00Z',
     'moderator'),
    ('88888888-8888-4888-8888-888888888803',
     '44444444-4444-4444-8444-444444444404',
     'seed-rating-uis-0001', 2,
     convert_to('seed-demo', 'UTF8'), 'anonymous', NULL,
     'under_review', '2026-08-13T08:20:00Z', NULL, NULL)
ON CONFLICT (id) DO NOTHING;

COMMIT;
