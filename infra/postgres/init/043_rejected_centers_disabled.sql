-- CHG-169 — Un acopio con verificación rechazada queda deshabilitado
-- y fuera del mapa. Esta migración aplica la regla a los rechazados
-- ANTERIORES a ella (hasta CHG-168 seguían publicados como «Sin
-- verificar»). Idempotente: la segunda pasada no encuentra filas;
-- preserva datos (nada se borra, el super_admin puede reactivar).

UPDATE disaster_service.aid_locations
SET operational_status = 'inactive',
    disabled_at = NOW(),
    updated_at = NOW()
WHERE verification_status = 'rejected'
  AND disabled_at IS NULL
  AND operational_status <> 'inactive';
