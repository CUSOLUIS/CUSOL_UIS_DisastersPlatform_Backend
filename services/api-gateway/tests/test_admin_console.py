"""CHG-036 — Gateway de la consola de superadministración.

Toda ruta /api/v1/admin/* resuelve la cookie contra identity en cada
solicitud, exige rol super_admin, aplica CSRF y límites propios, y es el
único que escribe los encabezados internos de actor.
"""

import base64

import httpx
import pytest

from app.config import Settings
from app.main import create_app


DISASTER_URL = "http://disaster-service:8001"
IDENTITY_URL = "http://identity-service:8002"

ADMIN_ACCOUNT = {
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
    "displayName": "Admin CUSOL",
    "email": "admin@cusol.local",
    "assignedRole": "super_admin",
    "status": "active",
    "sessionExpiresAt": "2026-08-15T10:00:00Z",
}
USER_ACCOUNT = {
    **ADMIN_ACCOUNT,
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
    "displayName": "Usuaria Normal",
    "email": "user@cusol.local",
    "assignedRole": "user",
}

SUBMISSIONS_OVERVIEW = {
    "underReview": 3,
    "needsInformation": 1,
    "acceptedToday": 2,
    "archived": 4,
    "oldestPendingAt": "2026-08-10T08:00:00Z",
    "byKind": [{"kind": "aid_location_rating", "count": 3}],
    "recentActivity": [
        {
            "id": "dddddddd-dddd-4ddd-8ddd-ddddddddddd1",
            "action": "submission_accepted",
            "resourceKind": "aid_location_rating",
            "occurredAt": "2026-08-14T09:00:00Z",
            "result": "success",
        }
    ],
    "generatedAt": "2026-08-14T10:00:00Z",
}
ACCOUNTS_OVERVIEW = {"activeAccounts": 5, "suspendedAccounts": 1}

SUBMISSION_PAGE = {
    "items": [
        {
            "id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1",
            "kind": "unverified_building_report",
            "trackingCode": "BR-2026-AAAA1111",
            "title": "Edificio sin verificar — Torre Norte",
            "locationLabel": "Bucaramanga, Santander",
            "status": "under_review",
            "sourceLabel": "Reporte ciudadano",
            "evidenceCount": 1,
            "receivedAt": "2026-08-14T10:00:00Z",
            "updatedAt": "2026-08-14T10:00:00Z",
            "version": 1,
        }
    ],
    "total": 1,
    "limit": 25,
    "offset": 0,
    "generatedAt": "2026-08-14T10:00:00Z",
}

ACCOUNT_DETAIL = {
    "id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2",
    "displayName": "Persona Demo",
    "email": "persona@cusol.local",
    "assignedRole": "moderator",
    "status": "active",
    "activeSessions": 1,
    "createdAt": "2026-08-01T10:00:00Z",
    "updatedAt": "2026-08-14T10:00:00Z",
    "version": 2,
    "department": "Santander",
    "municipality": "Bucaramanga",
    "requestedAccountType": "citizen",
    "organizationName": None,
    "organizationRole": None,
}


def gateway_settings(**overrides) -> Settings:
    values = {
        "disaster_service_url": DISASTER_URL,
        "identity_service_url": IDENTITY_URL,
        "upstream_timeout_seconds": 1,
    }
    values.update(overrides)
    return Settings(**values)


def identity_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/internal/v1/auth/me":
        token = request.headers.get("x-session-token")
        if token == "token-admin":
            return httpx.Response(200, json=ADMIN_ACCOUNT)
        if token == "token-user":
            return httpx.Response(200, json=USER_ACCOUNT)
        return httpx.Response(
            401,
            json={
                "type": "session-required",
                "title": "Sesión requerida",
                "status": 401,
                "detail": "La sesión está ausente, vencida o revocada.",
            },
        )
    if request.url.path == "/internal/v1/admin/accounts-overview":
        return httpx.Response(200, json=ACCOUNTS_OVERVIEW)
    raise AssertionError(f"ruta identity inesperada: {request.url.path}")


def make_clients(disaster_handler, identity=identity_handler):
    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(disaster_handler),
        base_url=DISASTER_URL,
    )
    identity_client = httpx.AsyncClient(
        transport=httpx.MockTransport(identity), base_url=IDENTITY_URL
    )
    return upstream, identity_client


async def request_gateway(app, method, path, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)


ADMIN_COOKIE = {"Cookie": "cusol_session=token-admin"}
USER_COOKIE = {"Cookie": "cusol_session=token-user"}


