"""CHG-036 — Bootstrap local del superadministrador.

Revisa la base implementada por Codex: secreto obligatorio, creación
Argon2id, reejecución sin cambios, reconciliación y rotación explícitas,
auditoría `admin_bootstrapped` y cero secretos en la salida.
"""

from contextlib import asynccontextmanager
from uuid import uuid4

import asyncpg
import pytest

from app import bootstrap_admin


PASSWORD = "ClaveSegura#2026x"


class FakeConnection:
    def __init__(self, existing: dict | None = None):
        self.existing = existing
        self.executed: list[tuple[str, tuple]] = []
        self.closed = False

    @asynccontextmanager
    async def _transaction(self):
        yield

    def transaction(self):
        return self._transaction()

    async def fetchrow(self, sql, *args):
        return self.existing

    async def fetchval(self, sql, *args):
        # to_regclass('administration.audit_events') existe.
        return True

    async def execute(self, sql, *args):
        self.executed.append((" ".join(sql.split()), args))

    async def close(self):
        self.closed = True


def setup_environment(monkeypatch, tmp_path, **env) -> FakeConnection:
    existing = env.pop("existing", None)
    secret = tmp_path / "admin_password"
    secret.write_text(env.pop("password", PASSWORD))
    monkeypatch.setenv(
        "ADMIN_BOOTSTRAP_PASSWORD_FILE", str(secret)
    )
    monkeypatch.setenv("ADMIN_BOOTSTRAP_EMAIL", "admin@cusol.local")
    monkeypatch.setenv("ARGON2_TIME_COST", "1")
    monkeypatch.setenv("ARGON2_MEMORY_COST", "8192")
    monkeypatch.setenv("ARGON2_PARALLELISM", "1")
    monkeypatch.delenv("ADMIN_BOOTSTRAP_RECONCILE", raising=False)
    monkeypatch.delenv("ADMIN_BOOTSTRAP_ROTATE_PASSWORD", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return FakeConnection(existing)


def patch_connect(monkeypatch, connection: FakeConnection) -> None:
    async def fake_connect(*_args, **_kwargs):
        return connection

    monkeypatch.setattr(asyncpg, "connect", fake_connect)


@pytest.mark.anyio
async def test_bootstrap_fails_closed_without_secret(monkeypatch):
    monkeypatch.setenv(
        "ADMIN_BOOTSTRAP_PASSWORD_FILE", "/no/existe/secreto"
    )
    with pytest.raises(RuntimeError) as excinfo:
        await bootstrap_admin.bootstrap()
    # El error no revela rutas, correo ni contenido.
    message = str(excinfo.value)
    assert "/no/existe" not in message
    assert "admin@" not in message


@pytest.mark.anyio
async def test_bootstrap_rejects_weak_secret(monkeypatch, tmp_path):
    setup_environment(monkeypatch, tmp_path, password="corta")
    with pytest.raises(RuntimeError) as excinfo:
        await bootstrap_admin.bootstrap()
    assert "corta" not in str(excinfo.value)


@pytest.mark.anyio
async def test_bootstrap_creates_argon2id_account_and_audits(
    monkeypatch, tmp_path, capsys
):
    connection = setup_environment(monkeypatch, tmp_path)
    patch_connect(monkeypatch, connection)

    await bootstrap_admin.bootstrap()

    inserts = [item for item in connection.executed if "INSERT" in item[0]]
    account_insert = next(
        item for item in inserts if "identity_service.accounts" in item[0]
    )
    password_hash = account_insert[1][2]
    assert password_hash.startswith("$argon2id$")
    assert PASSWORD not in password_hash
    audit_insert = next(
        item
        for item in inserts
        if "administration.audit_events" in item[0]
    )
    assert "admin_bootstrapped" in audit_insert[0]
    # Cero secretos en la salida del comando.
    output = capsys.readouterr().out
    assert PASSWORD not in output
    assert "$argon2id$" not in output


@pytest.mark.anyio
async def test_bootstrap_rerun_makes_no_changes(monkeypatch, tmp_path):
    connection = setup_environment(
        monkeypatch,
        tmp_path,
        existing={
            "id": uuid4(),
            "assigned_role": "super_admin",
            "status": "active",
        },
    )
    patch_connect(monkeypatch, connection)

    await bootstrap_admin.bootstrap()

    assert connection.executed == []


@pytest.mark.anyio
async def test_bootstrap_never_rotates_hash_without_flag(
    monkeypatch, tmp_path
):
    connection = setup_environment(
        monkeypatch,
        tmp_path,
        existing={
            "id": uuid4(),
            "assigned_role": "user",
            "status": "suspended",
        },
        ADMIN_BOOTSTRAP_RECONCILE="true",
    )
    patch_connect(monkeypatch, connection)

    await bootstrap_admin.bootstrap()

    updates = [item for item in connection.executed if "UPDATE" in item[0]]
    assert updates, "la reconciliación debe actualizar rol/estado"
    assert "assigned_role = 'super_admin'" in updates[0][0]
    assert "password_hash" not in updates[0][0]


@pytest.mark.anyio
async def test_bootstrap_rotates_hash_only_with_explicit_flag(
    monkeypatch, tmp_path
):
    connection = setup_environment(
        monkeypatch,
        tmp_path,
        existing={
            "id": uuid4(),
            "assigned_role": "super_admin",
            "status": "active",
        },
        ADMIN_BOOTSTRAP_ROTATE_PASSWORD="true",
    )
    patch_connect(monkeypatch, connection)

    await bootstrap_admin.bootstrap()

    updates = [item for item in connection.executed if "UPDATE" in item[0]]
    assert updates
    assert "password_hash" in updates[0][0]
    rotated_hash = updates[0][1][0]
    assert rotated_hash.startswith("$argon2id$")
