-- CHG-036 — Roles administrativos de aplicación.
-- Nunca representan privilegios del sistema operativo o de PostgreSQL.

ALTER TYPE identity_service.account_role
    ADD VALUE IF NOT EXISTS 'moderator';

ALTER TYPE identity_service.account_role
    ADD VALUE IF NOT EXISTS 'super_admin';

ALTER TABLE identity_service.accounts
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

