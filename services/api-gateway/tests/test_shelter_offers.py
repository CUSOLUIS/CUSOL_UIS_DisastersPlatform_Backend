"""CHG-205 — Gateway de «Ofrecer alojamiento temporal».

Mismo pacto que la gemela de comida (CHG-163): alta y listado
públicos, cuenta opcional en headers internos, límites de contribución
compartidos y cupo de lectura propio. Nunca se reenvían cookies al
servicio interno.
"""

import httpx
import pytest

from app.config import Settings
from app.main import create_app

DISASTER_URL = "http://disaster-service:8001"
IDENTITY_URL = "http://identity-service:8002"
PATH = "/api/v1/shelter-offers"
IDEMPOTENCY = {"Idempotency-Key": "clave-idempotente-0205"}
OFFER_ID = "88888888-8888-4888-8888-888888888805"

RECEIPT = {
    "id": OFFER_ID,
    "publicCode": "AL-2026-CCCC3333",
    "status": "active",
    "receivedAt": "2026-08-19T12:00:00Z",
    "expiresAt": "2026-08-19T18:00:00Z",
}

PAGE = {
    "items": [
        {
            "id": OFFER_ID,
            "description": "Tengo dos habitaciones libres con camas y baño.",
            "address": "Calle 10 #5-20, Bucaramanga",
            "latitude": 7.12,
            "longitude": -73.12,
            "notificationRadiusKm": 5,
            "spacesAvailable": 4,
            "sharedSpace": True,
            "acceptsPets": False,
            "accessibilityNotes": None,
            "createdAt": "2026-08-19T12:00:00Z",
            "expiresAt": "2026-08-19T18:00:00Z",
        }
    ],
    "total": 1,
    "generatedAt": "2026-08-19T12:05:00Z",
}

USER_ACCOUNT = {
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa63",
    "displayName": "Usuaria Normal",
    "email": "user@cusol.local",
    "assignedRole": "user",
    "status": "active",
    "sessionExpiresAt": "2026-08-19T20:00:00Z",
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
        if request.headers.get("x-session-token") == "token-user":
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


@pytest.mark.anyio
async def test_create_forwards_json_and_hides_cookies():
    seen = {}

    async def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["idempotency"] = request.headers.get("idempotency-key")
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["cookie"] = request.headers.get("cookie")
        seen["body"] = await request.aread()
        return httpx.Response(201, json=RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        PATH,
        headers={**IDEMPOTENCY, "Cookie": "cusol_session=privada"},
        json={"cualquier": "json"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 201
    assert response.json() == RECEIPT
    assert seen["path"] == "/internal/v1/shelter-offers"
    assert b'"cualquier"' in seen["body"]
    assert seen["idempotency"] == IDEMPOTENCY["Idempotency-Key"]
    assert seen["actor"] == "anonymous"
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_create_requires_idempotency_key():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(201, json=RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app, "POST", PATH, content=b"", headers={}
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 422
    assert calls == []


@pytest.mark.anyio
async def test_create_forwards_optional_account():
    seen = {}

    async def handler(request: httpx.Request):
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["account"] = request.headers.get("x-account-id")
        await request.aread()
        return httpx.Response(201, json=RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        PATH,
        headers=IDEMPOTENCY,
        cookies={"cusol_session": "token-user"},
        content=b"x",
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 201
    assert seen["actor"] == "authenticated"
    assert seen["account"] == USER_ACCOUNT["id"]


@pytest.mark.anyio
async def test_create_shares_contribution_rate_limit():
    upstream, identity = make_clients(
        lambda _: httpx.Response(201, json=RECEIPT)
    )
    app = create_app(
        gateway_settings(anonymous_contribution_rate_limit_per_minute=1),
        upstream,
        identity,
    )

    first = await request_gateway(
        app, "POST", PATH, headers=IDEMPOTENCY, content=b"x"
    )
    second = await request_gateway(
        app, "POST", PATH, headers=IDEMPOTENCY, content=b"x"
    )
    await upstream.aclose()
    await identity.aclose()

    assert first.status_code == 201
    assert second.status_code == 429


@pytest.mark.anyio
async def test_list_forwards_pagination():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json=PAGE)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "GET",
        PATH,
        params={"limit": 10, "offset": 20},
        cookies={"cusol_session": "privada"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["notificationRadiusKm"] == 5
    assert seen["path"] == "/internal/v1/shelter-offers"
    assert seen["params"] == {"limit": "10", "offset": "20"}
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_list_rejects_unknown_page_size():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(200, json=PAGE)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app, "GET", PATH, params={"limit": 7}
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 422
    assert calls == []


@pytest.mark.anyio
async def test_list_has_its_own_read_limit():
    upstream, identity = make_clients(
        lambda _: httpx.Response(200, json=PAGE)
    )
    app = create_app(
        gateway_settings(shelter_offer_read_rate_limit_per_minute=1),
        upstream,
        identity,
    )

    first = await request_gateway(app, "GET", PATH)
    second = await request_gateway(app, "GET", PATH)
    await upstream.aclose()
    await identity.aclose()

    assert first.status_code == 200
    assert second.status_code == 429
