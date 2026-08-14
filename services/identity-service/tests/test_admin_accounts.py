"""CHG-036 — Administración de cuentas (identity-service).

Cubre defensa en profundidad del rol, listado sin credenciales,
concurrencia, protección del último super_admin, self-action y
revocación de sesiones.
"""

import base64
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest

from app.config import Settings
from app.main import create_app


FAST_SETTINGS = Settings(
    database_url="postgresql://unused",
    argon2_time_cost=1,
    argon2_memory_cost=8192,
    argon2_parallelism=1,
)

ACTOR_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
TARGET_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2")
CREATED_AT = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)

ADMIN_HEADERS = {
    "X-Actor-Role": "super_admin",
    "X-Actor-Account-Id": str(ACTOR_ID),
    "X-Actor-Display": base64.b64encode("Admin CUSOL".encode()).decode(),
}


def account_row(**overrides) -> dict:
    row = {
        "id": TARGET_ID,
        "email": "persona@cusol.local",
        "first_names": "Persona",
        "last_names": "Demo",
        "assigned_role": "user",
        "status": "active",
        "department": "Santander",
        "municipality": "Bucaramanga",
        "requested_account_type": "citizen",
        "organization_name": None,
        "organization_role": None,
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
        "version": 1,
        "active_sessions": 2,
    }
    row.update(overrides)
    return row


class FakeAdminIdentityRepository:
    def __init__(self):
        self.account = account_row()
        self.update_outcome = "ok"
        self.update_args = None
        self.revoked = []
        self.audits = []
        self.list_args = None

    async def ping(self):
        return True

    async def admin_accounts_overview(self):
        return {"active_accounts": 5, "suspended_accounts": 1}

    async def admin_list_accounts(self, q, role, status, limit, offset):
        self.list_args = {
            "q": q, "role": role, "status": status,
            "limit": limit, "offset": offset,
        }
        return [dict(self.account)], 1

    async def admin_get_account(self, account_id):
        if account_id != self.account["id"]:
            return None
        return dict(self.account)

    async def admin_update_account(
        self,
        account_id,
        expected_version,
        new_role,
        new_status,
        actor_account_id,
        actor_display_name,
        reason_encrypted,
    ):
        self.update_args = {
            "account_id": account_id,
            "expected_version": expected_version,
            "new_role": new_role,
            "new_status": new_status,
        }
        if self.update_outcome != "ok":
            return self.update_outcome, None
        if new_role:
            self.account["assigned_role"] = new_role
        if new_status:
            self.account["status"] = new_status
        self.account["version"] += 1
        return "ok", uuid4()

    async def admin_revoke_sessions(
        self, account_id, actor_account_id, actor_display_name,
        reason_encrypted,
    ):
        if account_id != self.account["id"]:
            return False, None
        self.revoked.append(account_id)
        return True, uuid4()

    async def admin_write_audit(self, *args, **kwargs):
        self.audits.append(args)
        return uuid4()


async def request_app(app, method, path, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)


def admin_app(repository=None):
    return create_app(
        settings=FAST_SETTINGS,
        repository=repository or FakeAdminIdentityRepository(),
    )


@pytest.mark.anyio
async def test_admin_account_routes_require_super_admin():
    app = admin_app()

    for method, path in (
        ("GET", "/internal/v1/admin/accounts"),
        ("GET", f"/internal/v1/admin/accounts/{TARGET_ID}"),
        ("GET", "/internal/v1/admin/accounts-overview"),
    ):
        without = await request_app(app, method, path)
        as_user = await request_app(
            app, method, path,
            headers={**ADMIN_HEADERS, "X-Actor-Role": "user"},
        )
        assert without.status_code == 403
        assert as_user.status_code == 403


