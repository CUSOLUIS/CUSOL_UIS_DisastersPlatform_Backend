"""CHG-125 — Gateway de «Necesitamos ayuda».

La creación y el listado son públicos (con cuenta opcional que viaja en
headers internos); atender exige sesión válida contra identity-service;
la fotografía se reenvía tal cual. Nunca se reenvían cookies al
servicio interno.
"""

import json

import httpx
import pytest

from app.config import Settings
from app.main import create_app

DISASTER_URL = "http://disaster-service:8001"
IDENTITY_URL = "http://identity-service:8002"
PATH = "/api/v1/help-requests"
IDEMPOTENCY = {"Idempotency-Key": "clave-idempotente-0125"}
REQUEST_ID = "77777777-7777-4777-8777-777777777701"

RECEIPT = {
    "id": REQUEST_ID,
    "publicCode": "HR-2026-AAAA1111",
    "status": "active",
    "receivedAt": "2026-08-16T12:00:00Z",
    "expiresAt": "2026-08-16T18:00:00Z",
}

PAGE = {
    "items": [
        {
            "id": REQUEST_ID,
            "description": "Necesitamos ayuda urgente con rescate.",
            "address": "Calle 10 #5-20, Bucaramanga",
            "latitude": 7.12,
            "longitude": -73.12,
            "createdAt": "2026-08-16T12:00:00Z",
            "expiresAt": "2026-08-16T18:00:00Z",
            "attendersCount": 2,
            "attendedByMe": False,
            "photoUrl": None,
        }
    ],
    "total": 1,
    "generatedAt": "2026-08-16T12:05:00Z",
}

ATTEND_RECEIPT = {
    "id": REQUEST_ID,
    "attendersCount": 3,
    "attending": True,
}

