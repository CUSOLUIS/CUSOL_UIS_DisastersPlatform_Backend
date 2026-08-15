"""CHG-044 — Gateway de ofertas comunitarias (/api/v1/me/aid-offers).

Cubre 401 sin cookie, resolución de cuenta con encabezados internos no
falsificables, Origin/CSRF, Idempotency-Key, límites por cuenta,
reenvío con Problem Details y el directorio ampliado.
"""

import json

import httpx
import pytest

from app.config import Settings
from app.main import create_app


DISASTER_URL = "http://disaster-service:8001"
IDENTITY_URL = "http://identity-service:8002"
ACCOUNT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"

ACCOUNT = {
    "id": ACCOUNT_ID,
    "displayName": "Cuenta Demo",
    "email": "demo@cusol.local",
    "assignedRole": "user",
    "status": "active",
    "sessionExpiresAt": "2026-08-16T10:00:00Z",
}

RECEIPT = {
    "id": "10000000-0000-4000-8000-000000000001",
    "trackingCode": "AID-2026-ABCD1234",
    "kind": "community_meal",
    "moderationStatus": "under_review",
    "availabilityStatus": "scheduled",
    "receivedAt": "2026-08-15T12:00:00Z",
    "version": 1,
}

OWNER_SUMMARY = {
    "id": "10000000-0000-4000-8000-000000000001",
    "trackingCode": "AID-2026-ABCD1234",
    "kind": "community_meal",
    "title": "Almuerzos comunitarios",
    "moderationStatus": "under_review",
    "availabilityStatus": "paused",
    "availableUnits": 40,
    "capacityUnit": "servings",
    "availableFrom": "2030-08-16T16:00:00Z",
    "availableUntil": "2030-08-16T20:00:00Z",
    "receivedAt": "2026-08-15T12:00:00Z",
    "updatedAt": "2026-08-15T12:30:00Z",
    "version": 2,
}

OWNER_PAGE = {
    "items": [dict(OWNER_SUMMARY, availabilityStatus="scheduled")],
    "total": 1,
    "limit": 10,
    "offset": 0,
    "generatedAt": "2026-08-15T12:00:00Z",
}

OFFER_CARD = {
    "kind": "community_meal",
    "id": "20000000-0000-4000-8000-000000000002",
    "publicOfferCode": "OFR-2026-PUB1",
    "title": "Almuerzos comunitarios",
    "description": "Raciones preparadas para personas afectadas.",
    "areaReference": "Sector norte",
    "municipality": "Bucaramanga",
    "department": "Santander",
    "availabilityStatus": "active",
    "availableFrom": "2030-08-16T16:00:00Z",
    "availableUntil": "2030-08-16T20:00:00Z",
    "servingsAvailable": 40,
    "distributionMode": "pickup",
    "mealDescription": "Arroz, legumbres y proteína",
    "allergenInformation": None,
    "verificationStatus": "verified",
    "source": {
        "name": "Oferta comunitaria — plataforma CUSOL",
        "sourceType": "citizen",
        "url": None,
    },
    "updatedAt": "2026-08-15T12:00:00Z",
    "dataClassification": "demonstrative",
}

MEAL_PAYLOAD = {
    "kind": "community_meal",
    "title": "Almuerzos comunitarios",
    "description": (
        "Raciones preparadas para personas afectadas por la emergencia."
    ),
    "department": "Santander",
    "municipality": "Bucaramanga",
    "areaReference": "Sector norte, punto exacto por coordinación",
    "availableFrom": "2030-08-16T16:00:00Z",
    "availableUntil": "2030-08-16T20:00:00Z",
    "contactName": "Persona de prueba",
    "contactEmail": "oferta@example.test",
    "servingsAvailable": 40,
    "distributionMode": "pickup",
    "mealDescription": "Arroz, legumbres y proteína cocida",
    "foodSafetyConfirmed": True,
    "truthConfirmed": True,
    "contactConsent": True,
    "reviewAcknowledged": True,
    "publicSummaryConsent": True,
}

IDEMPOTENCY = {"Idempotency-Key": "clave-idempotente-0044"}


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


def identity_with_session():
    def handler(request: httpx.Request):
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

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=IDENTITY_URL
    )


async def request_gateway(app, method, path, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)


@pytest.mark.anyio
async def test_create_offer_requires_session():
    upstream_calls = []

    def handler(request: httpx.Request):
        upstream_calls.append(request.url.path)
        return httpx.Response(202, json=RECEIPT)

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/me/aid-offers",
        json=MEAL_PAYLOAD,
        headers=IDEMPOTENCY,
    )

    assert response.status_code == 401
    assert upstream_calls == []


