-- CHG-122 — El desenlace fallecido es definitivo. La prioridad 2 del
-- recompute (CHG-077) tomaba la novedad de salud más reciente, así
-- que una "encontrada" posterior pisaba un fallecimiento ya declarado
-- (caso MP-2026-01C9EB01 de las capturas del reporte). El servicio ya
-- aplica la regla pegajosa en cada mutación; este recálculo
-- retroactivo corrige los casos que quedaron sobrescritos antes del
-- cambio. Idempotente: la expresión es determinista sobre las
-- novedades vigentes (espejo de _PERSON_PUBLIC_STATUS_EXPRESSION en
-- app/repository.py — si aquella cambia, este archivo debe cambiar).

UPDATE disaster_service.missing_person_cases mc
SET public_status = COALESCE(
        (
            SELECT r.claimed_outcome
            FROM disaster_service.person_status_reports r
            WHERE r.person_id = mc.id
              AND r.moderation_status = 'accepted'
              AND r.archived_at IS NULL
            ORDER BY r.decided_at DESC NULLS LAST,
                     r.received_at DESC
            LIMIT 1
        ),
        (
            SELECT r.claimed_outcome
            FROM disaster_service.person_status_reports r
            WHERE r.person_id = mc.id
              AND r.moderation_status NOT IN ('rejected', 'withdrawn')
              AND r.archived_at IS NULL
              AND r.reporter_health_sector
            ORDER BY (r.claimed_outcome = 'deceased') DESC,
                     r.received_at DESC
            LIMIT 1
        ),
        (
            SELECT r.claimed_outcome
            FROM disaster_service.person_status_reports r
            WHERE r.person_id = mc.id
              AND r.moderation_status NOT IN ('rejected', 'withdrawn')
              AND r.archived_at IS NULL
              AND r.account_id IS NOT NULL
            GROUP BY r.claimed_outcome
            HAVING COUNT(DISTINCT r.account_id) >= 5
            ORDER BY (r.claimed_outcome = 'deceased') DESC,
                     COUNT(DISTINCT r.account_id) DESC,
                     MAX(r.received_at) DESC
            LIMIT 1
        ),
        'missing'
    ),
    updated_at = NOW()
WHERE mc.publication_status = 'published'
  AND mc.public_status <> COALESCE(
        (
            SELECT r.claimed_outcome
            FROM disaster_service.person_status_reports r
            WHERE r.person_id = mc.id
              AND r.moderation_status = 'accepted'
              AND r.archived_at IS NULL
            ORDER BY r.decided_at DESC NULLS LAST,
                     r.received_at DESC
            LIMIT 1
        ),
        (
            SELECT r.claimed_outcome
            FROM disaster_service.person_status_reports r
            WHERE r.person_id = mc.id
              AND r.moderation_status NOT IN ('rejected', 'withdrawn')
              AND r.archived_at IS NULL
              AND r.reporter_health_sector
            ORDER BY (r.claimed_outcome = 'deceased') DESC,
                     r.received_at DESC
            LIMIT 1
        ),
        (
            SELECT r.claimed_outcome
            FROM disaster_service.person_status_reports r
            WHERE r.person_id = mc.id
              AND r.moderation_status NOT IN ('rejected', 'withdrawn')
              AND r.archived_at IS NULL
              AND r.account_id IS NOT NULL
            GROUP BY r.claimed_outcome
            HAVING COUNT(DISTINCT r.account_id) >= 5
            ORDER BY (r.claimed_outcome = 'deceased') DESC,
                     COUNT(DISTINCT r.account_id) DESC,
                     MAX(r.received_at) DESC
            LIMIT 1
        ),
        'missing'
    );

-- CHG-084: la proyección humana acompaña al caso corregido.
UPDATE disaster_service.people p
SET status = (CASE mc.public_status::text
        WHEN 'found' THEN 'confirmed_alive'
        WHEN 'deceased' THEN 'reported_deceased'
        ELSE 'missing'
    END)::disaster_service.human_status
FROM disaster_service.missing_person_cases mc
WHERE p.missing_person_case_id = mc.id
  AND p.status IS DISTINCT FROM (CASE mc.public_status::text
        WHEN 'found' THEN 'confirmed_alive'
        WHEN 'deceased' THEN 'reported_deceased'
        ELSE 'missing'
    END)::disaster_service.human_status;