USER_ACCOUNT = {
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
    "displayName": "Usuaria Normal",
    "email": "user@cusol.local",
    "assignedRole": "user",
    "status": "active",
    "sessionExpiresAt": "2026-08-16T20:00:00Z",
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
async def test_create_streams_multipart_and_hides_cookies():
    seen = {}

    async def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["content_type"] = request.headers.get("content-type", "")
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
        files=[
            (
                "payload",
                (None, '{"cualquier":"json"}', "application/json"),
            ),
            ("photos", ("foto.jpg", b"\xff\xd8\xff bytes", "image/jpeg")),
        ],
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 201
    assert response.json() == RECEIPT
    assert seen["path"] == "/internal/v1/help-requests"
    assert seen["content_type"].startswith("multipart/form-data")
    assert b'{"cualquier":"json"}' in seen["body"]
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
async def test_create_has_its_own_rate_limit():
    upstream, identity = make_clients(
        lambda _: httpx.Response(201, json=RECEIPT)
    )
    app = create_app(
        gateway_settings(help_request_rate_limit_per_minute=1),
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
async def test_list_forwards_pagination_and_actor():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["actor"] = request.headers.get("x-actor-kind")
        return httpx.Response(200, json=PAGE)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app, "GET", PATH, params={"limit": 10, "offset": 20}
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert seen["path"] == "/internal/v1/help-requests"
    assert seen["params"] == {"limit": "10", "offset": "20"}
    assert seen["actor"] == "anonymous"


# CHG-190 — la pertenencia la calcula el servicio interno contra la cuenta
# de la sesión; el gateway solo tiene que dejarla pasar hasta el cliente.
@pytest.mark.anyio
async def test_list_passes_through_ownership_flag():
    page = json.loads(json.dumps(PAGE))
    page["items"][0]["createdByMe"] = True

    def handler(request: httpx.Request):
        return httpx.Response(200, json=page)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app, "GET", PATH, cookies={"cusol_session": "token-user"}
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json()["items"][0]["createdByMe"] is True


@pytest.mark.anyio
async def test_list_defaults_ownership_to_false_when_upstream_omits_it():
    def handler(request: httpx.Request):
        return httpx.Response(200, json=PAGE)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(app, "GET", PATH)
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json()["items"][0]["createdByMe"] is False


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
async def test_attend_requires_session():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(200, json=ATTEND_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app, "POST", f"{PATH}/{REQUEST_ID}/attend"
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 401
    assert calls == []


@pytest.mark.anyio
async def test_attend_forwards_account_and_returns_count():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["account"] = request.headers.get("x-account-id")
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json=ATTEND_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        f"{PATH}/{REQUEST_ID}/attend",
        cookies={"cusol_session": "token-user"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json() == ATTEND_RECEIPT
    assert seen["path"] == (
        f"/internal/v1/help-requests/{REQUEST_ID}/attend"
    )
    assert seen["actor"] == "authenticated"
    assert seen["account"] == USER_ACCOUNT["id"]
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_attend_passes_through_not_found():
    def handler(_request: httpx.Request):
        return httpx.Response(
            404,
            headers={"content-type": "application/problem+json"},
            content=json.dumps(
                {
                    "type": "about:blank",
                    "title": "Solicitud no disponible",
                    "status": 404,
                    "detail": "La solicitud no existe o ya expiró.",
                }
            ),
        )

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        f"{PATH}/{REQUEST_ID}/attend",
        cookies={"cusol_session": "token-user"},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 404
    assert response.json()["detail"] == (
        "La solicitud no existe o ya expiró."
    )


@pytest.mark.anyio
async def test_photo_passthrough_preserves_content_type():
    def handler(request: httpx.Request):
        assert request.url.path == (
            f"/internal/v1/public/help-requests/{REQUEST_ID}/photo"
        )
        return httpx.Response(
            200,
            content=b"\xff\xd8\xffjpegbytes",
            headers={
                "content-type": "image/jpeg",
                "cache-control": "public, max-age=300",
            },
        )

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app, "GET", f"/api/v1/public/help-requests/{REQUEST_ID}/photo"
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.content == b"\xff\xd8\xffjpegbytes"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "public, max-age=300"


@pytest.mark.anyio
async def test_upstream_failure_is_503():
    def handler(_request: httpx.Request):
        raise httpx.ConnectError("sin conexión")

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    created = await request_gateway(
        app, "POST", PATH, headers=IDEMPOTENCY, content=b"x"
    )
    listed = await request_gateway(app, "GET", PATH)
    await upstream.aclose()
    await identity.aclose()

    assert created.status_code == 503
    assert listed.status_code == 503
    assert created.json()["title"] == (
        "Servicio de solicitudes no disponible"
    )


# CHG-138 — Gestión admin: ver todo, borrar una a una o vaciar. Solo
# super_admin (401 sin sesión, 403 con rol menor); el gateway reenvía
# con headers de actor y jamás las cookies.

SUPER_ADMIN_ACCOUNT = {
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa9",
    "displayName": "Admin CUSOL",
    "email": "admin@cusol.local",
    "assignedRole": "super_admin",
    "status": "active",
    "sessionExpiresAt": "2026-08-16T20:00:00Z",
}

ADMIN_PAGE = {
    "items": [
        {
            "id": REQUEST_ID,
            "publicCode": "HR-2026-AAAA1111",
            "description": "Necesitamos ayuda urgente con rescate.",
            "address": "Calle 10 #5-20, Bucaramanga",
            "latitude": 7.12,
            "longitude": -73.12,
            "notificationRadiusKm": 10,
            "createdAt": "2026-08-16T12:00:00Z",
            "expiresAt": "2026-08-16T18:00:00Z",
            "expired": True,
            "attendersCount": 2,
            "hasPhoto": False,
        }
    ],
    "total": 1,
    "generatedAt": "2026-08-16T12:05:00Z",
}


def admin_identity_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/internal/v1/auth/me":
        token = request.headers.get("x-session-token")
        if token == "token-admin":
            return httpx.Response(200, json=SUPER_ADMIN_ACCOUNT)
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


@pytest.mark.anyio
async def test_admin_help_requests_require_session_and_role():
    async def handler(request: httpx.Request):
        raise AssertionError("no debe llegar al servicio interno")

    upstream, identity = make_clients(handler, admin_identity_handler)
    app = create_app(gateway_settings(), upstream, identity)

    anonymous = await request_gateway(
        app, "GET", "/api/v1/admin/help-requests"
    )
    assert anonymous.status_code == 401

    as_user = await request_gateway(
        app,
        "GET",
        "/api/v1/admin/help-requests",
        cookies={"cusol_session": "token-user"},
    )
    assert as_user.status_code == 403


@pytest.mark.anyio
async def test_admin_list_forwards_with_actor_headers():
    seen = {}

    async def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["role"] = request.headers.get("x-actor-role")
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json=ADMIN_PAGE)

    upstream, identity = make_clients(handler, admin_identity_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "GET",
        "/api/v1/admin/help-requests?limit=50",
        cookies={"cusol_session": "token-admin"},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["expired"] is True
    assert seen["path"] == "/internal/v1/admin/help-requests"
    assert seen["role"] == "super_admin"
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_admin_delete_and_purge_forward():
    calls = []

    async def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"deleted": 1})

    upstream, identity = make_clients(handler, admin_identity_handler)
    app = create_app(gateway_settings(), upstream, identity)

    one = await request_gateway(
        app,
        "DELETE",
        f"/api/v1/admin/help-requests/{REQUEST_ID}",
        cookies={"cusol_session": "token-admin"},
    )
    assert one.status_code == 200
    assert one.json() == {"deleted": 1}

    purge = await request_gateway(
        app,
        "DELETE",
        "/api/v1/admin/help-requests",
        cookies={"cusol_session": "token-admin"},
    )
    assert purge.status_code == 200

    assert calls == [
        ("DELETE", f"/internal/v1/admin/help-requests/{REQUEST_ID}"),
        ("DELETE", "/internal/v1/admin/help-requests"),
    ]


# CHG-139 — Reinicio absoluto: exige la frase exacta, orquesta datos y
# cuentas, y reporta el conteo combinado.
@pytest.mark.anyio
async def test_platform_reset_requires_confirmation_phrase():
    async def handler(request: httpx.Request):
        raise AssertionError("sin frase no debe llegar a los servicios")

    upstream, identity = make_clients(handler, admin_identity_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/admin/platform-reset",
        cookies={"cusol_session": "token-admin"},
        json={"confirm": "reiniciar"},
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_platform_reset_orchestrates_both_services():
    calls = []

    async def disaster_handler(request: httpx.Request):
        calls.append(("disaster", request.url.path))
        return httpx.Response(200, json={"tablesCleared": 21})

    def identity_with_reset(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/v1/admin/platform-reset":
            calls.append(("identity", request.url.path))
            assert request.headers.get("x-actor-role") == "super_admin"
            return httpx.Response(200, json={"accountsDeleted": 7})
        return admin_identity_handler(request)

    upstream, identity = make_clients(disaster_handler, identity_with_reset)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        "/api/v1/admin/platform-reset",
        cookies={"cusol_session": "token-admin"},
        json={"confirm": "REINICIAR TODO"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tablesCleared"] == 21
    assert body["accountsDeleted"] == 7
    assert ("disaster", "/internal/v1/admin/platform-reset") in calls
    assert ("identity", "/internal/v1/admin/platform-reset") in calls


@pytest.mark.anyio
async def test_platform_reset_requires_super_admin():
    async def handler(request: httpx.Request):
        raise AssertionError("no debe llegar al servicio interno")

    upstream, identity = make_clients(handler, admin_identity_handler)
    app = create_app(gateway_settings(), upstream, identity)

    as_user = await request_gateway(
        app,
        "POST",
        "/api/v1/admin/platform-reset",
        cookies={"cusol_session": "token-user"},
        json={"confirm": "REINICIAR TODO"},
    )
    assert as_user.status_code == 403


# CHG-148 — Voluntario anónimo: canal público (sin sesión), multipart,
# reenvío al servicio interno y contador de vuelta.
@pytest.mark.anyio
async def test_volunteer_forwards_anonymously_and_returns_count():
    seen = {}

    async def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["idempotency"] = request.headers.get("idempotency-key")
        seen["cookie"] = request.headers.get("cookie")
        seen["body"] = await request.aread()
        return httpx.Response(200, json=ATTEND_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        f"{PATH}/{REQUEST_ID}/volunteers",
        headers=IDEMPOTENCY,
        files=[
            (
                "payload",
                (None, '{"name":"Maria"}', "application/json"),
            ),
        ],
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json() == ATTEND_RECEIPT
    assert seen["path"] == (
        f"/internal/v1/help-requests/{REQUEST_ID}/volunteers"
    )
    assert seen["actor"] == "anonymous"
    assert seen["idempotency"] == IDEMPOTENCY["Idempotency-Key"]


@pytest.mark.anyio
async def test_volunteer_requires_idempotency_key():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(200, json=ATTEND_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        f"{PATH}/{REQUEST_ID}/volunteers",
        files=[("payload", (None, '{"name":"Maria"}', "application/json"))],
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 422
    assert calls == []


# CHG-148 — Voluntarios de una solicitud: solo super_admin; el gateway
# reenvía con las cabeceras del actor y devuelve la PII descifrada.
VOLUNTEERS_PAGE = {
    "items": [
        {
            "id": "88888888-8888-4888-8888-888888888801",
            "name": "Camilo Vega",
            "phone": "+57 301 000 0000",
            "email": None,
            "hasPhoto": False,
            "createdAt": "2026-08-18T10:00:00Z",
        }
    ],
    "total": 1,
    "generatedAt": "2026-08-18T10:05:00Z",
}


@pytest.mark.anyio
async def test_admin_volunteers_require_session_and_role():
    async def handler(request: httpx.Request):
        raise AssertionError("no debe llegar al servicio interno")

    upstream, identity = make_clients(handler, admin_identity_handler)
    app = create_app(gateway_settings(), upstream, identity)

    anonymous = await request_gateway(
        app, "GET", f"/api/v1/admin/help-requests/{REQUEST_ID}/volunteers"
    )
    assert anonymous.status_code == 401

    as_user = await request_gateway(
        app,
        "GET",
        f"/api/v1/admin/help-requests/{REQUEST_ID}/volunteers",
        cookies={"cusol_session": "token-user"},
    )
    assert as_user.status_code == 403


@pytest.mark.anyio
async def test_admin_volunteers_forwards_and_returns_pii():
    seen = {}

    async def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["role"] = request.headers.get("x-actor-role")
        return httpx.Response(200, json=VOLUNTEERS_PAGE)

    upstream, identity = make_clients(handler, admin_identity_handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "GET",
        f"/api/v1/admin/help-requests/{REQUEST_ID}/volunteers",
        cookies={"cusol_session": "token-admin"},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "Camilo Vega"
    assert seen["path"] == (
        f"/internal/v1/admin/help-requests/{REQUEST_ID}/volunteers"
    )
    assert seen["role"] == "super_admin"


# CHG-180 — La comunidad de «Necesitamos ayuda» en la puerta pública:
# leer comentarios es abierto, comentar exige Origin e Idempotency-Key
# (anónimo permitido), denunciar tiene su variante anónima —con la
# huella hasheada por el gateway— y con cuenta, y borrar un comentario
# exige super_admin. Espejo de lo que CHG-165/176 dieron a acopios y
# ofertas.

COMMENTS_PATH = f"/api/v1/help-requests/{REQUEST_ID}/comments"
PUBLIC_REPORT_PATH = f"/api/v1/public/help-requests/{REQUEST_ID}/reports"
ME_REPORT_PATH = f"/api/v1/me/help-requests/{REQUEST_ID}/reports"
COMMENT_ID = "77777777-7777-4777-8777-777777777702"

COMMENTS_PAGE = {
    "items": [
        {
            "id": COMMENT_ID,
            "authorDisplayName": None,
            "actorKind": "anonymous",
            "content": "Llegó ayuda al lugar, todo cierto.",
            "rating": 5,
            "createdAt": "2026-08-19T12:00:00Z",
        }
    ],
    "total": 1,
    "ratingAverage": 4.5,
    "ratingCount": 2,
}

REPORT_RECEIPT = {
    "helpRequestId": REQUEST_ID,
    "reportsCount": 1,
    "underObservation": False,
    "disabled": False,
}


@pytest.mark.anyio
async def test_comments_are_public_and_carry_the_average():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json=COMMENTS_PAGE)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app, "GET", COMMENTS_PATH, headers={"Cookie": "otra=privada"}
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 200
    assert response.json()["ratingAverage"] == 4.5
    assert seen["path"] == f"/internal/v1/help-requests/{REQUEST_ID}/comments"
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_anonymous_comment_is_forwarded_with_its_actor():
    seen = {}

    async def handler(request: httpx.Request):
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["account"] = request.headers.get("x-account-id")
        seen["idempotency"] = request.headers.get("idempotency-key")
        seen["body"] = await request.aread()
        return httpx.Response(201, json=COMMENTS_PAGE["items"][0])

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        COMMENTS_PATH,
        headers={"Idempotency-Key": "clave-comentario-ayuda-0180"},
        json={"content": "Estuve allí y hacía falta.", "rating": 4},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 201
    assert seen["actor"] == "anonymous"
    assert seen["account"] is None
    assert b"rating" in seen["body"]


@pytest.mark.anyio
async def test_comment_rejects_a_foreign_origin():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(201, json=COMMENTS_PAGE["items"][0])

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        COMMENTS_PATH,
        headers={
            "Idempotency-Key": "clave-comentario-ayuda-0180",
            "Origin": "https://malicioso.example",
        },
        json={"content": "Comentario intruso.", "rating": 1},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 403
    assert calls == []


@pytest.mark.anyio
async def test_anonymous_report_travels_with_a_hashed_fingerprint():
    seen = {}

    async def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["denouncer"] = request.headers.get("x-denouncer-key")
        seen["actor"] = request.headers.get("x-actor-kind")
        await request.aread()
        return httpx.Response(202, json=REPORT_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    response = await request_gateway(
        app,
        "POST",
        PUBLIC_REPORT_PATH,
        headers={
            "Idempotency-Key": "clave-denuncia-ayuda-0180",
            "X-Visitor-Fingerprint": "huella-del-visitante",
        },
        json={"category": "informacion_falsa", "reason": "No existe."},
    )
    await upstream.aclose()
    await identity.aclose()

    assert response.status_code == 202
    assert response.json()["helpRequestId"] == REQUEST_ID
    assert seen["actor"] == "anonymous"
    # La huella nunca viaja en claro: se hashea en el gateway.
    assert seen["denouncer"].startswith("fp:")
    assert "huella-del-visitante" not in seen["denouncer"]


@pytest.mark.anyio
async def test_account_report_requires_a_session():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(202, json=REPORT_RECEIPT)

    upstream, identity = make_clients(handler)
    app = create_app(gateway_settings(), upstream, identity)

    sin_sesion = await request_gateway(
        app,
        "POST",
        ME_REPORT_PATH,
        headers={"Idempotency-Key": "clave-denuncia-ayuda-0180"},
        json={"category": "seguridad", "reason": "Motivo suficiente."},
    )
    con_sesion = await request_gateway(
        app,
        "POST",
        ME_REPORT_PATH,
        headers={"Idempotency-Key": "clave-denuncia-ayuda-0180"},
        cookies={"cusol_session": "token-user"},
        json={"category": "seguridad", "reason": "Motivo suficiente."},
    )
    await upstream.aclose()
    await identity.aclose()

    assert sin_sesion.status_code == 401
    assert con_sesion.status_code == 202
    assert calls == [f"/internal/v1/help-requests/{REQUEST_ID}/reports"]


@pytest.mark.anyio
async def test_comment_deletion_is_only_for_super_admin():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["role"] = request.headers.get("x-actor-role")
        return httpx.Response(200, json={"deleted": 1})

    upstream, identity = make_clients(handler, identity=admin_identity_handler)
    app = create_app(gateway_settings(), upstream, identity)

    path = f"/api/v1/admin/help-requests/{REQUEST_ID}/comments/{COMMENT_ID}"
    como_usuario = await request_gateway(
        app, "DELETE", path, cookies={"cusol_session": "token-user"}
    )
    como_admin = await request_gateway(
        app, "DELETE", path, cookies={"cusol_session": "token-admin"}
    )
    await upstream.aclose()
    await identity.aclose()

    assert como_usuario.status_code == 403
    assert como_admin.status_code == 200
    assert seen["role"] == "super_admin"
    assert seen["path"] == (
        f"/internal/v1/admin/help-requests/{REQUEST_ID}/comments/{COMMENT_ID}"
    )

