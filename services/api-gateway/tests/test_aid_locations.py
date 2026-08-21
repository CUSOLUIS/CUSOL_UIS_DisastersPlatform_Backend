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

# CHG-165: el recibo suma `disabled` (umbral de 20 → centro fuera del
# mapa hasta que el super_admin lo reactive).
REPORT_RECEIPT = {
    "locationId": LOCATION_ID,
    "reportsCount": 1,
    "underObservation": False,
    "disabled": False,
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
    # Con un tipo que sí admite alta anónima (CHG-161 F2 exige sesión
    # para acopio local y distribución): lo que se prueba aquí es que
    # los 4xx del servicio llegan intactos.
    problem = {
        "type": "validation-error",
        "title": "Dependencia inválida",
        "status": 422,
        "detail": "Un punto de recolección exige un acopio padre.",
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
        json={"kind": "collection_point"},
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


# CHG-161 (F2) — Refuerzo server-side del portón de sesión: el acopio
# local y el punto de distribución no admiten alta anónima; la puerta
# pública lo corta sin molestar al disaster-service.


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kind", ["collection_center", "distribution_point"]
)
async def test_create_requires_session_for_responsible_kinds(kind):
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
        headers=IDEMPOTENCY,
        json={"kind": kind, "name": "Acopio Norte"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 401
    body = response.json()
    assert body["title"] == "Sesión requerida"
    assert body["type"] == "session-required"
    assert calls == []


@pytest.mark.anyio
async def test_create_allows_responsible_kind_with_session():
    seen = {}

    async def handler(request: httpx.Request):
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["account"] = request.headers.get("x-account-id")
        seen["body"] = await request.aread()
        return httpx.Response(201, json=RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        CREATE_PATH,
        headers=IDEMPOTENCY,
        cookies={"cusol_session": "token-user"},
        json={"kind": "collection_center", "name": "Acopio Norte"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 201
    assert seen["actor"] == "authenticated"
    assert seen["account"] == USER_ACCOUNT["id"]
    assert b"collection_center" in seen["body"]


@pytest.mark.anyio
async def test_create_forwards_unreadable_body_for_upstream_validation():
    """Un cuerpo que no es JSON no se juzga aquí: lo valida el servicio."""
    seen = {}

    async def handler(request: httpx.Request):
        seen["body"] = await request.aread()
        return httpx.Response(
            422,
            json={
                "type": "validation-error",
                "title": "Datos inválidos",
                "status": 422,
                "detail": "El cuerpo no es JSON.",
            },
        )

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        CREATE_PATH,
        headers={**IDEMPOTENCY, "Content-Type": "application/json"},
        content=b"no-soy-json",
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 422
    assert seen["body"] == b"no-soy-json"


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
async def test_damaged_home_requires_an_account():
    """CHG-182: publicar la casita exige cuenta; sin ella, 401."""
    calls = []

    def disaster_handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(201, json={})

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/damaged-homes",
        headers=IDEMPOTENCY,
        json={"description": "La casa perdió el techo."},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 401
    assert calls == []


@pytest.mark.anyio
async def test_damaged_home_publishes_with_a_session():
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
        cookies={"cusol_session": "token-user"},
        json={
            "description": "La casa perdió el techo y un muro.",
            "department": "Santander",
            "municipality": "Bucaramanga",
            "address": "Calle 10 # 4-20",
            "householdSize": 4,
        },
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 201
    assert seen["path"] == "/internal/v1/damaged-home-reports"
    # CHG-182: el actor viaja siempre autenticado y con su cuenta.
    assert seen["actor"] == "authenticated"


# CHG-162 (F2) — El informe admite fotos del daño: multipart en
# streaming (el gateway no interpreta las partes) con la misma guardia
# de tamaño que el reporte de edificio.


@pytest.mark.anyio
async def test_damaged_home_forwards_multipart_photos_untouched():
    seen = {}

    async def disaster_handler(request: httpx.Request):
        seen["content_type"] = request.headers.get("content-type")
        seen["body"] = await request.aread()
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(
            201,
            json={
                "id": "99999999-9999-4999-8999-999999999903",
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
        cookies={"cusol_session": "token-user"},
        data={"payload": '{"description":"La casa perdió el techo."}'},
        files=[("photos", ("daño.jpg", b"binario-de-foto", "image/jpeg"))],
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 201
    assert seen["content_type"].startswith("multipart/form-data")
    assert b"binario-de-foto" in seen["body"]
    # La cookie de sesión se queda en el gateway: al servicio solo van
    # los encabezados de actor.
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_damaged_home_rejects_oversized_upload():
    calls = []

    def disaster_handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(201, json={})

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/damaged-homes",
        headers={
            **IDEMPOTENCY,
            "Content-Type": "multipart/form-data; boundary=x",
            "Content-Length": "99999999",
        },
        content=b"--x--",
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 413
    assert calls == []


# --- CHG-171: catálogo de ciudades, viaje GPS y feed del mapa --------

TRANSPORT_ID = "dddddddd-dddd-4ddd-8ddd-ddddddddddd7"

JOURNEY_RECEIPT = {
    "id": TRANSPORT_ID,
    "status": "in_transit",
    "departedAt": "2026-08-19T12:00:00Z",
    "arrivedAt": None,
    "lastPositionAt": None,
}


@pytest.mark.anyio
async def test_transport_cities_are_public():
    def disaster_handler(request: httpx.Request):
        assert request.url.path == "/internal/v1/transport-cities"
        return httpx.Response(
            200,
            json={
                "items": [
                    {"name": "Bucaramanga", "department": "Santander"}
                ],
                "total": 1,
            },
        )

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(app, "GET", "/api/v1/transports/cities")
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "Bucaramanga"


@pytest.mark.anyio
async def test_active_transports_feed_is_public():
    def disaster_handler(request: httpx.Request):
        assert request.url.path == (
            "/internal/v1/humanitarian-transports/active"
        )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": TRANSPORT_ID,
                        "kind": "mule",
                        "status": "in_transit",
                        "originName": "Acopio La Feria",
                        "originMunicipality": "Bucaramanga",
                        "destinationName": "Receptor Santander",
                        "destinationMunicipality": "El Playón",
                        "createdAt": "2026-08-19T11:00:00Z",
                        "trail": [],
                    }
                ],
                "total": 1,
            },
        )

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(app, "GET", "/api/v1/transports/active")
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json()["items"][0]["status"] == "in_transit"


@pytest.mark.anyio
async def test_journey_actions_require_session_and_forward_account():
    seen = {}

    def disaster_handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["account"] = request.headers.get("x-account-id")
        return httpx.Response(200, json=JOURNEY_RECEIPT)

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    anonymous = await request_gateway(
        app, "POST", f"/api/v1/me/transports/{TRANSPORT_ID}/start"
    )
    authenticated = await request_gateway(
        app,
        "POST",
        f"/api/v1/me/transports/{TRANSPORT_ID}/positions",
        headers={"Cookie": "cusol_session=token-user"},
        json={"latitude": 7.2, "longitude": -73.15},
    )
    await upstream.aclose()
    await identity.aclose()

    assert anonymous.status_code == 401
    assert authenticated.status_code == 200
    assert authenticated.json()["status"] == "in_transit"
    assert seen["path"] == (
        f"/internal/v1/me/humanitarian-transports/{TRANSPORT_ID}/positions"
    )
    assert seen["account"] == USER_ACCOUNT["id"]


@pytest.mark.anyio
async def test_journey_conflict_passes_through():
    def disaster_handler(request: httpx.Request):
        return httpx.Response(
            409,
            json={
                "type": "about:blank",
                "title": "Estado del viaje no compatible",
                "status": 409,
                "detail": "El viaje no admite esta acción en su estado "
                "actual.",
            },
        )

    upstream, identity = make_clients(disaster_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        f"/api/v1/me/transports/{TRANSPORT_ID}/arrive",
        headers={"Cookie": "cusol_session=token-user"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 409
    assert response.json()["title"] == "Estado del viaje no compatible"


# CHG-182 — «Mi casita destruida» en la puerta pública: feed del mapa,
# fotos públicas, comunidad y bandeja propia. Publicar exige cuenta;
# mirar, comentar y denunciar no.

HOME_ID = "cccccccc-cccc-4ccc-8ccc-ccccccccc182"
HOME_COMMENT_ID = "cccccccc-cccc-4ccc-8ccc-ccccccccc183"

HOME_PAGE = {
    "items": [
        {
            "id": HOME_ID,
            "publicCode": "CASA-2026-ABCD1234",
            "description": "El río se llevó la mitad de la casa.",
            "department": "Chocó",
            "municipality": "Quibdó",
            "address": "Barrio Niño Jesús, calle 3",
            "latitude": 5.69,
            "longitude": -76.66,
            "householdSize": 5,
            "donationChannel": "Nequi",
            "donationReference": "3001234567",
            "createdAt": "2026-08-20T10:00:00Z",
            "updatedAt": "2026-08-20T10:00:00Z",
            "photoUrls": [f"/api/v1/public/damaged-homes/{HOME_ID}/photos/1"],
            "commentRatingAverage": 4.5,
            "commentRatingCount": 2,
        }
    ],
    "total": 1,
    "generatedAt": "2026-08-20T10:05:00Z",
}


@pytest.mark.anyio
async def test_damaged_homes_feed_is_public():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json=HOME_PAGE)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "GET",
        "/api/v1/damaged-homes?limit=25&offset=0",
        headers={"Cookie": "otra=privada"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["householdSize"] == 5
    assert item["donationChannel"] == "Nequi"
    assert seen["path"] == "/internal/v1/damaged-homes"
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_damaged_home_photo_is_streamed_with_its_media_type():
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            content=b"imagen",
            headers={"content-type": "image/jpeg"},
        )

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app, "GET", f"/api/v1/public/damaged-homes/{HOME_ID}/photos/1"
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content == b"imagen"


@pytest.mark.anyio
async def test_damaged_home_comment_forwards_the_actor():
    seen = {}

    async def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["actor"] = request.headers.get("x-actor-kind")
        await request.aread()
        return httpx.Response(
            201,
            json={
                "id": HOME_COMMENT_ID,
                "authorDisplayName": None,
                "actorKind": "anonymous",
                "content": "Vamos mañana con colchones.",
                "rating": 5,
                "createdAt": "2026-08-20T11:00:00Z",
            },
        )

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        f"/api/v1/damaged-homes/{HOME_ID}/comments",
        headers={"Idempotency-Key": "clave-comentario-casita-0182"},
        json={"content": "Vamos mañana con colchones.", "rating": 5},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 201
    assert seen["actor"] == "anonymous"
    assert seen["path"] == f"/internal/v1/damaged-homes/{HOME_ID}/comments"


@pytest.mark.anyio
async def test_damaged_home_anonymous_complaint_hashes_the_fingerprint():
    seen = {}

    async def handler(request: httpx.Request):
        seen["denouncer"] = request.headers.get("x-denouncer-key")
        await request.aread()
        return httpx.Response(
            202,
            json={
                "damagedHomeId": HOME_ID,
                "reportsCount": 1,
                "underObservation": False,
                "disabled": False,
            },
        )

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        f"/api/v1/public/damaged-homes/{HOME_ID}/reports",
        headers={
            "Idempotency-Key": "clave-denuncia-casita-0182",
            "X-Visitor-Fingerprint": "huella-del-visitante",
        },
        json={"category": "informacion_falsa", "reason": "No es esa casa."},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 202
    assert seen["denouncer"].startswith("fp:")
    assert "huella-del-visitante" not in seen["denouncer"]


@pytest.mark.anyio
async def test_my_damaged_homes_requires_a_session():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(
            200, json={"items": [], "total": 0, "unreadTotal": 0}
        )

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    sin_sesion = await request_gateway(app, "GET", "/api/v1/me/damaged-homes")
    con_sesion = await request_gateway(
        app,
        "GET",
        "/api/v1/me/damaged-homes",
        cookies={"cusol_session": "token-user"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert sin_sesion.status_code == 401
    assert con_sesion.status_code == 200
    assert calls == ["/internal/v1/me/damaged-homes"]


@pytest.mark.anyio
async def test_marking_comments_seen_travels_with_the_account():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["account"] = request.headers.get("x-account-id")
        return httpx.Response(204)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        f"/api/v1/me/damaged-homes/{HOME_ID}/comments-seen",
        cookies={"cusol_session": "token-user"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 204
    assert seen["account"] == USER_ACCOUNT["id"]
    assert seen["path"] == (
        f"/internal/v1/me/damaged-homes/{HOME_ID}/comments-seen"
    )



# CHG-202 — La dueña elimina su casita: mutación con sesión, así que
# pasa por la comprobación de origen y la cuenta sale de la cookie.
@pytest.mark.anyio
async def test_deleting_own_damaged_home_carries_the_session_account():
    seen = {}

    def handler(request: httpx.Request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["account"] = request.headers.get("x-account-id")
        return httpx.Response(204)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    sin_sesion = await request_gateway(
        app, "DELETE", f"/api/v1/me/damaged-homes/{HOME_ID}"
    )
    con_sesion = await request_gateway(
        app,
        "DELETE",
        f"/api/v1/me/damaged-homes/{HOME_ID}",
        cookies={"cusol_session": "token-user"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert sin_sesion.status_code == 401
    assert con_sesion.status_code == 204
    assert seen["method"] == "DELETE"
    assert seen["path"] == f"/internal/v1/me/damaged-homes/{HOME_ID}"
    assert seen["account"] is not None


@pytest.mark.anyio
async def test_deleting_own_damaged_home_rejects_a_foreign_origin():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(204)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "DELETE",
        f"/api/v1/me/damaged-homes/{HOME_ID}",
        headers={"Origin": "https://malicioso.example"},
        cookies={"cusol_session": "token-user"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 403
    assert calls == []
