"""CHG-034 — Gateway del directorio humanitario y aportes.

Cubre reenvío de búsqueda con filtros, límites de tasa separados,
rutas públicas anónimas, rutas `/me` con cookie + CSRF y el reenvío
del actor decidido por el gateway (nunca por el cliente).
"""

import httpx
import pytest

from app.config import Settings
from app.main import create_app


DISASTER_URL = "http://disaster-service:8001"
IDENTITY_URL = "http://identity-service:8002"

SEARCH_RESPONSE = {
    "items": [
        {
            "kind": "collection_center",
            "id": "44444444-4444-4444-8444-444444444403",
            "name": "Centro de acopio — Coliseo Bicentenario",
            "locationLabel": "Coliseo Bicentenario, Bucaramanga",
            "municipality": "Bucaramanga",
            "department": "Santander",
            "verificationStatus": "verified",
            "availabilityStatus": "active",
            "openNow": True,
            "acceptedSupplies": ["water", "food"],
            "averageRating": 4.5,
            "ratingsCount": 2,
            "source": {
                "name": "UNGRD",
                "sourceType": "official",
                "url": None,
            },
            "updatedAt": "2026-08-13T09:00:00Z",
            "dataClassification": "demonstrative",
        }
    ],
    "total": 1,
    "limit": 20,
    "offset": 0,
    "query": "acopio",
    "kind": "collection_center",
    "generatedAt": "2026-08-14T10:00:00Z",
}

RECEIPT_ANONYMOUS = {
    "id": "99999999-9999-4999-8999-999999999901",
    "status": "under_review",
    "actorKind": "anonymous",
    "receivedAt": "2026-08-14T10:05:00Z",
}

RECEIPT_AUTHENTICATED = {
    "id": "99999999-9999-4999-8999-999999999902",
    "status": "under_review",
    "actorKind": "authenticated",
    "receivedAt": "2026-08-14T10:06:00Z",
}

ACCOUNT = {
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
    "displayName": "Cuenta Demo",
    "email": "demo@cusol.local",
    "assignedRole": "user",
    "status": "active",
    "sessionExpiresAt": "2026-08-15T10:00:00Z",
}

PERSON_ID = "55555555-5555-4555-8555-555555555501"
LOCATION_ID = "44444444-4444-4444-8444-444444444403"
IDEMPOTENCY = {"Idempotency-Key": "clave-idempotente-0034"}


def gateway_settings(**overrides) -> Settings:
    values = {
        "disaster_service_url": DISASTER_URL,
        "identity_service_url": IDENTITY_URL,
        "upstream_timeout_seconds": 1,
    }
    values.update(overrides)
    return Settings(**values)


def upstream_client(handler):
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=DISASTER_URL
    )


def identity_client(handler):
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=IDENTITY_URL
    )


def identity_with_session(handler=None):
    def default_handler(request: httpx.Request):
        assert request.url.path == "/internal/v1/auth/me"
        if request.headers.get("x-session-token") == "token-valido":
            return httpx.Response(200, json=ACCOUNT)
        return httpx.Response(
            401,
            json={
                "type": "session-required",
                "title": "Sesión requerida",
                "status": 401,
                "detail": "La sesión está ausente, vencida o revocada.",
            },
        )

    return identity_client(handler or default_handler)


async def request_gateway(app, method, path, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)


# --- Búsqueda ---


@pytest.mark.anyio
async def test_directory_search_forwards_all_filters():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(dict(request.url.params))
        assert request.url.path == (
            "/internal/v1/humanitarian-directory/search"
        )
        return httpx.Response(200, json=SEARCH_RESPONSE)

    upstream = upstream_client(handler)
    app = create_app(gateway_settings(), upstream)

    response = await request_gateway(
        app,
        "GET",
        "/api/v1/humanitarian-directory/search"
        "?kind=collection_center&q=acopio&verificationStatus=verified"
        "&availabilityStatus=active&openNow=true&department=Santander"
        "&minRating=4&limit=10&offset=5",
    )
    await upstream.aclose()

    assert response.status_code == 200
    assert seen == {
        "kind": "collection_center",
        "q": "acopio",
        "limit": "10",
        "offset": "5",
        "verificationStatus": "verified",
        "availabilityStatus": "active",
        "openNow": "true",
        "department": "Santander",
        "minRating": "4.0",
    }
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["averageRating"] == 4.5


