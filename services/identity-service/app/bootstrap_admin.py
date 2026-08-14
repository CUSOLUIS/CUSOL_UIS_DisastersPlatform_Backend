"""Bootstrap local idempotente del superadministrador (CHG-036).

La contraseña solo se lee desde un archivo montado como secreto y se descarta
después de generar Argon2id. Este comando no expone credenciales ni hashes.
"""

import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path
from uuid import uuid4

import asyncpg

from .config import Settings
from .security import (
    build_password_hasher,
    is_valid_email,
    normalize_email,
    password_policy_errors,
)


async def _audit_bootstrap(
    connection: asyncpg.Connection, account_id, detail: str
) -> None:
    """Audita `admin_bootstrapped` sin secreto, correo ni hash (CHG-036).

    La tabla la crea la migración 014; si aún no existe (base recién
    inicializada a medias) el bootstrap no debe fallar por auditar.
    """
    exists = await connection.fetchval(
        "SELECT to_regclass('administration.audit_events') IS NOT NULL"
    )
    if not exists:
        return
    await connection.execute(
        """
        INSERT INTO administration.audit_events (
            actor_account_id, actor_display_name, action,
            resource_kind, resource_id, result, changed_fields
        ) VALUES ($1, 'Bootstrap local', 'admin_bootstrapped',
                  'account', $1, 'success', $2)
        """,
        account_id,
        [detail],
    )


async def bootstrap() -> None:
    settings = Settings.from_environment()
    email = normalize_email(
        os.getenv("ADMIN_BOOTSTRAP_EMAIL", "admin@cusol.local")
    )
    secret_path = Path(
        os.getenv(
            "ADMIN_BOOTSTRAP_PASSWORD_FILE",
            "/run/secrets/cusol/admin_password",
        )
    )
    reconcile = os.getenv(
        "ADMIN_BOOTSTRAP_RECONCILE", "false"
    ).lower() in {"1", "true", "yes"}
    rotate_password = os.getenv(
        "ADMIN_BOOTSTRAP_ROTATE_PASSWORD", "false"
    ).lower() in {"1", "true", "yes"}

    if not secret_path.is_file():
        raise RuntimeError(
            "Bootstrap administrativo fallido: secreto no configurado."
        )
    password = secret_path.read_text(encoding="utf-8").rstrip("\r\n")
    if not is_valid_email(email):
        raise RuntimeError("ADMIN_BOOTSTRAP_EMAIL no es válido.")
    errors = password_policy_errors(password)
    if errors:
        raise RuntimeError(
            "El secreto administrativo no cumple la política de contraseña."
        )

    hasher = build_password_hasher(
        settings.argon2_time_cost,
        settings.argon2_memory_cost,
        settings.argon2_parallelism,
    )
    password_hash = hasher.hash(password)
    password = ""
    connection = await asyncpg.connect(settings.database_url, timeout=5)
    try:
        async with connection.transaction():
            existing = await connection.fetchrow(
                """
                SELECT id, assigned_role::text AS assigned_role, status::text
                FROM identity_service.accounts
                WHERE email = $1
                FOR UPDATE
                """,
                email,
            )
            now = datetime.now(UTC)
            if existing is None:
                account_id = uuid4()
                await connection.execute(
                    """
                    INSERT INTO identity_service.accounts (
                        id, email, first_names, last_names, department,
                        municipality, requested_account_type, assigned_role,
                        password_hash, status, email_verified_at,
                        created_at, updated_at
                    ) VALUES (
                        $1, $2, 'Administrador', 'CUSOL', 'Santander',
                        'Bucaramanga', 'citizen', 'super_admin', $3,
                        'active', $4, $4, $4
                    )
                    """,
                    account_id,
                    email,
                    password_hash,
                    now,
                )
                await _audit_bootstrap(
                    connection, account_id, "created"
                )
                print("Cuenta super_admin local creada correctamente.")
                return

            changes: list[str] = []
            values: list[object] = []
            if reconcile and (
                existing["assigned_role"] != "super_admin"
                or existing["status"] != "active"
            ):
                changes.extend(
                    ["assigned_role = 'super_admin'", "status = 'active'"]
                )
            if rotate_password:
                values.append(password_hash)
                changes.append(f"password_hash = ${len(values) + 1}")
            if changes:
                values.append(email)
                await connection.execute(
                    "UPDATE identity_service.accounts SET "
                    + ", ".join(changes)
                    + ", updated_at = NOW(), version = version + 1 "
                    + f"WHERE email = ${len(values)}",
                    *values,
                )
                await _audit_bootstrap(
                    connection, existing["id"], "reconciled"
                )
                print("Cuenta super_admin local reconciliada correctamente.")
            else:
                print("Cuenta administrativa existente; sin cambios.")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(bootstrap())