@pytest.mark.anyio
async def test_admin_accounts_list_never_exposes_credentials():
    repository = FakeAdminIdentityRepository()
    app = admin_app(repository=repository)

    response = await request_app(
        app,
        "GET",
        "/internal/v1/admin/accounts?q=persona&role=user&status=active"
        "&limit=10",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert repository.list_args["q"] == "persona"
    assert repository.list_args["role"] == "user"
    assert repository.list_args["limit"] == 10
    item = response.json()["items"][0]
    assert set(item.keys()) == {
        "id", "displayName", "email", "assignedRole", "status",
        "activeSessions", "createdAt", "updatedAt", "version",
    }
    assert "passwordHash" not in response.text
    assert "phone" not in response.text


@pytest.mark.anyio
async def test_admin_account_detail_includes_profile_without_secrets():
    app = admin_app()

    response = await request_app(
        app,
        "GET",
        f"/internal/v1/admin/accounts/{TARGET_ID}",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["department"] == "Santander"
    assert body["requestedAccountType"] == "citizen"
    assert "passwordHash" not in response.text


@pytest.mark.anyio
async def test_admin_account_update_changes_role_with_version():
    repository = FakeAdminIdentityRepository()
    app = admin_app(repository=repository)

    response = await request_app(
        app,
        "PATCH",
        f"/internal/v1/admin/accounts/{TARGET_ID}",
        headers=ADMIN_HEADERS,
        json={
            "expectedVersion": 1,
            "reason": "Promoción a moderación humanitaria.",
            "assignedRole": "moderator",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["assignedRole"] == "moderator"
    assert body["version"] == 2
    assert repository.update_args["new_role"] == "moderator"


@pytest.mark.anyio
async def test_admin_account_update_requires_role_or_status():
    app = admin_app()

    response = await request_app(
        app,
        "PATCH",
        f"/internal/v1/admin/accounts/{TARGET_ID}",
        headers=ADMIN_HEADERS,
        json={
            "expectedVersion": 1,
            "reason": "Sin cambios declarados aquí.",
        },
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_admin_account_update_blocks_self_action():
    repository = FakeAdminIdentityRepository()
    repository.account = account_row(id=ACTOR_ID)
    app = admin_app(repository=repository)

    response = await request_app(
        app,
        "PATCH",
        f"/internal/v1/admin/accounts/{ACTOR_ID}",
        headers=ADMIN_HEADERS,
        json={
            "expectedVersion": 1,
            "reason": "Intento de auto-degradación.",
            "assignedRole": "user",
        },
    )

    assert response.status_code == 409
    assert repository.update_args is None
    # El intento negado queda auditado.
    assert repository.audits


@pytest.mark.anyio
async def test_admin_account_update_conflict_and_last_admin():
    repository = FakeAdminIdentityRepository()
    repository.update_outcome = "conflict"
    app = admin_app(repository=repository)
    conflict = await request_app(
        app,
        "PATCH",
        f"/internal/v1/admin/accounts/{TARGET_ID}",
        headers=ADMIN_HEADERS,
        json={
            "expectedVersion": 1,
            "reason": "Versión desactualizada.",
            "status": "suspended",
        },
    )

    repository.update_outcome = "last_admin"
    last_admin = await request_app(
        app,
        "PATCH",
        f"/internal/v1/admin/accounts/{TARGET_ID}",
        headers=ADMIN_HEADERS,
        json={
            "expectedVersion": 1,
            "reason": "Degradar al único admin.",
            "assignedRole": "user",
        },
    )

    assert conflict.status_code == 409
    assert last_admin.status_code == 409
    assert "super_admin" in last_admin.json()["detail"]


@pytest.mark.anyio
async def test_admin_revoke_sessions_is_204_and_audited():
    repository = FakeAdminIdentityRepository()
    app = admin_app(repository=repository)

    revoked = await request_app(
        app,
        "DELETE",
        f"/internal/v1/admin/accounts/{TARGET_ID}/sessions",
        headers=ADMIN_HEADERS,
        json={"reason": "Sospecha de robo de sesión."},
    )
    missing = await request_app(
        app,
        "DELETE",
        f"/internal/v1/admin/accounts/{uuid4()}/sessions",
        headers=ADMIN_HEADERS,
        json={"reason": "Cuenta inexistente aquí."},
    )

    assert revoked.status_code == 204
    assert repository.revoked == [TARGET_ID]
    assert missing.status_code == 404
