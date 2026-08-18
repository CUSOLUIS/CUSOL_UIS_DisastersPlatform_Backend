"""CHG-153 — Gateway de logística (alta de puntos y denuncias).

El alta de centros de acopio / puntos de distribución es pública con
cuenta opcional (headers internos `x-actor-kind`/`x-account-id`) y exige
Idempotency-Key. Las denuncias viajan con `x-denouncer-key`: la cuenta
si hay sesión, o `fp:{sha256(fingerprint|ip)}` si es anónima (P1). La
validación de dependencia/ciudad vive en el disaster-service y sus 4xx
pasan tal cual.
"""

import hashlib

import httpx
import pytest

from app.config import Settings
from app.main import create_app

DISASTER_URL = "http://disaster-service:8001"
IDENTITY_URL = "http://identity-service:8002"
CREATE_PATH = "/api/v1/aid-locations"
LOCATION_ID = "88888888-8888-4888-8888-888888888801"
PUBLIC_REPORT_PATH = f"/api/v1/public/aid-locations/{LOCATION_ID}/reports"
ME_REPORT_PATH = f"/api/v1/me/aid-locations/{LOCATION_ID}/reports"
IDEMPOTENCY = {"Idempotency-Key": "clave-idempotente-0153"}

RECEIPT = {
    "id": LOCATION_ID,
    "kind": "receiver_center",
    "operationalStatus": "open",
    "createdAt": "2026-08-18T12:00:00Z",
}

REPORT_RECEIPT = {
    "locationId": LOCATION_ID,
    "reportsCount": 1,
    "underObservation": False,
}