@pytest.mark.anyio
async def test_directory_search_passes_through_upstream_422():
    def handler(_request: httpx.Request):
        return httpx.Response(
            422,
            json={
                "type": "about:blank",
                "title": "Filtros inválidos",
                "status": 422,
                "detail": "Los filtros de lugares no aplican a personas.",
            },
        )

    upstream = upstream_client(handler)
    app = create_app(gateway_settings(), upstream)

    response = await request_gateway(
        app,
        "GET",
        "/api/v1/humanitarian-directory/search"
        "?kind=missing_person&q=Camila&minRating=3",
    )
    await upstream.aclose()

    assert response.status_code == 422
    assert response.json()["title"] == "Filtros inválidos"


@pytest.mark.anyio
async def test_directory_search_has_its_own_rate_limit():
    upstream = upstream_client(
        lambda _: httpx.Response(200, json=SEARCH_RESPONSE)
    )
    app = create_app(
        gateway_settings(directory_search_rate_limit_per_minute=1),
        upstream,
    )

    first = await request_gateway(
        app,
        "GET",
        "/api/v1/humanitarian-directory/search"
        "?kind=collection_center&q=acopio",
    )
    second = await request_gateway(
        app,
        "GET",
        "/api/v1/humanitarian-directory/search"
        "?kind=collection_center&q=acopio",
    )
    await upstream.aclose()

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["content-type"] == "application/problem+json"


# --- Aportes anónimos ---


@pytest.mark.anyio
async def test_public_status_report_forwards_anonymous_actor():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["account"] = request.headers.get("x-account-id")
        seen["idempotency"] = request.headers.get("idempotency-key")
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(202, json=RECEIPT_ANONYMOUS)

    upstream = upstream_client(handler)
    app = create_app(gateway_settings(), upstream)

    response = await request_gateway(
        app,
        "POST",
        f"/api/v1/public/missing-persons/{PERSON_ID}/status-reports",
        headers=IDEMPOTENCY,
        files={"payload": (None, "{}", "application/json")},
    )
    await upstream.aclose()

    assert response.status_code == 202
    assert response.json()["actorKind"] == "anonymous"
    assert seen["path"] == (
        f"/internal/v1/missing-persons/{PERSON_ID}/status-reports"
    )
    assert seen["actor"] == "anonymous"
    assert seen["account"] is None
    assert seen["idempotency"] == IDEMPOTENCY["Idempotency-Key"]
    # La ruta pública nunca reenvía cookies al upstream.
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_public_routes_require_idempotency_key():
    upstream = upstream_client(
        lambda _: httpx.Response(202, json=RECEIPT_ANONYMOUS)
    )
    app = create_app(gateway_settings(), upstream)

    report = await request_gateway(
        app,
        "POST",
        f"/api/v1/public/missing-persons/{PERSON_ID}/status-reports",
        files={"payload": (None, "{}", "application/json")},
    )
    rating = await request_gateway(
        app,
        "POST",
        f"/api/v1/public/aid-locations/{LOCATION_ID}/ratings",
        headers={"Idempotency-Key": "corta"},
        files={"payload": (None, "{}", "application/json")},
    )
    await upstream.aclose()

    assert report.status_code == 422
    assert rating.status_code == 422


@pytest.mark.anyio
async def test_public_rating_passes_through_upstream_404():
    def handler(_request: httpx.Request):
        return httpx.Response(
            404,
            json={
                "type": "about:blank",
                "title": "Lugar no disponible",
                "status": 404,
                "detail": "El lugar no existe o no es publicable.",
            },
        )

    upstream = upstream_client(handler)
    app = create_app(gateway_settings(), upstream)

    response = await request_gateway(
        app,
        "POST",
        f"/api/v1/public/aid-locations/{LOCATION_ID}/ratings",
        headers=IDEMPOTENCY,
        files={"payload": (None, "{}", "application/json")},
    )
    await upstream.aclose()

    assert response.status_code == 404


@pytest.mark.anyio
async def test_anonymous_contributions_share_a_separate_rate_limit():
    upstream = upstream_client(
        lambda _: httpx.Response(202, json=RECEIPT_ANONYMOUS)
    )
    app = create_app(
        gateway_settings(
            anonymous_contribution_rate_limit_per_minute=1
        ),
        upstream,
    )

    first = await request_gateway(
        app,
        "POST",
        f"/api/v1/public/aid-locations/{LOCATION_ID}/ratings",
        headers=IDEMPOTENCY,
        files={"payload": (None, "{}", "application/json")},
    )
    second = await request_gateway(
        app,
        "POST",
        f"/api/v1/public/missing-persons/{PERSON_ID}/status-reports",
        headers=IDEMPOTENCY,
        files={"payload": (None, "{}", "application/json")},
    )
    await upstream.aclose()

    assert first.status_code == 202
    assert second.status_code == 429