@pytest.mark.anyio
async def test_admin_routes_require_session_and_role():
    calls = []

    def disaster_handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(200, json=SUBMISSIONS_OVERVIEW)

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    anonymous = await request_gateway(app, "GET", "/api/v1/admin/overview")
    as_user = await request_gateway(
        app, "GET", "/api/v1/admin/overview", headers=USER_COOKIE
    )
    # Los encabezados internos del cliente jamás se aceptan.
    spoofed = await request_gateway(
        app,
        "GET",
        "/api/v1/admin/overview",
        headers={
            **USER_COOKIE,
            "X-Actor-Role": "super_admin",
            "X-Actor-Account-Id": ADMIN_ACCOUNT["id"],
        },
    )
    await upstream.aclose()
    await identity.aclose()

    assert anonymous.status_code == 401
    assert as_user.status_code == 403
    assert spoofed.status_code == 403
    assert calls == []


@pytest.mark.anyio
async def test_admin_overview_merges_both_services():
    def disaster_handler(request: httpx.Request):
        assert request.url.path == (
            "/internal/v1/admin/submissions-overview"
        )
        assert request.headers["x-actor-role"] == "super_admin"
        return httpx.Response(200, json=SUBMISSIONS_OVERVIEW)

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app, "GET", "/api/v1/admin/overview", headers=ADMIN_COOKIE
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    body = response.json()
    assert body["underReview"] == 3
    assert body["activeAccounts"] == 5
    assert body["suspendedAccounts"] == 1
    assert body["byKind"][0]["kind"] == "aid_location_rating"


@pytest.mark.anyio
async def test_admin_submissions_forward_actor_and_filters():
    seen = {}

    def disaster_handler(request: httpx.Request):
        seen["params"] = dict(request.url.params)
        seen["actor_id"] = request.headers.get("x-actor-account-id")
        seen["actor_role"] = request.headers.get("x-actor-role")
        seen["actor_display"] = request.headers.get("x-actor-display")
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json=SUBMISSION_PAGE)

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "GET",
        "/api/v1/admin/submissions?q=torre&kind="
        "unverified_building_report&status=under_review&limit=25",
        headers={
            **ADMIN_COOKIE,
            # Intento de suplantación: el gateway debe sobrescribir.
            "X-Actor-Account-Id": "11111111-1111-4111-8111-111111111111",
            "X-Actor-Role": "user",
        },
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert seen["params"]["q"] == "torre"
    assert seen["params"]["status"] == "under_review"
    assert seen["actor_id"] == ADMIN_ACCOUNT["id"]
    assert seen["actor_role"] == "super_admin"
    assert base64.b64decode(seen["actor_display"]).decode() == (
        "Admin CUSOL"
    )
    # La cookie no viaja a los servicios internos.
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_admin_mutations_validate_origin():
    upstream, identity = make_clients(
        lambda _: httpx.Response(200, json=SUBMISSION_PAGE)
    )
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "PATCH",
        "/api/v1/admin/submissions/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1",
        headers={
            **ADMIN_COOKIE,
            "Origin": "https://malicioso.example",
        },
        json={"expectedVersion": 1, "reason": "x" * 10, "changes": []},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 403


@pytest.mark.anyio
async def test_admin_conflict_passthrough_is_409():
    def disaster_handler(request: httpx.Request):
        import json as jsonlib

        return httpx.Response(
            409,
            headers={"content-type": "application/problem+json"},
            content=jsonlib.dumps(
                {
                    "type": "about:blank",
                    "title": "Conflicto de versión",
                    "status": 409,
                    "detail": "El expediente cambió.",
                }
            ),
        )

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/admin/submissions/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"
        "/decisions",
        headers=ADMIN_COOKIE,
        json={
            "expectedVersion": 1,
            "action": "accept",
            "reason": "Decisión sobre versión vieja.",
        },
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 409
    assert response.headers["content-type"] == (
        "application/problem+json"
    )


@pytest.mark.anyio
async def test_admin_rate_limit_is_per_account():
    upstream, identity = make_clients(
        lambda _: httpx.Response(200, json=SUBMISSION_PAGE)
    )
    app = create_app(
        gateway_settings(admin_rate_limit_per_minute=1),
        upstream,
        identity,
    )

    first = await request_gateway(
        app, "GET", "/api/v1/admin/submissions", headers=ADMIN_COOKIE
    )
    second = await request_gateway(
        app, "GET", "/api/v1/admin/submissions", headers=ADMIN_COOKIE
    )
    await upstream.aclose()
    await identity.aclose()

    assert first.status_code == 200
    assert second.status_code == 429


