"""CHG-208 — Gateway de la capa sísmica y la red de emergencia.

El gateway es quien declara el actor: sin sesión todo va anónimo; con
sesión viajan `x-account-id` (y el rol solo en rutas admin). El nombre
visible y el teléfono salen de la sesión, jamás del navegador, y la
cookie nunca se reenvía al servicio interno.
"""

import json

import httpx
import pytest

from app.config import Settings
from app.main import create_app

DISASTER_URL = "http://disaster-service:8001"
IDENTITY_URL = "http://identity-service:8002"

EVENT_ID = "99999999-9999-4999-8999-999999999901"
ALERT_ID = "99999999-9999-4999-8999-999999999902"
CONTACT_ID = "99999999-9999-4999-8999-999999999903"

USER_ACCOUNT = {
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa71",
    "displayName": "Laura Gómez",
    "email": "laura@cusol.local",
    "assignedRole": "user",
    "status": "active",
    "sessionExpiresAt": "2026-08-27T20:00:00Z",
    "phone": "+57 300 123 4567",
}

ADMIN_ACCOUNT = {
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa72",
    "displayName": "Super Admin",
    "email": "admin@cusol.local",
    "assignedRole": "super_admin",
    "status": "active",
    "sessionExpiresAt": "2026-08-27T20:00:00Z",
}

EVENTS_PAYLOAD = {
    "events": [
        {
            "id": EVENT_ID,
            "source": "SIMULATED",
            "sourceEventId": "SIM-2026-ABCD1234",
            "magnitude": 5.2,
            "depthKm": 20.0,
            "latitude": 7.12,
            "longitude": -73.12,
            "originTimeUtc": "2026-08-26T17:40:00Z",
            "processingStatus": "SEISMIC_DATA_PRELIMINARY",
            "isSimulated": True,
            "simulatedBanner": (
                "🧪 EVENTO SIMULADO — NO ES UN SISMO REAL"
            ),
            "description": None,
            "pendingInstrumentalNotice": None,
            "zones": [],
        }
    ],
    "generatedAt": "2026-08-26T17:41:00Z",
}

AFFECTED_PAYLOAD = {
    "eventId": EVENT_ID,
    "markers": [
        {
            "latitude": 7.12,
            "longitude": -73.12,
            "severityLevel": "STRONG",
            "status": "ACTIVE",
            "identified": False,
            "displayName": None,
            "alertId": None,
            "isSelf": False,
        }
    ],
    "generatedAt": "2026-08-26T17:41:00Z",
}

SETTINGS_PAYLOAD = {"enabled": True, "maxContacts": 3}

PANEL_PAYLOAD = {
    "alertId": ALERT_ID,
    "displayName": "Julián Villamizar",
    "status": "ACTIVE",
    "magnitude": 5.2,
    "originTimeUtc": "2026-08-26T17:40:00Z",
    "severityLevel": "STRONG",
    "zoneTitle": "Sacudida fuerte estimada",
    "latitude": 7.119349,
    "longitude": -73.122742,
    "accuracyMeters": 14.0,
    "locatedAt": "2026-08-26T17:39:00Z",
    "resolvedAddress": None,
    "alertCreatedAt": "2026-08-26T17:41:00Z",
    "safeConfirmedAt": None,
    "isSimulated": True,
}

SIMULATION_RECEIPT = {
    "eventId": EVENT_ID,
    "sourceEventId": "SIM-2026-ABCD1234",
    "zonesCreated": 3,
    "alertsActivated": 1,
    "banner": "🧪 EVENTO SIMULADO — NO ES UN SISMO REAL",
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
        if token == "token-user":
            return httpx.Response(200, json=USER_ACCOUNT)
        if token == "token-admin":
            return httpx.Response(200, json=ADMIN_ACCOUNT)
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
async def test_eventos_publicos_sin_sesion():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["actor"] = request.headers.get("x-actor-kind")
        return httpx.Response(200, json=EVENTS_PAYLOAD)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)
    response = await request_gateway(app, "GET", "/api/v1/seismic/events")
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json()["events"][0]["simulatedBanner"].startswith("🧪")
    assert seen["path"] == "/internal/v1/seismic/events"
    assert seen["actor"] == "anonymous"


