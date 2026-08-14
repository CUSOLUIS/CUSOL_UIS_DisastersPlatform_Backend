"""CHG-035 — Gateway del reporte de edificio sin verificar.

El gateway reenvía el multipart en streaming sin interpretarlo, valida
la cabecera de idempotencia y el tamaño declarado, aplica un límite de
tasa propio y nunca reenvía cookies al servicio interno.
"""

import httpx
import pytest

from app.config import Settings
from app.main import create_app


DISASTER_URL = "http://disaster-service:8001"
PATH = "/api/v1/unverified-building-reports"
IDEMPOTENCY = {"Idempotency-Key": "clave-idempotente-0035"}

RECEIPT = {
    "id": "99999999-9999-4999-8999-999999999903",
    "publicTrackingCode": "BR-2026-AAAA1111",
    "status": "under_review",
    "receivedAt": "2026-08-14T12:00:00Z",
}


def gateway_settings(**overrides) -> Settings:
    values = {
        "disaster_service_url": DISASTER_URL,
        "upstream_timeout_seconds": 1,
    }
    values.update(overrides)
    return Settings(**values)


def upstream_client(handler):
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=DISASTER_URL
    )


async def post_gateway(app, path=PATH, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post(path, **kwargs)


@pytest.mark.anyio
async def test_building_report_streams_multipart_untouched():
    seen = {}

    async def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["content_type"] = request.headers.get("content-type", "")
        seen["idempotency"] = request.headers.get("idempotency-key")
        seen["actor"] = request.headers.get("x-actor-kind")
        seen["cookie"] = request.headers.get("cookie")
        seen["body"] = await request.aread()
        return httpx.Response(201, json=RECEIPT)

    upstream = upstream_client(handler)
    app = create_app(gateway_settings(), upstream)

    response = await post_gateway(
        app,
        headers={**IDEMPOTENCY, "Cookie": "cusol_session=privada"},
        files=[
            ("payload", (None, '{"cualquier":"json"}',
                         "application/json")),
            ("photos", ("foto.jpg", b"\xff\xd8\xff bytes",
                        "image/jpeg")),
        ],
    )
    await upstream.aclose()

    assert response.status_code == 201
    assert response.json() == RECEIPT
    assert seen["path"] == "/internal/v1/unverified-building-reports"
    # El cuerpo llega intacto (multipart sin parsear) con su boundary.
    assert seen["content_type"].startswith("multipart/form-data")
    assert b'{"cualquier":"json"}' in seen["body"]
    assert b"\xff\xd8\xff bytes" in seen["body"]
    assert seen["idempotency"] == IDEMPOTENCY["Idempotency-Key"]
    assert seen["actor"] == "anonymous"
    assert seen["cookie"] is None


@pytest.mark.anyio
async def test_building_report_requires_idempotency_key():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(201, json=RECEIPT)

    upstream = upstream_client(handler)
    app = create_app(gateway_settings(), upstream)

    missing = await post_gateway(app, content=b"", headers={})
    short = await post_gateway(
        app, content=b"", headers={"Idempotency-Key": "corta"}
    )
    await upstream.aclose()

    assert missing.status_code == 422
    assert short.status_code == 422
    assert calls == []


@pytest.mark.anyio
async def test_building_report_rejects_declared_oversize():
    upstream = upstream_client(
        lambda _: httpx.Response(201, json=RECEIPT)
    )
    app = create_app(
        gateway_settings(max_report_body_bytes=1024), upstream
    )

    response = await post_gateway(
        app,
        headers={**IDEMPOTENCY, "Content-Length": "2048"},
        content=b"x" * 2048,
    )
    await upstream.aclose()

    assert response.status_code == 413


@pytest.mark.anyio
async def test_building_report_passes_through_upstream_errors():
    def handler(_request: httpx.Request):
        import json as jsonlib

        return httpx.Response(
            422,
            headers={"content-type": "application/problem+json"},
            content=jsonlib.dumps(
                {
                    "type": "about:blank",
                    "title": "Datos inválidos",
                    "status": 422,
                    "detail": "Revisa los campos: pendingReasons.",
                }
            ),
        )

    upstream = upstream_client(handler)
    app = create_app(gateway_settings(), upstream)

    response = await post_gateway(app, headers=IDEMPOTENCY, content=b"x")
    await upstream.aclose()

    assert response.status_code == 422
    assert response.headers["content-type"] == (
        "application/problem+json"
    )
    assert response.json()["detail"] == (
        "Revisa los campos: pendingReasons."
    )


@pytest.mark.anyio
async def test_building_report_has_its_own_rate_limit():
    def handler(request: httpx.Request):
        if request.url.path.endswith("/ratings"):
            return httpx.Response(
                202,
                json={
                    "id": "99999999-9999-4999-8999-999999999904",
                    "status": "under_review",
                    "actorKind": "anonymous",
                    "receivedAt": "2026-08-14T12:10:00Z",
                },
            )
        return httpx.Response(201, json=RECEIPT)

    upstream = upstream_client(handler)
    app = create_app(
        gateway_settings(building_reports_rate_limit_per_minute=1),
        upstream,
    )

    first = await post_gateway(app, headers=IDEMPOTENCY, content=b"x")
    second = await post_gateway(app, headers=IDEMPOTENCY, content=b"x")
    # Otros límites (p. ej. aporte anónimo del directorio) no se ven
    # afectados por consumir el de reportes de edificio.
    directory = await post_gateway(
        app,
        path=(
            "/api/v1/public/aid-locations/"
            "44444444-4444-4444-8444-444444444404/ratings"
        ),
        headers=IDEMPOTENCY,
        files={"payload": (None, "{}", "application/json")},
    )
    await upstream.aclose()

    assert first.status_code == 201
    assert second.status_code == 429
    assert directory.status_code == 202


@pytest.mark.anyio
async def test_building_report_upstream_failure_is_503():
    def handler(_request: httpx.Request):
        raise httpx.ConnectError("sin conexión")

    upstream = upstream_client(handler)
    app = create_app(gateway_settings(), upstream)

    response = await post_gateway(app, headers=IDEMPOTENCY, content=b"x")
    await upstream.aclose()

    assert response.status_code == 503
    assert response.json()["title"] == (
        "Servicio de reportes no disponible"
    )
