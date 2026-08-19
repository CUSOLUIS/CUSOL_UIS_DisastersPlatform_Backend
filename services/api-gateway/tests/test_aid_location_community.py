"""CHG-165 — Gateway de comentarios y consola de verificación de
Centros de Acopio Local.

Los comentarios se leen públicos y se publican con cuenta opcional (el
gateway resuelve `x-actor-kind`, `x-account-id` y el nombre visible en
`x-actor-display`; nunca correo ni datos privados). La bandeja de
verificaciones, la decisión y la reactivación exigen super_admin y
viajan con los encabezados internos de actor.
"""

import base64

import httpx
import pytest

from app.config import Settings
from app.main import create_app

DISASTER_URL = "http://disaster-service:8001"
IDENTITY_URL = "http://identity-service:8002"
LOCATION_ID = "88888888-8888-4888-8888-888888888801"
COMMENTS_PATH = f"/api/v1/aid-locations/{LOCATION_ID}/comments"
VERIFICATIONS_PATH = "/api/v1/admin/aid-locations/verifications"
VERIFICATION_PATH = (
    f"/api/v1/admin/aid-locations/{LOCATION_ID}/verification"
)
REACTIVATE_PATH = f"/api/v1/admin/aid-locations/{LOCATION_ID}/reactivate"
IDEMPOTENCY = {"Idempotency-Key": "clave-idempotente-0165"}

ADMIN_ACCOUNT = {
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
    "displayName": "Admin CUSOL",
    "email": "admin@cusol.local",
    "assignedRole": "super_admin",
    "status": "active",
    "sessionExpiresAt": "2026-08-19T20:00:00Z",
}
USER_ACCOUNT = {
    **ADMIN_ACCOUNT,
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
    "displayName": "Usuaria Normal",
    "email": "user@cusol.local",
    "assignedRole": "user",
}

ADMIN_COOKIE = {"Cookie": "cusol_session=token-admin"}
USER_COOKIE = {"Cookie": "cusol_session=token-user"}

COMMENT = {
    "id": "77777777-7777-4777-8777-777777777701",
    "authorDisplayName": None,
    "actorKind": "anonymous",
    "content": "El punto continúa abierto y recibiendo.",
    # CHG-166: calificación 1-5 del comentario.
    "rating": 5,
    "createdAt": "2026-08-19T12:00:00Z",
}
COMMENTS_PAGE = {
    "items": [COMMENT],
    "total": 1,
    # CHG-166: promedio server-side.
    "ratingAverage": 5.0,
    "ratingCount": 1,
}

CENTER_SUMMARY = {
    "id": LOCATION_ID,
    "kind": "collection_center",
    "name": "Acopio La Feria",
    "locationLabel": "Calle 10 # 5-51",
    "municipality": "Bucaramanga",
    "department": "Santander",
    "latitude": 7.1,
    "longitude": -73.1,
    "description": "Recibe alimentos no perecederos.",
    "schedule": None,
    "contact": None,
    "createdAt": "2026-08-18T12:00:00Z",
    "createdByAccountId": None,
    "verificationStatus": "unverified",
    "operationalStatus": "open",
    "disabledAt": None,
    "verifiedAt": None,
    "activeReportsCount": 0,
}

