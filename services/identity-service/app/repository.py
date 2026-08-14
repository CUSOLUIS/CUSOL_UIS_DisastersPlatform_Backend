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
    ) -> datetime | None:
        """Consume el token una sola vez y activa la cuenta.

        Devuelve el instante de verificación o None si el token es
        inválido, vencido o ya consumido.
        """
        ...

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
    ) -> datetime | None:
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
        return now

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
