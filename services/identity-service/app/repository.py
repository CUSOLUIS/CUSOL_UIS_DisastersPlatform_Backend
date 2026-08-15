from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import asyncpg


@dataclass(frozen=True)
class AccountRecord:
    id: UUID
    email: str
    first_names: str
    last_names: str
    assigned_role: str
    status: str
    password_hash: str


@dataclass(frozen=True)
class SessionAccount:
    account: AccountRecord
    session_expires_at: datetime


@dataclass(frozen=True)
class NewAccount:
    id: UUID
    email: str
    first_names: str
    last_names: str
    phone: str | None
    department: str
    municipality: str
    requested_account_type: str
    organization_name: str | None
    organization_role: str | None
    password_hash: str


class IdentityRepository(Protocol):
    async def ping(self) -> bool: ...

    async def create_account(self, account: NewAccount) -> bool:
        """True si la cuenta fue creada; False si el correo ya existía."""
        ...

    async def create_verification_token(
        self, account_id: UUID, token_hash: str, expires_at: datetime
    ) -> None: ...

    async def consume_verification_token(
        self, token_hash: str, now: datetime
    ) -> UUID | None:
        """Consume el token una sola vez y activa la cuenta.

        Devuelve el id de la cuenta verificada o None si el token es
        inválido, vencido o ya consumido. CHG-051: el id permite emitir
        la sesión de bienvenida en el mismo paso.
        """
        ...

    async def get_account_by_id(
        self, account_id: UUID
    ) -> "AccountRecord | None": ...

    async def get_account_by_email(
        self, email: str
    ) -> AccountRecord | None: ...

    async def create_session(
        self, account_id: UUID, token_hash: str, expires_at: datetime
    ) -> None: ...

    async def revoke_session(self, token_hash: str) -> None: ...

    async def get_session_account(
        self, token_hash: str, now: datetime
    ) -> SessionAccount | None: ...


class PostgresIdentityRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def ping(self) -> bool:
        return await self._pool.fetchval("SELECT 1") == 1

    async def create_account(self, account: NewAccount) -> bool:
        row = await self._pool.fetchrow(
            """
            INSERT INTO identity_service.accounts (
                id, email, first_names, last_names, phone,
                department, municipality, requested_account_type,
                organization_name, organization_role, password_hash
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8::identity_service.requested_account_type, $9, $10, $11
            )
            ON CONFLICT (email) DO NOTHING
            RETURNING id
            """,
            account.id,
            account.email,
            account.first_names,
            account.last_names,
            account.phone,
            account.department,
            account.municipality,
            account.requested_account_type,
            account.organization_name,
            account.organization_role,
            account.password_hash,
        )
        return row is not None

    async def create_verification_token(
        self, account_id: UUID, token_hash: str, expires_at: datetime
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO identity_service.email_verification_tokens (
                account_id, token_hash, expires_at
            ) VALUES ($1, $2, $3)
            """,
            account_id,
            token_hash,
            expires_at,
        )

    async def consume_verification_token(
        self, token_hash: str, now: datetime
    ) -> UUID | None:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    UPDATE identity_service.email_verification_tokens
                    SET consumed_at = $2
                    WHERE token_hash = $1
                      AND consumed_at IS NULL
                      AND expires_at > $2
                    RETURNING account_id
                    """,
                    token_hash,
                    now,
                )
                if row is None:
                    return None
                await connection.execute(
                    """
                    UPDATE identity_service.accounts
                    SET status = 'active',
                        email_verified_at = $2,
                        updated_at = $2
                    WHERE id = $1
                      AND status = 'pending_verification'
                    """,
                    row["account_id"],
                    now,
                )
        return row["account_id"]

    async def get_account_by_id(
        self, account_id: UUID
    ) -> AccountRecord | None:
        row = await self._pool.fetchrow(
            """
            SELECT id, email, first_names, last_names,
                   assigned_role, status, password_hash
            FROM identity_service.accounts
            WHERE id = $1
            """,
            account_id,
        )
        if row is None:
            return None
        return AccountRecord(
            id=row["id"],
            email=row["email"],
            first_names=row["first_names"],
            last_names=row["last_names"],
            assigned_role=row["assigned_role"],
            status=row["status"],
            password_hash=row["password_hash"],
        )

    async def get_account_by_email(
        self, email: str
    ) -> AccountRecord | None:
        row = await self._pool.fetchrow(
            """
            SELECT id, email, first_names, last_names,
                   assigned_role, status, password_hash
            FROM identity_service.accounts
            WHERE email = $1
            """,
            email,
        )
        if row is None:
            return None
        return AccountRecord(
            id=row["id"],
            email=row["email"],
            first_names=row["first_names"],
            last_names=row["last_names"],
            assigned_role=row["assigned_role"],
            status=row["status"],
            password_hash=row["password_hash"],
        )

    async def create_session(
        self, account_id: UUID, token_hash: str, expires_at: datetime
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO identity_service.sessions (
                account_id, token_hash, expires_at
            ) VALUES ($1, $2, $3)
            """,
            account_id,
            token_hash,
            expires_at,
        )

    async def revoke_session(self, token_hash: str) -> None:
        await self._pool.execute(
            """
            UPDATE identity_service.sessions
            SET revoked_at = NOW()
            WHERE token_hash = $1 AND revoked_at IS NULL
            """,
            token_hash,
        )

    async def get_session_account(
        self, token_hash: str, now: datetime
    ) -> SessionAccount | None:
        row = await self._pool.fetchrow(
            """
            SELECT a.id, a.email, a.first_names, a.last_names,
                   a.assigned_role, a.status, a.password_hash,
                   s.expires_at
            FROM identity_service.sessions s
            INNER JOIN identity_service.accounts a
                ON a.id = s.account_id
            WHERE s.token_hash = $1
              AND s.revoked_at IS NULL
              AND s.expires_at > $2
              AND a.status = 'active'
            """,
            token_hash,
            now,
        )
        if row is None:
            return None
        return SessionAccount(
            account=AccountRecord(
                id=row["id"],
                email=row["email"],
                first_names=row["first_names"],
                last_names=row["last_names"],
                assigned_role=row["assigned_role"],
                status=row["status"],
                password_hash=row["password_hash"],
            ),
            session_expires_at=row["expires_at"],
        )

    # CHG-036 — Administración de cuentas. Nunca se selecciona
    # password_hash ni tokens; el teléfono tampoco viaja a la consola.

    async def admin_accounts_overview(self) -> dict:
        rows = await self._pool.fetch(
            """
            SELECT status::text AS status, COUNT(*)::int AS quantity
            FROM identity_service.accounts
            GROUP BY status
            """
        )
        by_status = {row["status"]: row["quantity"] for row in rows}
        return {
            "active_accounts": by_status.get("active", 0),
            "suspended_accounts": by_status.get("suspended", 0),
        }

    async def admin_list_accounts(
        self,
        q: str | None,
        role: str | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict], int]:
        clauses: list[str] = []
        values: list[object] = []
        if q is not None:
            values.append(q.strip())
            position = len(values)
            clauses.append(
                f"""lower(unaccent(
                    a.email || ' ' || a.first_names || ' ' ||
                    a.last_names
                )) LIKE '%' || lower(unaccent(${position})) || '%'"""
            )
        if role is not None:
            values.append(role)
            clauses.append(f"a.assigned_role::text = ${len(values)}")
        if status is not None:
            values.append(status)
            clauses.append(f"a.status::text = ${len(values)}")
        where_clause = (
            "WHERE " + " AND ".join(clauses) if clauses else ""
        )
        total = await self._pool.fetchval(
            f"""
            SELECT COUNT(*)
            FROM identity_service.accounts a
            {where_clause}
            """,
            *values,
        )
        limit_parameter = len(values) + 1
        offset_parameter = len(values) + 2
        rows = await self._pool.fetch(
            f"""
            {_ADMIN_ACCOUNT_SELECT}
            {where_clause}
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ${limit_parameter} OFFSET ${offset_parameter}
            """,
            *values,
            limit,
            offset,
        )
        return [dict(row) for row in rows], int(total)

    async def admin_get_account(self, account_id: UUID) -> dict | None:
        row = await self._pool.fetchrow(
            f"{_ADMIN_ACCOUNT_SELECT} WHERE a.id = $1",
            account_id,
        )
        return dict(row) if row is not None else None

    async def _admin_audit(
        self,
        connection,
        actor_account_id: UUID,
        actor_display_name: str,
        action: str,
        resource_id: UUID,
        result: str,
        reason_encrypted: bytes | None,
        changed_fields: list[str],
    ) -> UUID:
        return await connection.fetchval(
            """
            INSERT INTO administration.audit_events (
                actor_account_id, actor_display_name, action,
                resource_kind, resource_id, result, reason_protected,
                changed_fields
            ) VALUES ($1, $2, $3, 'account', $4, $5, $6, $7)
            RETURNING id
            """,
            actor_account_id,
            actor_display_name,
            action,
            resource_id,
            result,
            reason_encrypted,
            changed_fields,
        )

    async def admin_write_audit(
        self,
        actor_account_id: UUID,
        actor_display_name: str,
        action: str,
        resource_id: UUID,
        result: str,
        reason_encrypted: bytes | None = None,
        changed_fields: list[str] | None = None,
    ) -> UUID:
        async with self._pool.acquire() as connection:
            return await self._admin_audit(
                connection,
                actor_account_id,
                actor_display_name,
                action,
                resource_id,
                result,
                reason_encrypted,
                changed_fields or [],
            )

    async def admin_update_account(
        self,
        account_id: UUID,
        expected_version: int,
        new_role: str | None,
        new_status: str | None,
        actor_account_id: UUID,
        actor_display_name: str,
        reason_encrypted: bytes | None,
    ) -> tuple[str, UUID | None]:
        """Cambia rol/estado con versión y protege al último admin.

        Devuelve (outcome, audit_event_id) con outcome en
        {'ok', 'conflict', 'not_found', 'last_admin'}.
        """
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                current = await connection.fetchrow(
                    """
                    SELECT assigned_role::text AS assigned_role,
                           status::text AS status, version
                    FROM identity_service.accounts
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    account_id,
                )
                if current is None:
                    return "not_found", None
                if current["version"] != expected_version:
                    audit_id = await self._admin_audit(
                        connection,
                        actor_account_id,
                        actor_display_name,
                        "account_updated",
                        account_id,
                        "failed",
                        None,
                        [],
                    )
                    return "conflict", audit_id
                resolved_role = new_role or current["assigned_role"]
                resolved_status = new_status or current["status"]
                was_active_admin = (
                    current["assigned_role"] == "super_admin"
                    and current["status"] == "active"
                )
                stays_active_admin = (
                    resolved_role == "super_admin"
                    and resolved_status == "active"
                )
                if was_active_admin and not stays_active_admin:
                    # Bloqueo transaccional del último super_admin
                    # activo: se bloquean las filas pares antes de
                    # contar para impedir degradaciones concurrentes.
                    others = await connection.fetch(
                        """
                        SELECT id
                        FROM identity_service.accounts
                        WHERE assigned_role = 'super_admin'
                          AND status = 'active'
                          AND id <> $1
                        FOR UPDATE
                        """,
                        account_id,
                    )
                    if not others:
                        audit_id = await self._admin_audit(
                            connection,
                            actor_account_id,
                            actor_display_name,
                            "account_updated",
                            account_id,
                            "denied",
                            reason_encrypted,
                            [],
                        )
                        return "last_admin", audit_id
                changed: list[str] = []
                if resolved_role != current["assigned_role"]:
                    changed.append("assignedRole")
                if resolved_status != current["status"]:
                    changed.append("status")
                await connection.execute(
                    """
                    UPDATE identity_service.accounts
                    SET assigned_role =
                            $2::identity_service.account_role,
                        status = $3::identity_service.account_status,
                        version = version + 1,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    account_id,
                    resolved_role,
                    resolved_status,
                )
                audit_id = await self._admin_audit(
                    connection,
                    actor_account_id,
                    actor_display_name,
                    "account_updated",
                    account_id,
                    "success",
                    reason_encrypted,
                    changed,
                )
        return "ok", audit_id

    async def admin_revoke_sessions(
        self,
        account_id: UUID,
        actor_account_id: UUID,
        actor_display_name: str,
        reason_encrypted: bytes | None,
    ) -> tuple[bool, UUID | None]:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                exists = await connection.fetchval(
                    """
                    SELECT 1 FROM identity_service.accounts
                    WHERE id = $1
                    """,
                    account_id,
                )
                if not exists:
                    return False, None
                await connection.execute(
                    """
                    UPDATE identity_service.sessions
                    SET revoked_at = NOW()
                    WHERE account_id = $1 AND revoked_at IS NULL
                    """,
                    account_id,
                )
                audit_id = await self._admin_audit(
                    connection,
                    actor_account_id,
                    actor_display_name,
                    "account_sessions_revoked",
                    account_id,
                    "success",
                    reason_encrypted,
                    [],
                )
        return True, audit_id


# CHG-036 — Selección administrativa: sin hash, tokens ni teléfono.
_ADMIN_ACCOUNT_SELECT = """
    SELECT a.id, a.email, a.first_names, a.last_names,
           a.assigned_role::text AS assigned_role,
           a.status::text AS status,
           a.department, a.municipality,
           a.requested_account_type::text AS requested_account_type,
           a.organization_name, a.organization_role,
           a.created_at, a.updated_at, a.version,
           (SELECT COUNT(*)
            FROM identity_service.sessions s
            WHERE s.account_id = a.id
              AND s.revoked_at IS NULL
              AND s.expires_at > NOW())::int AS active_sessions
    FROM identity_service.accounts a
"""