@pytest.mark.anyio
async def test_afectados_declara_actor_y_oculta_cookie():
    seen = {}

    def handler(request: httpx.Request):
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["account"] = request.headers.get("x-account-id")
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json=AFFECTED_PAYLOAD)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)
    # Sin sesión: anónimo.
    response = await request_gateway(
        app, "GET", f"/api/v1/seismic/events/{EVENT_ID}/affected"
    )
    assert response.status_code == 200
    assert seen["actor"] == "anonymous"
    assert seen["account"] is None
    # Con sesión: autenticado, y la cookie no cruza.
    response = await request_gateway(
        app,
        "GET",
        f"/api/v1/seismic/events/{EVENT_ID}/affected",
        headers={"Cookie": "cusol_session=token-user"},
    )
    await upstream.aclose()
    await identity.aclose()
    assert response.status_code == 200
    assert seen["actor"] == "authenticated"
    assert seen["account"] == USER_ACCOUNT["id"]
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_ajustes_toman_el_nombre_de_la_sesion():
    seen = {}

    async def handler(request: httpx.Request):
        seen["body"] = await request.aread()
        seen["method"] = request.method
        return httpx.Response(200, json=SETTINGS_PAYLOAD)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)
    # Sin sesión: 401 sin tocar el upstream.
    response = await request_gateway(
        app,
        "PUT",
        "/api/v1/seismic/settings",
        json={"enabled": True},
    )
    assert response.status_code == 401
    # Con sesión: el gateway inyecta el displayName de la cuenta aunque
    # el navegador intente colar otro.
    response = await request_gateway(
        app,
        "PUT",
        "/api/v1/seismic/settings",
        json={"enabled": True, "displayName": "Impostora"},
        headers={"Cookie": "cusol_session=token-user"},
    )
    await upstream.aclose()
    await identity.aclose()
    assert response.status_code == 200
    assert b"Laura G" in seen["body"]
    assert b"Impostora" not in seen["body"]


@pytest.mark.anyio
async def test_coincidencias_llevan_identidad_de_la_sesion():
    seen = {}

    async def handler(request: httpx.Request):
        seen["body"] = await request.aread()
        seen["path"] = request.url.path
        return httpx.Response(200, json={"invitations": []})

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)
    response = await request_gateway(
        app,
        "POST",
        "/api/v1/seismic/invitations/match",
        json={"documentNumber": "1.098.765.432"},
        headers={"Cookie": "cusol_session=token-user"},
    )
    await upstream.aclose()
    await identity.aclose()
    assert response.status_code == 200
    assert seen["path"] == "/internal/v1/seismic/invitations/match"
    assert b"Laura G" in seen["body"]
    assert b"3001234567" in seen["body"] or b"300 123 4567" in seen["body"]
    assert b"1.098.765.432" in seen["body"]


@pytest.mark.anyio
async def test_panel_exige_sesion_y_pasa_el_404():
    def handler(request: httpx.Request):
        return httpx.Response(
            404,
            json={
                "type": "about:blank",
                "title": "Alerta no disponible",
                "status": 404,
                "detail": "La alerta no existe o no estás autorizado "
                "para consultarla.",
            },
        )

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)
    response = await request_gateway(
        app, "GET", f"/api/v1/seismic/alerts/{ALERT_ID}"
    )
    assert response.status_code == 401
    response = await request_gateway(
        app,
        "GET",
        f"/api/v1/seismic/alerts/{ALERT_ID}",
        headers={"Cookie": "cusol_session=token-user"},
    )
    await upstream.aclose()
    await identity.aclose()
    assert response.status_code == 404


@pytest.mark.anyio
async def test_panel_valida_respuesta_autorizada():
    def handler(request: httpx.Request):
        return httpx.Response(200, json=PANEL_PAYLOAD)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)
    response = await request_gateway(
        app,
        "GET",
        f"/api/v1/seismic/alerts/{ALERT_ID}",
        headers={"Cookie": "cusol_session=token-user"},
    )
    await upstream.aclose()
    await identity.aclose()
    assert response.status_code == 200
    body = response.json()
    assert body["displayName"] == "Julián Villamizar"
    assert body["zoneTitle"] == "Sacudida fuerte estimada"


@pytest.mark.anyio
async def test_simulacro_exige_rol_super_admin():
    seen = {}

    async def handler(request: httpx.Request):
        seen["role"] = request.headers.get("x-actor-role")
        seen["body"] = await request.aread()
        return httpx.Response(201, json=SIMULATION_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)
    body = {"latitude": 7.12, "longitude": -73.12, "magnitude": 5.2}
    # Usuaria normal: 403 sin tocar el upstream.
    response = await request_gateway(
        app,
        "POST",
        "/api/v1/admin/seismic/simulations",
        json=body,
        headers={"Cookie": "cusol_session=token-user"},
    )
    assert response.status_code == 403
    assert "role" not in seen
    # Super admin: cruza con el rol declarado por el gateway.
    response = await request_gateway(
        app,
        "POST",
        "/api/v1/admin/seismic/simulations",
        json=body,
        headers={"Cookie": "cusol_session=token-admin"},
    )
    await upstream.aclose()
    await identity.aclose()
    assert response.status_code == 201
    assert response.json()["banner"].startswith("🧪")
    assert seen["role"] == "super_admin"