USER_ACCOUNT = {
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
    "displayName": "Usuaria Normal",
    "email": "user@cusol.local",
    "assignedRole": "user",
    "status": "active",
    "sessionExpiresAt": "2026-08-18T20:00:00Z",
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


# --- alta de puntos ---------------------------------------------------


@pytest.mark.anyio
async def test_create_forwards_anonymous_and_hides_cookies():
    seen = {}

    async def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["idempotency"] = request.headers.get("idempotency-key")
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["account"] = request.headers.get("x-account-id")
        seen["cookie"] = request.headers.get("cookie")
        seen["body"] = await request.aread()
        return httpx.Response(201, json=RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        CREATE_PATH,
        headers={**IDEMPOTENCY, "Cookie": "otra_cookie=privada"},
        json={"kind": "receiver_center", "name": "Acopio Norte"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 201
    assert response.json() == RECEIPT
    assert seen["path"] == "/internal/v1/aid-locations"
    assert b"Acopio Norte" in seen["body"]
    assert seen["idempotency"] == IDEMPOTENCY["Idempotency-Key"]
    assert seen["actor"] == "anonymous"
    assert seen["account"] is None
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
        app, "POST", CREATE_PATH, content=b"{}", headers={}
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
        CREATE_PATH,
        headers=IDEMPOTENCY,
        cookies={"cusol_session": "token-user"},
        json={"kind": "distribution_point"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 201
    assert seen["actor"] == "authenticated"
    assert seen["account"] == USER_ACCOUNT["id"]


@pytest.mark.anyio
async def test_create_rejects_disallowed_origin():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(201, json=RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        CREATE_PATH,
        headers={**IDEMPOTENCY, "Origin": "https://malicioso.example"},
        json={"kind": "receiver_center"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 403
    assert calls == []


@pytest.mark.anyio
async def test_create_passes_through_upstream_validation_error():
    problem = {
        "type": "validation-error",
        "title": "Dependencia inválida",
        "status": 422,
        "detail": "Un punto de distribución exige un acopio padre.",
    }

    def handler(request: httpx.Request):
        return httpx.Response(422, json=problem)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        CREATE_PATH,
        headers=IDEMPOTENCY,
        json={"kind": "distribution_point"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 422
    assert response.json() == problem


@pytest.mark.anyio
async def test_create_upstream_failure_is_503():
    def handler(request: httpx.Request):
        raise httpx.ConnectError("caído", request=request)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        CREATE_PATH,
        headers=IDEMPOTENCY,
        json={"kind": "receiver_center"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 503


@pytest.mark.anyio
async def test_create_anonymous_rate_limit():
    def handler(request: httpx.Request):
        return httpx.Response(201, json=RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(
        gateway_settings(anonymous_contribution_rate_limit_per_minute=1),
        upstream,
        identity,
    )

    first = await request_gateway(
        app,
        "POST",
        CREATE_PATH,
        headers=IDEMPOTENCY,
        json={"kind": "receiver_center"},
    )
    second = await request_gateway(
        app,
        "POST",
        CREATE_PATH,
        headers=IDEMPOTENCY,
        json={"kind": "receiver_center"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert first.status_code == 201
    assert second.status_code == 429


# --- candidatos a centro asociado ------------------------------------


@pytest.mark.anyio
async def test_parent_candidates_forwards_query_and_mirrors_contract():
    seen = {}
    upstream_body = {
        "items": [
            {
                "id": LOCATION_ID,
                "name": "Receptor Metropolitano",
                "address": "Km 3 vía Girón",
                "municipality": "Bucaramanga",
                "department": "Santander",
                "operationalStatus": "open",
            }
        ],
        "total": 1,
    }

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=upstream_body)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "GET",
        "/api/v1/aid-locations/parent-candidates",
        params={"kind": "distribution_point", "municipality": "Bucaramanga"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json() == upstream_body
    assert seen["path"] == "/internal/v1/aid-locations/parent-candidates"
    assert seen["params"] == {
        "kind": "distribution_point",
        "municipality": "Bucaramanga",
    }


@pytest.mark.anyio
async def test_parent_candidates_passes_through_independent_kind_422():
    problem = {
        "type": "about:blank",
        "title": "Tipo sin dependencia",
        "status": 422,
        "detail": "Este tipo de punto no exige un centro asociado.",
    }

    def handler(request: httpx.Request):
        return httpx.Response(422, json=problem)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "GET",
        "/api/v1/aid-locations/parent-candidates",
        params={"kind": "receiver_center", "municipality": "Bucaramanga"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 422
    assert response.json() == problem


@pytest.mark.anyio
async def test_parent_candidates_shares_directory_search_rate_limit():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"items": [], "total": 0})

    upstream, identity = make_clients(handler)
    app = create_app(
        gateway_settings(directory_search_rate_limit_per_minute=1),
        upstream,
        identity,
    )

    params = {"kind": "collection_point", "municipality": "Bucaramanga"}
    first = await request_gateway(
        app, "GET", "/api/v1/aid-locations/parent-candidates", params=params
    )
    second = await request_gateway(
        app, "GET", "/api/v1/aid-locations/parent-candidates", params=params
    )
    await upstream.aclose()
    await identity.aclose()

    assert first.status_code == 200
    assert second.status_code == 429


# --- denuncias --------------------------------------------------------


@pytest.mark.anyio
async def test_anonymous_report_hashes_fingerprint_into_denouncer_key():
    seen = {}

    async def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["denouncer"] = request.headers.get("x-denouncer-key")
        seen["account"] = request.headers.get("x-account-id")
        await request.aread()
        return httpx.Response(202, json=REPORT_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    fingerprint = "visitante-777"
    response = await request_gateway(
        app,
        "POST",
        PUBLIC_REPORT_PATH,
        headers={**IDEMPOTENCY, "X-Visitor-Fingerprint": fingerprint},
        json={"reason": "closed_location"},
    )
    await upstream.aclose()
    await identity.aclose()

    expected = hashlib.sha256(fingerprint.encode()).hexdigest()[:32]
    assert response.status_code == 202
    assert response.json() == REPORT_RECEIPT
    assert seen["path"] == (
        f"/internal/v1/aid-locations/{LOCATION_ID}/reports"
    )
    assert seen["actor"] == "anonymous"
    assert seen["denouncer"] == f"fp:{expected}"
    assert seen["account"] is None


@pytest.mark.anyio
async def test_anonymous_report_without_fingerprint_uses_client_key():
    seen = {}

    async def handler(request: httpx.Request):
        seen["denouncer"] = request.headers.get("x-denouncer-key")
        await request.aread()
        return httpx.Response(202, json=REPORT_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        PUBLIC_REPORT_PATH,
        headers=IDEMPOTENCY,
        json={"reason": "closed_location"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 202
    assert seen["denouncer"].startswith("fp:")
    assert len(seen["denouncer"]) == len("fp:") + 32


@pytest.mark.anyio
async def test_anonymous_report_requires_idempotency_key():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(202, json=REPORT_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app, "POST", PUBLIC_REPORT_PATH, content=b"{}", headers={}
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 422
    assert calls == []


@pytest.mark.anyio
async def test_anonymous_report_rate_limit():
    def handler(request: httpx.Request):
        return httpx.Response(202, json=REPORT_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(
        gateway_settings(anonymous_contribution_rate_limit_per_minute=1),
        upstream,
        identity,
    )

    first = await request_gateway(
        app,
        "POST",
        PUBLIC_REPORT_PATH,
        headers=IDEMPOTENCY,
        json={"reason": "closed_location"},
    )
    second = await request_gateway(
        app,
        "POST",
        PUBLIC_REPORT_PATH,
        headers=IDEMPOTENCY,
        json={"reason": "closed_location"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert first.status_code == 202
    assert second.status_code == 429


@pytest.mark.anyio
async def test_authenticated_report_uses_account_denouncer_key():
    seen = {}

    async def handler(request: httpx.Request):
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["denouncer"] = request.headers.get("x-denouncer-key")
        seen["account"] = request.headers.get("x-account-id")
        await request.aread()
        return httpx.Response(202, json=REPORT_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        ME_REPORT_PATH,
        headers=IDEMPOTENCY,
        cookies={"cusol_session": "token-user"},
        json={"reason": "closed_location"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 202
    assert seen["actor"] == "authenticated"
    assert seen["denouncer"] == f"account:{USER_ACCOUNT['id']}"
    assert seen["account"] == USER_ACCOUNT["id"]


@pytest.mark.anyio
async def test_authenticated_report_requires_session():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(202, json=REPORT_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        ME_REPORT_PATH,
        headers=IDEMPOTENCY,
        json={"reason": "closed_location"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 401
    assert calls == []


@pytest.mark.anyio
async def test_report_upstream_failure_is_503():
    def handler(request: httpx.Request):
        raise httpx.ConnectError("caído", request=request)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        PUBLIC_REPORT_PATH,
        headers=IDEMPOTENCY,
        json={"reason": "closed_location"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 503


# --- CHG-161/162: transportes y «Mi casita partida» ---


TRANSPORT_RECEIPT = {
    "id": "99999999-9999-4999-8999-999999999901",
    "kind": "boat",
    "status": "registered",
    "originLocationId": "88888888-8888-4888-8888-888888888801",
    "destinationLocationId": "88888888-8888-4888-8888-888888888802",
    "createdAt": "2026-08-18T21:00:00Z",
}

TRANSPORT_BODY = {
    "kind": "boat",
    "originMunicipality": "Bucaramanga",
    "destinationMunicipality": "El Playón",
    "originLocationId": "88888888-8888-4888-8888-888888888801",
    "destinationLocationId": "88888888-8888-4888-8888-888888888802",
}


@pytest.mark.anyio
async def test_transport_requires_session():
    upstream, identity = make_clients(
        lambda _: httpx.Response(201, json=TRANSPORT_RECEIPT)
    )
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/transports",
        headers=IDEMPOTENCY,
        json=TRANSPORT_BODY,
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 401


@pytest.mark.anyio
async def test_transport_forwards_authenticated_account():
    seen = {}

    def disaster_handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["account"] = request.headers.get("x-account-id")
        seen["idempotency"] = request.headers.get("idempotency-key")
        return httpx.Response(201, json=TRANSPORT_RECEIPT)

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/transports",
        headers={**IDEMPOTENCY, "Cookie": "cusol_session=token-user"},
        json=TRANSPORT_BODY,
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 201
    assert response.json() == TRANSPORT_RECEIPT
    assert seen["path"] == "/internal/v1/humanitarian-transports"
    assert seen["actor"] == "authenticated"
    assert seen["account"] == USER_ACCOUNT["id"]
    assert seen["idempotency"] == IDEMPOTENCY["Idempotency-Key"]


@pytest.mark.anyio
async def test_damaged_home_allows_anonymous_and_forwards():
    seen = {}

    def disaster_handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["actor"] = request.headers.get("x-actor-kind")
        return httpx.Response(
            201,
            json={
                "id": "99999999-9999-4999-8999-999999999902",
                "createdAt": "2026-08-18T21:00:00Z",
            },
        )

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/damaged-homes",
        headers=IDEMPOTENCY,
        json={
            "description": "La casa perdió el techo y un muro.",
            "department": "Santander",
            "municipality": "Bucaramanga",
            "address": "Calle 10 # 4-20",
        },
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 201
    assert seen["path"] == "/internal/v1/damaged-home-reports"
    assert seen["actor"] == "anonymous"