@pytest.mark.anyio
async def test_evidence_grant_has_separate_limit_and_forwards():
    def disaster_handler(request: httpx.Request):
        assert request.url.path.endswith("/access-grants")
        return httpx.Response(
            201,
            json={
                "url": "/api/v1/admin/evidence-access/token-opaco",
                "expiresAt": "2026-08-14T10:05:00Z",
                "auditEventId": "dddddddd-dddd-4ddd-8ddd-ddddddddddd2",
            },
        )

    upstream, identity = make_clients(disaster_handler)
    app = create_app(
        gateway_settings(admin_evidence_rate_limit_per_minute=1),
        upstream,
        identity,
    )
    path = (
        "/api/v1/admin/submissions/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"
        "/evidence/cccccccc-cccc-4ccc-8ccc-ccccccccccc1/access-grants"
    )

    first = await request_gateway(app, "POST", path, headers=ADMIN_COOKIE)
    second = await request_gateway(app, "POST", path, headers=ADMIN_COOKIE)
    await upstream.aclose()
    await identity.aclose()

    assert first.status_code == 201
    assert first.json()["url"].startswith(
        "/api/v1/admin/evidence-access/"
    )
    assert second.status_code == 429


@pytest.mark.anyio
async def test_evidence_access_streams_bytes_with_media_type():
    def disaster_handler(request: httpx.Request):
        assert request.url.path == (
            "/internal/v1/admin/evidence-access/token-opaco"
        )
        assert request.headers["x-actor-role"] == "super_admin"
        return httpx.Response(
            200,
            content=b"derivado-sin-exif",
            headers={"content-type": "image/jpeg"},
        )

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "GET",
        "/api/v1/admin/evidence-access/token-opaco",
        headers=ADMIN_COOKIE,
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.content == b"derivado-sin-exif"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store, private"


@pytest.mark.anyio
async def test_admin_accounts_flow_via_identity():
    def identity_admin_handler(request: httpx.Request):
        if request.url.path == "/internal/v1/auth/me":
            return identity_handler(request)
        if request.url.path.endswith("/sessions"):
            assert request.method == "DELETE"
            return httpx.Response(204)
        if request.method == "PATCH":
            return httpx.Response(200, json=ACCOUNT_DETAIL)
        raise AssertionError(
            f"ruta inesperada: {request.url.path}"
        )

    upstream, identity = make_clients(
        lambda _: httpx.Response(500), identity=identity_admin_handler
    )
    app = create_app(gateway_settings(), upstream, identity)

    updated = await request_gateway(
        app,
        "PATCH",
        f"/api/v1/admin/accounts/{ACCOUNT_DETAIL['id']}",
        headers=ADMIN_COOKIE,
        json={
            "expectedVersion": 1,
            "reason": "Promoción a moderación.",
            "assignedRole": "moderator",
        },
    )
    revoked = await request_gateway(
        app,
        "DELETE",
        f"/api/v1/admin/accounts/{ACCOUNT_DETAIL['id']}/sessions",
        headers=ADMIN_COOKIE,
        json={"reason": "Rotación preventiva de sesiones."},
    )
    await upstream.aclose()
    await identity.aclose()

    assert updated.status_code == 200
    assert updated.json()["assignedRole"] == "moderator"
    assert revoked.status_code == 204


# --- CHG-159: tema de la bandeja y borrado definitivo ---


@pytest.mark.anyio
async def test_admin_submissions_forward_theme_filter():
    seen = {}

    def disaster_handler(request: httpx.Request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=SUBMISSION_PAGE)

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "GET",
        "/api/v1/admin/submissions?theme=ayuda&limit=25",
        headers=ADMIN_COOKIE,
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert seen["params"]["theme"] == "ayuda"


@pytest.mark.anyio
async def test_admin_permanent_delete_forwards_and_returns_receipt():
    seen = {}
    receipt = {
        "id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1",
        "auditEventId": "dddddddd-dddd-4ddd-8ddd-ddddddddddd1",
        "deletedAt": "2026-08-18T21:00:00Z",
    }

    def disaster_handler(request: httpx.Request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = request.content.decode()
        return httpx.Response(200, json=receipt)

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "DELETE",
        "/api/v1/admin/submissions/"
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1/permanent",
        headers=ADMIN_COOKIE,
        json={
            "expectedVersion": 3,
            "reason": "Duplicada; borrado definitivo autorizado.",
        },
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json() == receipt
    assert seen["method"] == "DELETE"
    assert seen["path"].endswith(
        "/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1/permanent"
    )
    assert "expectedVersion" in seen["body"]


@pytest.mark.anyio
async def test_admin_permanent_delete_requires_super_admin():
    upstream, identity = make_clients(
        lambda _: httpx.Response(200, json={})
    )
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "DELETE",
        "/api/v1/admin/submissions/"
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1/permanent",
        headers=USER_COOKIE,
        json={"expectedVersion": 1, "reason": "Sin rol suficiente."},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 403
