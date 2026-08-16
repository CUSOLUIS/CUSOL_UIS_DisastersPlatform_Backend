-- CHG-124 — Muerte confirmada cuando la declara el sector salud. El
-- filtro "Muertos confirmados" existía desde 002/CHG-084 pero la
-- proyección mapeaba todo fallecimiento a reported_deceased, así que
-- siempre estaba vacío. El servicio ya proyecta con la regla nueva en
-- cada mutación; este recálculo retroactivo corrige las filas
-- existentes (y lo que el backfill histórico de 023 proyecte con la
-- regla vieja, porque este archivo corre después). Idempotente:
-- espejo de _PEOPLE_STATUS_CASE_EXPRESSION en app/repository.py — si
-- aquella cambia, este archivo debe cambiar.

UPDATE disaster_service.people p
SET status = (CASE
        WHEN mc.public_status::text = 'found' THEN 'confirmed_alive'
        WHEN mc.public_status::text = 'deceased' THEN
            CASE WHEN EXISTS (
                SELECT 1
                FROM disaster_service.person_status_reports hr
                WHERE hr.person_id = mc.id
                  AND hr.reporter_health_sector
                  AND hr.claimed_outcome = 'deceased'
                  AND hr.moderation_status NOT IN ('rejected', 'withdrawn')
                  AND hr.archived_at IS NULL
            ) THEN 'confirmed_deceased'
            ELSE 'reported_deceased' END
        ELSE 'missing'
    END)::disaster_service.human_status
FROM disaster_service.missing_person_cases mc
WHERE p.missing_person_case_id = mc.id
  AND p.status IS DISTINCT FROM (CASE
        WHEN mc.public_status::text = 'found' THEN 'confirmed_alive'
        WHEN mc.public_status::text = 'deceased' THEN
            CASE WHEN EXISTS (
                SELECT 1
                FROM disaster_service.person_status_reports hr
                WHERE hr.person_id = mc.id
                  AND hr.reporter_health_sector
                  AND hr.claimed_outcome = 'deceased'
                  AND hr.moderation_status NOT IN ('rejected', 'withdrawn')
                  AND hr.archived_at IS NULL
            ) THEN 'confirmed_deceased'
            ELSE 'reported_deceased' END
        ELSE 'missing'
    END)::disaster_service.human_status;