# --- Aportes autenticados (/me) ---


@pytest.mark.anyio
async def test_me_status_report_requires_session_cookie():
    upstream_calls = []

    def upstream_handler(request: httpx.Request):
        upstream_calls.append(request.url.path)
        return httpx.Response(202, json=RECEIPT_AUTHENTICATED)

    upstream = upstream_client(upstream_handler)
    identity = identity_with_session()
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        f"/api/v1/me/missing-persons/{PERSON_ID}/status-reports",
        headers=IDEMPOTENCY,
        files={"payload": (None, "{}", "application/json")},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 401
    assert response.headers["content-type"] == (
        "application/problem+json"
    )
    # Sesión ausente jamás degrada al flujo anónimo.
    assert upstream_calls == []


@pytest.mark.anyio
async def test_me_status_report_with_expired_session_is_401():
    upstream_calls = []

    def upstream_handler(request: httpx.Request):
        upstream_calls.append(request.url.path)
        return httpx.Response(202, json=RECEIPT_AUTHENTICATED)

    upstream = upstream_client(upstream_handler)
    identity = identity_with_session()
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        f"/api/v1/me/missing-persons/{PERSON_ID}/status-reports",
        headers=IDEMPOTENCY,
        cookies={"cusol_session": "token-vencido"},
        files={"payload": (None, "{}", "application/json")},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 401
    assert upstream_calls == []


@pytest.mark.anyio
async def test_me_rating_forwards_account_actor():
    seen = {}

    def upstream_handler(request: httpx.Request):
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["account"] = request.headers.get("x-account-id")
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(202, json=RECEIPT_AUTHENTICATED)

    upstream = upstream_client(upstream_handler)
    identity = identity_with_session()
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        f"/api/v1/me/aid-locations/{LOCATION_ID}/ratings",
        headers=IDEMPOTENCY,
        cookies={"cusol_session": "token-valido"},
        files={"payload": (None, "{}", "application/json")},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 202
    assert response.json()["actorKind"] == "authenticated"
    assert seen["actor"] == "authenticated"
    assert seen["account"] == ACCOUNT["id"]
    # La cookie no viaja al servicio interno.
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_me_routes_reject_disallowed_origin():
    upstream = upstream_client(
        lambda _: httpx.Response(202, json=RECEIPT_AUTHENTICATED)
    )
    identity = identity_with_session()
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        f"/api/v1/me/aid-locations/{LOCATION_ID}/ratings",
        headers={**IDEMPOTENCY, "Origin": "https://malicioso.example"},
        cookies={"cusol_session": "token-valido"},
        files={"payload": (None, "{}", "application/json")},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 403


@pytest.mark.anyio
async def test_account_contributions_have_their_own_rate_limit():
    upstream = upstream_client(
        lambda _: httpx.Response(202, json=RECEIPT_AUTHENTICATED)
    )
    identity = identity_with_session()
    app = create_app(
        gateway_settings(account_contribution_rate_limit_per_minute=1),
        upstream,
        identity,
    )

    first = await request_gateway(
        app,
        "POST",
        f"/api/v1/me/aid-locations/{LOCATION_ID}/ratings",
        headers=IDEMPOTENCY,
        cookies={"cusol_session": "token-valido"},
        files={"payload": (None, "{}", "application/json")},
    )
    second = await request_gateway(
        app,
        "POST",
        f"/api/v1/me/missing-persons/{PERSON_ID}/status-reports",
        headers=IDEMPOTENCY,
        cookies={"cusol_session": "token-valido"},
        files={"payload": (None, "{}", "application/json")},
    )
    await upstream.aclose()
    await identity.aclose()

    assert first.status_code == 202
    assert second.status_code == 429


@pytest.mark.anyio
async def test_contribution_upstream_failure_is_503():
    def handler(_request: httpx.Request):
        raise httpx.ConnectError("sin conexión")

    upstream = upstream_client(handler)
    app = create_app(gateway_settings(), upstream)

    response = await request_gateway(
        app,
        "POST",
        f"/api/v1/public/aid-locations/{LOCATION_ID}/ratings",
        headers=IDEMPOTENCY,
        files={"payload": (None, "{}", "application/json")},
    )
    await upstream.aclose()

    assert response.status_code == 503
    assert response.json()["title"] == (
        "Servicio de valoraciones no disponible"
    )