@pytest.mark.anyio
async def test_create_offer_forwards_account_decided_by_gateway():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["account"] = request.headers.get("x-account-id")
        seen["idempotency"] = request.headers.get("idempotency-key")
        seen["cookie"] = request.headers.get("cookie")
        seen["body"] = json.loads(request.read())
        return httpx.Response(202, json=RECEIPT)

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    # El cliente intenta elegir cuenta en el cuerpo: se reenvía tal
    # cual al validador upstream, pero la cuenta interna la decide el
    # gateway desde la sesión, no el cuerpo.
    payload = dict(MEAL_PAYLOAD)
    response = await request_gateway(
        app,
        "POST",
        "/api/v1/me/aid-offers",
        json=payload,
        headers=IDEMPOTENCY,
        cookies={"cusol_session": "token-valido"},
    )

    assert response.status_code == 202
    assert response.json()["trackingCode"] == "AID-2026-ABCD1234"
    assert seen["path"] == "/internal/v1/aid-offers"
    assert seen["actor"] == "authenticated"
    assert seen["account"] == ACCOUNT_ID
    assert seen["idempotency"] == IDEMPOTENCY["Idempotency-Key"]
    # La cookie jamás viaja al upstream.
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_create_offer_validates_idempotency_and_content_type():
    def handler(request: httpx.Request):
        return httpx.Response(202, json=RECEIPT)

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    missing_key = await request_gateway(
        app,
        "POST",
        "/api/v1/me/aid-offers",
        json=MEAL_PAYLOAD,
        cookies={"cusol_session": "token-valido"},
    )
    assert missing_key.status_code == 422

    wrong_type = await request_gateway(
        app,
        "POST",
        "/api/v1/me/aid-offers",
        content="kind=community_meal",
        headers={
            **IDEMPOTENCY,
            "Content-Type": "text/plain",
        },
        cookies={"cusol_session": "token-valido"},
    )
    assert wrong_type.status_code == 415


