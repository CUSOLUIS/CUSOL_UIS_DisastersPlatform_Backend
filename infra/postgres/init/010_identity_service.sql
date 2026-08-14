-- CHG-022 / ADR-004 — Esquema propietario del identity-service.
-- Cuentas privadas, verificación de correo y sesiones opacas.
-- Nunca se almacenan contraseñas ni tokens en claro: solo hash Argon2id
-- (contraseñas) y SHA-256 (tokens aleatorios de >= 32 bytes).
-- Idempotente: IF NOT EXISTS / duplicate_object; re-ejecutable.

CREATE SCHEMA IF NOT EXISTS identity_service;

DO $$
BEGIN
    CREATE TYPE identity_service.requested_account_type AS ENUM (
        'citizen',
        'volunteer',
        'organization_representative'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE identity_service.account_role AS ENUM (
        'user'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
    CREATE TYPE identity_service.account_status AS ENUM (
        'pending_verification',
        'active',
        'suspended'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

CREATE TABLE IF NOT EXISTS identity_service.accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL UNIQUE,
    first_names TEXT NOT NULL,
    last_names TEXT NOT NULL,
    phone TEXT,
    department TEXT NOT NULL,
    municipality TEXT NOT NULL,
    requested_account_type identity_service.requested_account_type NOT NULL,
    organization_name TEXT,
    organization_role TEXT,
    assigned_role identity_service.account_role NOT NULL DEFAULT 'user',
    password_hash TEXT NOT NULL,
    status identity_service.account_status NOT NULL
        DEFAULT 'pending_verification',
    email_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT account_email_not_blank CHECK (btrim(email) <> ''),
    CONSTRAINT account_email_normalized CHECK (email = lower(btrim(email))),
    CONSTRAINT account_org_name_required CHECK (
        requested_account_type <> 'organization_representative'
        OR (organization_name IS NOT NULL
            AND btrim(organization_name) <> '')
    )
);

CREATE TABLE IF NOT EXISTS identity_service.email_verification_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL
        REFERENCES identity_service.accounts(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS email_verification_tokens_account_idx
    ON identity_service.email_verification_tokens (account_id);

CREATE TABLE IF NOT EXISTS identity_service.sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL
        REFERENCES identity_service.accounts(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS sessions_account_idx
    ON identity_service.sessions (account_id);
