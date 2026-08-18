"""CHG-154 — Gateway de la gestión admin de registros de personas.

Listar (con ocultos), ocultar (reversible, nada se borra), restaurar y
editar. Solo super_admin; las mutaciones validan Origin; el gateway
escribe los encabezados internos de actor.
"""

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
    "sessionExpiresAt": "2026-08-18T20:00:00Z",
}
USER_ACCOUNT = {
    **ADMIN_ACCOUNT,
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
    "displayName": "Usuaria Normal",
    "email": "user@cusol.local",
    "assignedRole": "user",
}

PERSON_ID = "99999999-9999-4999-8999-999999999901"

PERSON = {
    "id": PERSON_ID,
    "displayName": "Marina Rueda",
    "status": "missing",
    "location": "Bucaramanga, Santander",
    "relatedEvent": "Deslizamiento Mesa de los Santos",
    "latitude": 7.1,
    "longitude": -73.1,
    "hasLinkedCase": True,
    "source": {
        "name": "Reporte ciudadano",
        "sourceType": "citizen",
        "url": None,
    },
    "createdAt": "2026-08-10T10:00:00Z",
    "updatedAt": "2026-08-18T10:00:00Z",
    "hiddenAt": None,
    "hiddenBy": None,
}

PEOPLE_PAGE = {"items": [PERSON], "total": 1}

HIDDEN_PERSON = {
    **PERSON,
    "hiddenAt": "2026-08-18T11:00:00Z",
    "hiddenBy": "Admin CUSOL",
}

ADMIN_COOKIE = {"Cookie": "cusol_session=token-admin"}
USER_COOKIE = {"Cookie": "cusol_session=token-user"}


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


@pytest.mark.anyio
async def test_people_routes_require_session_and_role():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(200, json=PEOPLE_PAGE)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    anonymous = await request_gateway(app, "GET", "/api/v1/admin/people")
    normal_user = await request_gateway(
        app, "GET", "/api/v1/admin/people", headers=USER_COOKIE
    )
    hide_anonymous = await request_gateway(
        app, "POST", f"/api/v1/admin/people/{PERSON_ID}/hide"
    )
    await upstream.aclose()
    await identity.aclose()

    assert anonymous.status_code == 401
    assert normal_user.status_code == 403
    assert hide_anonymous.status_code == 401
    assert calls == []


@pytest.mark.anyio
async def test_list_people_forwards_filters_and_actor_headers():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["params"] = request.url.params.multi_items()
        seen["actor_role"] = request.headers.get("x-actor-role")
        seen["actor_id"] = request.headers.get("x-actor-account-id")
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json=PEOPLE_PAGE)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "GET",
        "/api/v1/admin/people",
        headers=ADMIN_COOKIE,
        params=[
            ("statuses", "missing"),
            ("statuses", "confirmed_alive"),
            ("q", "marina"),
            ("visibility", "hidden"),
            ("limit", "10"),
        ],
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json() == PEOPLE_PAGE
    assert seen["path"] == "/internal/v1/admin/people"
    assert ("statuses", "missing") in seen["params"]
    assert ("statuses", "confirmed_alive") in seen["params"]
    assert ("q", "marina") in seen["params"]
    assert ("visibility", "hidden") in seen["params"]
    assert seen["actor_role"] == "super_admin"
    assert seen["actor_id"] == ADMIN_ACCOUNT["id"]
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_update_person_forwards_body_and_passes_conflict():
    seen = {}

    async def handler(request: httpx.Request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = await request.aread()
        return httpx.Response(200, json={**PERSON, "displayName": "Marina R."})

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "PATCH",
        f"/api/v1/admin/people/{PERSON_ID}",
        headers=ADMIN_COOKIE,
        json={"displayName": "Marina R."},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json()["displayName"] == "Marina R."
    assert seen["method"] == "PATCH"
    assert seen["path"] == f"/internal/v1/admin/people/{PERSON_ID}"
    assert b"Marina R." in seen["body"]

    conflict = {
        "type": "about:blank",
        "title": "Estado gobernado por novedades",
        "status": 409,
        "detail": "El estado no se edita a mano.",
    }

    def conflict_handler(request: httpx.Request):
        return httpx.Response(409, json=conflict)

    upstream, identity = make_clients(conflict_handler)
    app = create_app(gateway_settings(), upstream, identity)
    response = await request_gateway(
        app,
        "PATCH",
        f"/api/v1/admin/people/{PERSON_ID}",
        headers=ADMIN_COOKIE,
        json={"status": "confirmed_alive"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 409
    assert response.json() == conflict


@pytest.mark.anyio
async def test_hide_and_restore_mirror_the_record():
    def handler(request: httpx.Request):
        if request.url.path.endswith("/hide"):
            return httpx.Response(200, json=HIDDEN_PERSON)
        if request.url.path.endswith("/restore"):
            return httpx.Response(200, json=PERSON)
        raise AssertionError(f"ruta inesperada: {request.url.path}")

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    hidden = await request_gateway(
        app,
        "POST",
        f"/api/v1/admin/people/{PERSON_ID}/hide",
        headers=ADMIN_COOKIE,
    )
    restored = await request_gateway(
        app,
        "POST",
        f"/api/v1/admin/people/{PERSON_ID}/restore",
        headers=ADMIN_COOKIE,
    )
    await upstream.aclose()
    await identity.aclose()

    assert hidden.status_code == 200
    assert hidden.json()["hiddenAt"] == HIDDEN_PERSON["hiddenAt"]
    assert hidden.json()["hiddenBy"] == "Admin CUSOL"
    assert restored.status_code == 200
    assert restored.json()["hiddenAt"] is None


@pytest.mark.anyio
async def test_people_mutations_reject_disallowed_origin():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(200, json=HIDDEN_PERSON)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        f"/api/v1/admin/people/{PERSON_ID}/hide",
        headers={**ADMIN_COOKIE, "Origin": "https://malicioso.example"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 403
    assert calls == []