@pytest.mark.anyio
async def test_create_offer_rejects_foreign_origin():
    def handler(request: httpx.Request):
        return httpx.Response(202, json=RECEIPT)

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/me/aid-offers",
        json=MEAL_PAYLOAD,
        headers={**IDEMPOTENCY, "Origin": "https://malicioso.example"},
        cookies={"cusol_session": "token-valido"},
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_create_offer_rate_limited_per_account():
    def handler(request: httpx.Request):
        return httpx.Response(202, json=RECEIPT)

    app = create_app(
        settings=gateway_settings(
            aid_offer_write_rate_limit_per_minute=1
        ),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    first = await request_gateway(
        app,
        "POST",
        "/api/v1/me/aid-offers",
        json=MEAL_PAYLOAD,
        headers=IDEMPOTENCY,
        cookies={"cusol_session": "token-valido"},
    )
    second = await request_gateway(
        app,
        "POST",
        "/api/v1/me/aid-offers",
        json=MEAL_PAYLOAD,
        headers=IDEMPOTENCY,
        cookies={"cusol_session": "token-valido"},
    )

    assert first.status_code == 202
    assert second.status_code == 429


@pytest.mark.anyio
async def test_list_my_offers_forwards_filters():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["account"] = request.headers.get("x-account-id")
        return httpx.Response(200, json=OWNER_PAGE)

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    response = await request_gateway(
        app,
        "GET",
        "/api/v1/me/aid-offers"
        "?kind=community_meal&moderationStatus=under_review&limit=10",
        cookies={"cusol_session": "token-valido"},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["kind"] == "community_meal"
    assert seen["path"] == "/internal/v1/aid-offers"
    assert seen["params"]["kind"] == "community_meal"
    assert seen["params"]["moderationStatus"] == "under_review"
    assert seen["account"] == ACCOUNT_ID


@pytest.mark.anyio
async def test_patch_forwards_version_conflicts_as_problem():
    def handler(request: httpx.Request):
        assert request.method == "PATCH"
        return httpx.Response(
            409,
            json={
                "type": "about:blank",
                "title": "Conflicto de versión",
                "status": 409,
                "detail": "La oferta cambió.",
            },
        )

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    response = await request_gateway(
        app,
        "PATCH",
        "/api/v1/me/aid-offers/10000000-0000-4000-8000-000000000001",
        json={"version": 1, "availabilityStatus": "paused"},
        cookies={"cusol_session": "token-valido"},
    )

    assert response.status_code == 409
    assert response.json()["title"] == "Conflicto de versión"


@pytest.mark.anyio
async def test_patch_success_returns_owner_summary():
    def handler(request: httpx.Request):
        return httpx.Response(200, json=OWNER_SUMMARY)

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    response = await request_gateway(
        app,
        "PATCH",
        "/api/v1/me/aid-offers/10000000-0000-4000-8000-000000000001",
        json={"version": 1, "availableUnits": 0},
        cookies={"cusol_session": "token-valido"},
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2


@pytest.mark.anyio
async def test_directory_search_accepts_offer_kinds():
    def handler(request: httpx.Request):
        assert (
            request.url.path
            == "/internal/v1/humanitarian-directory/search"
        )
        assert request.url.params["kind"] == "community_meal"
        return httpx.Response(
            200,
            json={
                "items": [OFFER_CARD],
                "total": 1,
                "limit": 20,
                "offset": 0,
                "query": "almuerzo",
                "kind": "community_meal",
                "generatedAt": "2026-08-15T12:00:00Z",
            },
        )

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    response = await request_gateway(
        app,
        "GET",
        "/api/v1/humanitarian-directory/search"
        "?kind=community_meal&q=almuerzo",
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["publicOfferCode"] == "OFR-2026-PUB1"
    assert "contactName" not in item
    assert "accountId" not in item


# CHG-054 — Cuenta opcional en el ingreso público de reportes.


@pytest.mark.anyio
async def test_missing_report_attaches_account_when_session_is_valid():
    seen = {}

    def handler(request: httpx.Request):
        if request.url.path == "/internal/v1/missing-person-reports":
            seen["actor"] = request.headers.get("x-actor-kind")
            seen["account"] = request.headers.get("x-account-id")
            seen["cookie"] = request.headers.get("cookie")
            return httpx.Response(
                201,
                json={
                    "id": "10000000-0000-4000-8000-000000000009",
                    "publicCaseCode": "MP-2026-XYZ1",
                    "status": "under_review",
                    "receivedAt": "2026-08-15T12:00:00Z",
                },
            )
        return httpx.Response(500)

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/missing-person-reports",
        content=b"payload-multipart-simulado",
        headers={
            "Idempotency-Key": "clave-idempotente-0054",
            "Content-Type": "multipart/form-data; boundary=x",
        },
        cookies={"cusol_session": "token-valido"},
    )

    assert response.status_code == 201
    assert seen["actor"] == "authenticated"
    assert seen["account"] == ACCOUNT_ID
    # La cookie jamás viaja al upstream.
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_missing_report_stays_anonymous_without_session():
    seen = {}

    def handler(request: httpx.Request):
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["account"] = request.headers.get("x-account-id")
        return httpx.Response(
            201,
            json={
                "id": "10000000-0000-4000-8000-000000000009",
                "publicCaseCode": "MP-2026-XYZ1",
                "status": "under_review",
                "receivedAt": "2026-08-15T12:00:00Z",
            },
        )

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/missing-person-reports",
        content=b"payload-multipart-simulado",
        headers={
            "Idempotency-Key": "clave-idempotente-0054",
            "Content-Type": "multipart/form-data; boundary=x",
        },
    )

    assert response.status_code == 201
    assert seen["actor"] == "anonymous"
    assert seen["account"] is None


@pytest.mark.anyio
async def test_missing_report_ignores_invalid_session_cookie():
    seen = {}

    def handler(request: httpx.Request):
        seen["actor"] = request.headers.get("x-actor-kind")
        return httpx.Response(
            201,
            json={
                "id": "10000000-0000-4000-8000-000000000009",
                "publicCaseCode": "MP-2026-XYZ1",
                "status": "under_review",
                "receivedAt": "2026-08-15T12:00:00Z",
            },
        )

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/missing-person-reports",
        content=b"payload-multipart-simulado",
        headers={
            "Idempotency-Key": "clave-idempotente-0054",
            "Content-Type": "multipart/form-data; boundary=x",
        },
        cookies={"cusol_session": "token-vencido"},
    )

    # Sesión inválida: el envío sigue siendo anónimo, jamás falla.
    assert response.status_code == 201
    assert seen["actor"] == "anonymous"


# CHG-066 — Presencia de visitantes con consentimiento.


PRESENCE_PAYLOAD = {
    "presenceId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa9",
    "latitude": 7.13,
    "longitude": -73.13,
    "platform": "web",
}

PRESENCE_PAGE = {
    "items": [
        {
            "presenceId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa9",
            "latitude": 7.13,
            "longitude": -73.13,
            "accuracyMeters": 12.5,
            "platform": "android",
            "authenticated": True,
            "firstSeenAt": "2026-08-15T12:00:00Z",
            "updatedAt": "2026-08-15T12:05:00Z",
        }
    ],
    "total": 1,
    "windowMinutes": 30,
    "generatedAt": "2026-08-15T12:06:00Z",
}


@pytest.mark.anyio
async def test_presence_rejects_anonymous_visitors():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        return httpx.Response(202, json={"status": "accepted"})

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    response = await request_gateway(
        app, "POST", "/api/v1/presence", json=PRESENCE_PAYLOAD
    )

    # Sin sesión no hay presencia en vivo y el upstream ni se toca.
    assert response.status_code == 401
    assert "path" not in seen


@pytest.mark.anyio
async def test_presence_attaches_account_with_valid_session():
    seen = {}

    def handler(request: httpx.Request):
        seen["account"] = request.headers.get("x-account-id")
        seen["cookie"] = request.headers.get("cookie")
        seen["actor"] = request.headers.get("x-actor-kind")
        return httpx.Response(202, json={"status": "accepted"})

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/presence",
        json=PRESENCE_PAYLOAD,
        cookies={"cusol_session": "token-valido"},
    )

    assert response.status_code == 202
    assert seen["account"] == ACCOUNT_ID
    assert seen["cookie"] is None
    assert seen["actor"] == "authenticated"