@pytest.mark.anyio
async def test_contactos_reenvian_cuerpo_con_cuenta():
    seen = {}

    async def handler(request: httpx.Request):
        seen["account"] = request.headers.get("x-account-id")
        seen["body"] = await request.aread()
        return httpx.Response(
            201,
            json={
                "id": CONTACT_ID,
                "status": "PENDING",
                "displayName": "Ana Martínez",
                "linked": False,
                "createdAt": "2026-08-26T17:41:00Z",
            },
        )

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)
    response = await request_gateway(
        app,
        "POST",
        "/api/v1/seismic/contacts",
        json={
            "firstNames": "Ana",
            "lastNames": "Martínez",
            "documentType": "Cédula de ciudadanía",
            "documentNumber": "1098765432",
            "phone": "+57 301 555 6677",
        },
        headers={"Cookie": "cusol_session=token-user"},
    )
    await upstream.aclose()
    await identity.aclose()
    assert response.status_code == 201
    assert seen["account"] == USER_ACCOUNT["id"]
    assert b"Ana" in seen["body"]


# CHG-215 — Consulta de ID compartible y alta directa por shareCode.


def identity_with_share_code(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/internal/v1/accounts/by-share-code/CUSOL-ABC234":
        return httpx.Response(
            200,
            json={
                "accountId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb02",
                "firstNames": "María Paz",
                "lastNames": "Rueda",
                "phone": "+57 3001234567",
            },
        )
    if request.url.path.startswith("/internal/v1/accounts/by-share-code/"):
        return httpx.Response(
            404,
            json={
                "type": "about:blank",
                "title": "ID no encontrado",
                "status": 404,
                "detail": "Ese ID no corresponde a ninguna cuenta.",
            },
        )
    return identity_handler(request)


@pytest.mark.anyio
async def test_consulta_de_id_exige_sesion():
    upstream, identity = make_clients(
        lambda request: httpx.Response(500), identity_with_share_code
    )
    app = create_app(gateway_settings(), upstream, identity)
    response = await request_gateway(
        app, "GET", "/api/v1/accounts/share-code/CUSOL-ABC234"
    )
    await upstream.aclose()
    await identity.aclose()
    assert response.status_code == 401


@pytest.mark.anyio
async def test_consulta_de_id_autollena_sin_correo_ni_uuid():
    upstream, identity = make_clients(
        lambda request: httpx.Response(500), identity_with_share_code
    )
    app = create_app(gateway_settings(), upstream, identity)
    response = await request_gateway(
        app,
        "GET",
        "/api/v1/accounts/share-code/CUSOL-ABC234",
        cookies={"cusol_session": "token-user"},
    )
    await upstream.aclose()
    await identity.aclose()
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "firstNames": "María Paz",
        "lastNames": "Rueda",
        "phone": "+57 3001234567",
    }


@pytest.mark.anyio
async def test_consulta_de_id_desconocido_devuelve_404():
    upstream, identity = make_clients(
        lambda request: httpx.Response(500), identity_with_share_code
    )
    app = create_app(gateway_settings(), upstream, identity)
    response = await request_gateway(
        app,
        "GET",
        "/api/v1/accounts/share-code/CUSOL-ZZZ999",
        cookies={"cusol_session": "token-user"},
    )
    await upstream.aclose()
    await identity.aclose()
    assert response.status_code == 404


@pytest.mark.anyio
async def test_alta_por_share_code_va_a_la_via_directa():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "id": "cccccccc-cccc-4ccc-8ccc-cccccccccc03",
                "status": "PENDING",
                "displayName": "María Paz Rueda",
                "linked": True,
                "createdAt": "2026-08-27T20:00:00Z",
            },
        )

    upstream, identity = make_clients(handler, identity_with_share_code)
    app = create_app(gateway_settings(), upstream, identity)
    response = await request_gateway(
        app,
        "POST",
        "/api/v1/seismic/contacts",
        json={"shareCode": "CUSOL-ABC234"},
        cookies={"cusol_session": "token-user"},
    )
    await upstream.aclose()
    await identity.aclose()
    assert response.status_code == 201
    assert seen["path"] == "/internal/v1/seismic/contacts/direct"
    # El nombre visible lo fija el gateway desde la cuenta, no el navegador.
    assert seen["body"] == {
        "contactAccountId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb02",
        "displayName": "María Paz Rueda",
    }