ACTION_RECEIPT = {
    "id": LOCATION_ID,
    "verificationStatus": "verified",
    "operationalStatus": "open",
    "disabledAt": None,
    "activeReportsCount": 0,
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


# --- comentarios ------------------------------------------------------


@pytest.mark.anyio
async def test_comments_listing_is_public():
    seen = {}

    async def handler(request: httpx.Request):
        seen["path"] = request.url.path
        return httpx.Response(200, json=COMMENTS_PAGE)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(app, "GET", COMMENTS_PATH)
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json() == COMMENTS_PAGE
    assert seen["path"] == (
        f"/internal/v1/aid-locations/{LOCATION_ID}/comments"
    )


@pytest.mark.anyio
async def test_anonymous_comment_travels_without_display_name():
    seen = {}

    async def handler(request: httpx.Request):
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["account"] = request.headers.get("x-account-id")
        seen["display"] = request.headers.get("x-actor-display")
        await request.aread()
        return httpx.Response(201, json=COMMENT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        COMMENTS_PATH,
        headers=IDEMPOTENCY,
        json={"content": "El punto continúa abierto y recibiendo."},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 201
    assert response.json() == COMMENT
    assert seen["actor"] == "anonymous"
    assert seen["account"] is None
    assert seen["display"] is None


@pytest.mark.anyio
async def test_authenticated_comment_carries_public_display_name():
    seen = {}

    async def handler(request: httpx.Request):
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["account"] = request.headers.get("x-account-id")
        seen["display"] = request.headers.get("x-actor-display")
        await request.aread()
        return httpx.Response(
            201,
            json={
                **COMMENT,
                "authorDisplayName": "Usuaria Normal",
                "actorKind": "authenticated",
            },
        )

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        COMMENTS_PATH,
        headers={**IDEMPOTENCY, **USER_COOKIE},
        json={"content": "Acabo de entregar varias cajas en este punto."},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 201
    assert seen["actor"] == "authenticated"
    assert seen["account"] == USER_ACCOUNT["id"]
    assert base64.b64decode(seen["display"]).decode() == "Usuaria Normal"


@pytest.mark.anyio
async def test_comment_requires_idempotency_key():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(201, json=COMMENT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        COMMENTS_PATH,
        json={"content": "Comentario sin llave idempotente."},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 422
    assert calls == []


# --- consola super_admin ---------------------------------------------


@pytest.mark.anyio
async def test_admin_routes_require_session_and_role():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(200, json=ACTION_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    anonymous = await request_gateway(app, "GET", VERIFICATIONS_PATH)
    as_user = await request_gateway(
        app, "GET", VERIFICATIONS_PATH, headers=USER_COOKIE
    )
    user_reactivate = await request_gateway(
        app, "POST", REACTIVATE_PATH, headers=USER_COOKIE
    )
    await upstream.aclose()
    await identity.aclose()

    assert anonymous.status_code == 401
    assert as_user.status_code == 403
    # §28: la barrera es backend, no un botón oculto.
    assert user_reactivate.status_code == 403
    assert calls == []


@pytest.mark.anyio
async def test_admin_verifications_listing_forwards_actor():
    seen = {}

    async def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["role"] = request.headers.get("x-actor-role")
        return httpx.Response(
            200, json={"pending": [CENTER_SUMMARY], "disabled": []}
        )

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app, "GET", VERIFICATIONS_PATH, headers=ADMIN_COOKIE
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json()["pending"][0]["name"] == "Acopio La Feria"
    assert seen["path"] == "/internal/v1/admin/aid-locations/verifications"
    assert seen["role"] == "super_admin"


@pytest.mark.anyio
async def test_admin_verification_decision_forwards_body():
    seen = {}

    async def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["body"] = (await request.aread()).decode()
        return httpx.Response(200, json=ACTION_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        VERIFICATION_PATH,
        headers=ADMIN_COOKIE,
        json={"decision": "approve"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json() == ACTION_RECEIPT
    assert seen["path"] == (
        f"/internal/v1/admin/aid-locations/{LOCATION_ID}/verification"
    )
    assert "approve" in seen["body"]


@pytest.mark.anyio
async def test_admin_reactivate_passes_conflict_through():
    async def handler(request: httpx.Request):
        return httpx.Response(
            409,
            json={
                "type": "about:blank",
                "title": "Centro no deshabilitado",
                "status": 409,
                "detail": "Solo puede reactivarse un centro "
                "deshabilitado por denuncias.",
            },
        )

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app, "POST", REACTIVATE_PATH, headers=ADMIN_COOKIE
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 409
    assert response.json()["title"] == "Centro no deshabilitado"