@pytest.mark.anyio
async def test_admin_presence_requires_super_admin_session():
    def handler(request: httpx.Request):
        return httpx.Response(200, json=PRESENCE_PAGE)

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    without_cookie = await request_gateway(
        app, "GET", "/api/v1/admin/visitor-presence"
    )
    assert without_cookie.status_code == 401

    # La sesión válida del fixture tiene rol `user`: insuficiente.
    with_user_role = await request_gateway(
        app,
        "GET",
        "/api/v1/admin/visitor-presence",
        cookies={"cusol_session": "token-valido"},
    )
    assert with_user_role.status_code == 403


# CHG-069 — "Mi espacio": reportes propios y alertas de voluntariado.


MY_REPORTS_PAGE = {
    "items": [
        {
            "id": "cccccccc-cccc-4ccc-8ccc-ccccccccccc1",
            "kind": "missing_person_report",
            "referenceCode": "MP-2026-ABCD1234",
            "title": "Persona De Prueba",
            "status": "under_review",
            "receivedAt": "2026-08-15T12:00:00Z",
            "novelties": [
                {
                    "claimedOutcome": "found",
                    "moderationStatus": "under_review",
                    "receivedAt": "2026-08-15T12:30:00Z",
                }
            ],
        }
    ],
    "total": 1,
    "generatedAt": "2026-08-15T13:00:00Z",
}

VOLUNTEER_ALERT = {
    "id": "dddddddd-dddd-4ddd-8ddd-ddddddddddd1",
    "description": "Se necesita gente para remover escombros.",
    "address": "Calle 45 #27-08, Bucaramanga",
    "latitude": 7.1193,
    "longitude": -73.1227,
    "status": "active",
    "createdAt": "2026-08-15T12:00:00Z",
    "updatedAt": "2026-08-15T12:00:00Z",
}


@pytest.mark.anyio
async def test_my_space_routes_require_session():
    def handler(request: httpx.Request):
        return httpx.Response(200, json=MY_REPORTS_PAGE)

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    for method, path in [
        ("GET", "/api/v1/me/reports"),
        ("GET", "/api/v1/me/volunteer-alerts"),
        ("POST", "/api/v1/me/volunteer-alerts"),
    ]:
        response = await request_gateway(app, method, path)
        assert response.status_code == 401, path


@pytest.mark.anyio
async def test_my_reports_forward_with_account():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["account"] = request.headers.get("x-account-id")
        return httpx.Response(200, json=MY_REPORTS_PAGE)

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    response = await request_gateway(
        app,
        "GET",
        "/api/v1/me/reports",
        cookies={"cusol_session": "token-valido"},
    )

    assert response.status_code == 200
    assert seen["path"] == "/internal/v1/me/reports"
    assert seen["account"] == ACCOUNT_ID
    body = response.json()
    assert body["items"][0]["novelties"][0]["claimedOutcome"] == "found"


@pytest.mark.anyio
async def test_volunteer_alert_created_through_gateway():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["account"] = request.headers.get("x-account-id")
        return httpx.Response(201, json=VOLUNTEER_ALERT)

    app = create_app(
        settings=gateway_settings(),
        client=upstream_client(handler),
        identity_client=identity_with_session(),
    )

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/me/volunteer-alerts",
        cookies={"cusol_session": "token-valido"},
        json={
            "description": "Se necesita gente para remover escombros.",
            "address": "Calle 45 #27-08, Bucaramanga",
            "latitude": 7.1193,
            "longitude": -73.1227,
        },
    )

    assert response.status_code == 201
    assert seen["path"] == "/internal/v1/me/volunteer-alerts"
    assert seen["account"] == ACCOUNT_ID
    assert response.json()["status"] == "active"
