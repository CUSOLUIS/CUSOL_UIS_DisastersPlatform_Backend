-- CHG-174 — Aceptación inicial de ruta entre Centro de Acopio Local y
-- Mulera. Idempotente. No borra ni altera nada existente: los
-- transportes históricos simplemente no tienen filas en estas tablas
-- (§84 del contrato: no inventar aceptaciones para el histórico).
--
-- Dos etapas que el contrato exige mantener separadas (§4):
--   1) aceptación de SOLICITUD  → transport_center_requests
--   2) aceptación de RUTA       → transport_route_acceptances

-- 1) Solicitud a cada centro involucrado en un transporte. Se crea una
--    por lado (origen local y destino receptor) en la misma
--    transacción del alta del transporte (§7).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'transport_request_status'
          AND n.nspname = 'disaster_service'
    ) THEN
        CREATE TYPE disaster_service.transport_request_status
            AS ENUM ('pending', 'accepted', 'declined');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'transport_center_role'
          AND n.nspname = 'disaster_service'
    ) THEN
        CREATE TYPE disaster_service.transport_center_role
            AS ENUM ('local', 'reception');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'transport_route_acceptance_status'
          AND n.nspname = 'disaster_service'
    ) THEN
        -- code_issued: el Centro Local ya emitió el código y espera a
        -- la Mulera. accepted: Local ↔ Mulera completada. La relación
        -- Mulera ↔ Receptor NO se modela aquí (§76).
        CREATE TYPE disaster_service.transport_route_acceptance_status
            AS ENUM ('code_issued', 'accepted');
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS disaster_service.transport_center_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transport_id UUID NOT NULL
        REFERENCES disaster_service.humanitarian_transports(id)
        ON DELETE CASCADE,
    center_id UUID NOT NULL
        REFERENCES disaster_service.aid_locations(id)
        ON DELETE CASCADE,
    -- Qué papel juega ese centro en ESTE transporte.
    center_role disaster_service.transport_center_role NOT NULL,
    status disaster_service.transport_request_status
        NOT NULL DEFAULT 'pending',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    -- Cuenta que aceptó o declinó (responsable del centro o super_admin).
    decided_by UUID,
    -- §51: un solo proceso por transporte y centro.
    UNIQUE (transport_id, center_id)
);

-- La bandeja del centro lista lo pendiente primero y lo más reciente
-- arriba (§65).
CREATE INDEX IF NOT EXISTS transport_center_requests_center_idx
    ON disaster_service.transport_center_requests
    (center_id, status, requested_at DESC);

-- 2) Aceptación de ruta Local ↔ Mulera. Una sola por transporte y
--    centro local: el doble clic en ACEPTAR RUTA no puede abrir dos
--    procesos ni emitir dos códigos (§51-§52).
CREATE TABLE IF NOT EXISTS disaster_service.transport_route_acceptances (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transport_id UUID NOT NULL
        REFERENCES disaster_service.humanitarian_transports(id)
        ON DELETE CASCADE,
    local_center_id UUID NOT NULL
        REFERENCES disaster_service.aid_locations(id)
        ON DELETE CASCADE,
    -- Código único de registro de ruta: lo genera el backend, es de un
    -- solo uso y jamás se escribe en la auditoría (§28-§30, §54, §62).
    confirmation_code TEXT NOT NULL,
    code_used_at TIMESTAMPTZ,
    local_accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    local_accepted_by UUID,
    mule_code_validated_at TIMESTAMPTZ,
    mule_accepted_at TIMESTAMPTZ,
    mule_accepted_by UUID,
    status disaster_service.transport_route_acceptance_status
        NOT NULL DEFAULT 'code_issued',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (transport_id, local_center_id)
);

-- El código no puede repetirse entre transportes (§28).
CREATE UNIQUE INDEX IF NOT EXISTS transport_route_acceptances_code_idx
    ON disaster_service.transport_route_acceptances (confirmation_code);
