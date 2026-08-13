import httpx
import pytest

from app.config import Settings
from app.main import create_app


SETTINGS = Settings(
    disaster_service_url="http://disaster-service:8001",
    upstream_timeout_seconds=1,
)


def custom_settings(**overrides) -> Settings:
    values = {
        "disaster_service_url": "http://disaster-service:8001",
        "upstream_timeout_seconds": 1,
    }
    values.update(overrides)
    return Settings(**values)


def mock_client(handler):
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(
        transport=transport,
        base_url=SETTINGS.disaster_service_url,
    )


async def get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get(path)


async def post(app, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.post(path, **kwargs)


@pytest.mark.anyio
async def test_liveness():
    upstream = mock_client(lambda _: httpx.Response(200, json={}))
    app = create_app(SETTINGS, upstream)

    response = await get(app, "/health/live")
    await upstream.aclose()

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "api-gateway"}


@pytest.mark.anyio
async def test_readiness_reports_upstream_failure():
    upstream = mock_client(
        lambda _: httpx.Response(503, json={"status": "not_ready"})
    )
    app = create_app(SETTINGS, upstream)

    response = await get(app, "/health/ready")
    await upstream.aclose()

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.anyio
async def test_disasters_are_forwarded_with_query_parameters():
    def handler(request: httpx.Request):
        assert request.url.params["disasterType"] == "flood"
        assert request.url.params["limit"] == "10"
        return httpx.Response(
            200,
            json={"items": [], "total": 0, "limit": 10, "offset": 0},
        )

    upstream = mock_client(handler)
    app = create_app(SETTINGS, upstream)

    response = await get(
        app, "/api/v1/disasters?disasterType=flood&limit=10"
    )
    await upstream.aclose()

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "limit": 10,
        "offset": 0,
    }


PEOPLE_OVERVIEW = {
    "summary": {
        "missing": 5,
        "reportedDeceased": 2,
        "confirmedAlive": 3,
        "confirmedDeceased": 2,
    },
    "recentPeople": [
        {
            "id": "9c1f6f6e-3f6a-4a6e-8f9d-2a1b3c4d5e6f",
            "displayName": "Persona demo 01 — M.R.",
            "status": "missing",
            "location": "Café Madrid, Bucaramanga",
            "relatedEvent": "Inundación en el norte de Bucaramanga",
            "source": {
                "name": "Reporte ciudadano — plataforma CUSOL",
                "sourceType": "citizen",
                "url": None,
            },
            "createdAt": "2026-08-12T10:30:00Z",
        }
    ],
    "generatedAt": "2026-08-12T12:00:00Z",
}


@pytest.mark.anyio
async def test_people_overview_is_forwarded_with_recent_limit():
    def handler(request: httpx.Request):
        assert request.url.path == "/internal/v1/people/overview"
        assert request.url.params["recentLimit"] == "15"
        return httpx.Response(200, json=PEOPLE_OVERVIEW)

    upstream = mock_client(handler)
    app = create_app(SETTINGS, upstream)

    response = await get(app, "/api/v1/people/overview?recentLimit=15")
    await upstream.aclose()

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["missing"] == 5
    assert body["recentPeople"][0]["displayName"] == (
        "Persona demo 01 — M.R."
    )


@pytest.mark.anyio
async def test_people_overview_rejects_out_of_range_limit():
    upstream = mock_client(lambda _: httpx.Response(200, json={}))
    app = create_app(SETTINGS, upstream)

    response = await get(app, "/api/v1/people/overview?recentLimit=51")
    await upstream.aclose()

    assert response.status_code == 422


@pytest.mark.anyio
async def test_people_overview_reports_upstream_failure_as_problem():
    upstream = mock_client(
        lambda _: httpx.Response(500, json={"detail": "boom"})
    )
    app = create_app(SETTINGS, upstream)

    response = await get(app, "/api/v1/people/overview")
    await upstream.aclose()

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["title"] == "Servicio de personas no disponible"


HUMAN_MAP_OVERVIEW = {
    "features": [
        {
            "kind": "cluster",
            "id": "z5:x38:y34",
            "latitude": 7.05,
            "longitude": -73.15,
            "count": 1999,
            "statusCounts": {
                "missing": 500,
                "reportedDeceased": 500,
                "confirmedAlive": 500,
                "confirmedDeceased": 499,
            },
            "bounds": {
                "west": -73.4,
                "south": 6.8,
                "east": -73.0,
                "north": 7.2,
            },
        },
        {
            "kind": "point",
            "id": "77777777-7777-4777-8777-777777777701",
            "status": "missing",
            "latitude": 7.13,
            "longitude": -73.12,
            "coordinatePrecision": "approximate",
            "verificationStatus": "under_review",
            "source": {
                "name": "Reporte ciudadano — plataforma CUSOL",
                "sourceType": "citizen",
                "url": None,
            },
            "updatedAt": "2026-08-12T14:00:00Z",
        },
    ],
    "totalMatched": 2012,
    "totalMapped": 2000,
    "unmappedCount": 12,
    "returnedFeatures": 2,
    "nextCursor": None,
    "generatedAt": "2026-08-13T12:00:00Z",
    "dataClassification": "demonstrative",
}


@pytest.mark.anyio
async def test_human_map_forwards_all_parameters():
    def handler(request: httpx.Request):
        assert request.url.path == "/internal/v1/people/map-overview"
        params = request.url.params
        assert params["west"] == "-79.0"
        assert params["south"] == "-4.3"
        assert params["east"] == "-66.8"
        assert params["north"] == "12.6"
        assert params["zoom"] == "5"
        assert params["limit"] == "200"
        assert params.get_list("statuses") == [
            "missing", "confirmed_alive"
        ]
        assert params["cursor"] == "bzo1MA=="
        return httpx.Response(200, json=HUMAN_MAP_OVERVIEW)

    upstream = mock_client(handler)
    app = create_app(SETTINGS, upstream)

    response = await get(
        app,
        "/api/v1/people/map-overview"
        "?west=-79.0&south=-4.3&east=-66.8&north=12.6&zoom=5"
        "&statuses=missing&statuses=confirmed_alive"
        "&limit=200&cursor=bzo1MA%3D%3D",
    )
    await upstream.aclose()

    assert response.status_code == 200
    body = response.json()
    assert body["totalMapped"] == 2000
    assert body["unmappedCount"] == 12
    assert body["features"][0]["kind"] == "cluster"
    assert body["features"][1]["kind"] == "point"
    assert body["features"][1]["coordinatePrecision"] == "approximate"


@pytest.mark.anyio
async def test_human_map_rejects_invalid_parameters_without_upstream():
    def handler(request: httpx.Request):
        raise AssertionError(
            "no debe llamarse al upstream con parámetros inválidos"
        )

    upstream = mock_client(handler)
    app = create_app(SETTINGS, upstream)

    inverted = await get(
        app,
        "/api/v1/people/map-overview"
        "?west=-66.8&south=-4.3&east=-79.0&north=12.6&zoom=5",
    )
    bad_zoom = await get(
        app,
        "/api/v1/people/map-overview"
        "?west=-79.0&south=-4.3&east=-66.8&north=12.6&zoom=25",
    )
    bad_status = await get(
        app,
        "/api/v1/people/map-overview"
        "?west=-79.0&south=-4.3&east=-66.8&north=12.6&zoom=5"
        "&statuses=otro",
    )
    await upstream.aclose()

    assert inverted.status_code == 422
    assert inverted.headers["content-type"] == (
        "application/problem+json"
    )
    assert bad_zoom.status_code == 422
    assert bad_status.status_code == 422


@pytest.mark.anyio
async def test_human_map_passes_through_upstream_422():
    upstream = mock_client(
        lambda _: httpx.Response(
            422,
            json={
                "type": "about:blank",
                "title": "Cursor inválido",
                "status": 422,
                "detail": "El cursor no corresponde a esta consulta.",
            },
        )
    )
    app = create_app(SETTINGS, upstream)

    response = await get(
        app,
        "/api/v1/people/map-overview"
        "?west=-79.0&south=-4.3&east=-66.8&north=12.6&zoom=5"
        "&cursor=abcd",
    )
    await upstream.aclose()

    assert response.status_code == 422


@pytest.mark.anyio
async def test_human_map_reports_upstream_failure_as_problem():
    upstream = mock_client(
        lambda _: httpx.Response(500, json={"detail": "boom"})
    )
    app = create_app(SETTINGS, upstream)

    response = await get(
        app,
        "/api/v1/people/map-overview"
        "?west=-79.0&south=-4.3&east=-66.8&north=12.6&zoom=5",
    )
    await upstream.aclose()

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["title"] == (
        "Servicio de personas no disponible"
    )


OPERATIONAL_MAP_OVERVIEW = {
    "summary": {
        "missingPerson": 1,
        "collectionCenter": 0,
        "rubbleReviewed": 0,
        "rubblePending": 0,
        "buildingPending": 1,
    },
    "items": [
        {
            "id": "44444444-4444-4444-8444-444444444409",
            "category": "building_pending",
            "title": "Edificio sin inspección registrada — sector comercial",
            "locationLabel": "Centro, Bucaramanga",
            "latitude": 7.118,
            "longitude": -73.126,
            "coordinatePrecision": "approximate",
            "verificationStatus": "unverified",
            "relatedDisasterId": "22222222-2222-4222-8222-222222222201",
            "description": None,
            "source": {
                "name": "Reporte ciudadano — plataforma CUSOL",
                "sourceType": "citizen",
                "url": None,
            },
            "updatedAt": "2026-08-12T16:00:00Z",
        },
        {
            "id": "44444444-4444-4444-8444-444444444401",
            "category": "missing_person",
            "title": "Zona de búsqueda — sector Café Madrid",
            "locationLabel": "Café Madrid, Bucaramanga",
            "latitude": 7.13,
            "longitude": -73.12,
            "coordinatePrecision": "approximate",
            "verificationStatus": "under_review",
            "relatedDisasterId": "22222222-2222-4222-8222-222222222201",
            "description": None,
            "source": {
                "name": "Reporte ciudadano — plataforma CUSOL",
                "sourceType": "citizen",
                "url": None,
            },
            "updatedAt": "2026-08-12T14:00:00Z",
        },
    ],
    "generatedAt": "2026-08-12T16:00:00Z",
    "dataClassification": "demonstrative",
}


@pytest.mark.anyio
async def test_operational_map_is_forwarded_with_limit():
    def handler(request: httpx.Request):
        assert request.url.path == "/internal/v1/operational-map/overview"
        assert request.url.params["limit"] == "25"
        return httpx.Response(200, json=OPERATIONAL_MAP_OVERVIEW)

    upstream = mock_client(handler)
    app = create_app(SETTINGS, upstream)

    response = await get(app, "/api/v1/operational-map/overview?limit=25")
    await upstream.aclose()

    assert response.status_code == 200
    body = response.json()
    assert body["dataClassification"] == "demonstrative"
    assert body["summary"]["missingPerson"] == 1
    assert body["summary"]["buildingPending"] == 1
    assert body["items"][0]["category"] == "building_pending"
    assert body["items"][0]["coordinatePrecision"] == "approximate"


@pytest.mark.anyio
async def test_operational_map_tolerates_upstream_without_buildings():
    # Despliegue coordinado CHG-010: un upstream anterior sin
    # buildingPending sigue siendo válido y el gateway emite 0.
    legacy = {
        **OPERATIONAL_MAP_OVERVIEW,
        "summary": {
            "missingPerson": 1,
            "collectionCenter": 0,
            "rubbleReviewed": 0,
            "rubblePending": 0,
        },
        "items": [OPERATIONAL_MAP_OVERVIEW["items"][1]],
    }
    upstream = mock_client(
        lambda _: httpx.Response(200, json=legacy)
    )
    app = create_app(SETTINGS, upstream)

    response = await get(app, "/api/v1/operational-map/overview")
    await upstream.aclose()

    assert response.status_code == 200
    assert response.json()["summary"]["buildingPending"] == 0


@pytest.mark.anyio
async def test_operational_map_rejects_out_of_range_limit():
    upstream = mock_client(lambda _: httpx.Response(200, json={}))
    app = create_app(SETTINGS, upstream)

    below = await get(app, "/api/v1/operational-map/overview?limit=0")
    above = await get(app, "/api/v1/operational-map/overview?limit=501")
    await upstream.aclose()

    assert below.status_code == 422
    assert above.status_code == 422


@pytest.mark.anyio
async def test_operational_map_reports_upstream_failure_as_problem():
    upstream = mock_client(
        lambda _: httpx.Response(500, json={"detail": "boom"})
    )
    app = create_app(SETTINGS, upstream)

    response = await get(app, "/api/v1/operational-map/overview")
    await upstream.aclose()

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["title"] == (
        "Servicio del mapa operacional no disponible"
    )


SEARCH_RESPONSE = {
    "items": [
        {
            "id": "55555555-5555-4555-8555-555555555501",
            "publicCaseCode": "MP-2026-DEMO01",
            "displayName": "Camila Rueda (caso demo)",
            "aliases": ["Cami"],
            "approximateAge": 34,
            "lastSeenAt": "2026-08-10T18:30:00Z",
            "lastSeenArea": "Sector Café Madrid",
            "municipality": "Bucaramanga",
            "department": "Santander",
            "clothingDescription": "Chaqueta azul",
            "physicalDescription": None,
            "distinctiveMarks": None,
            "publicPhotoUrl": None,
            "mapPointId": None,
            "updatedAt": "2026-08-12T10:00:00Z",
            "dataClassification": "demonstrative",
        }
    ],
    "total": 1,
    "query": "Camila",
}

RECEIPT_RESPONSE = {
    "id": "66666666-6666-4666-8666-666666666601",
    "publicCaseCode": "MP-2026-AAAA1111",
    "status": "under_review",
    "receivedAt": "2026-08-12T16:00:00Z",
}

REPORT_HEADERS = {"Idempotency-Key": "clave-idempotente-0001"}


@pytest.mark.anyio
async def test_missing_person_search_is_forwarded():
    def handler(request: httpx.Request):
        assert request.url.path == "/internal/v1/missing-persons/search"
        assert request.url.params["q"] == "Camila"
        assert request.url.params["limit"] == "5"
        return httpx.Response(200, json=SEARCH_RESPONSE)

    upstream = mock_client(handler)
    app = create_app(SETTINGS, upstream)

    response = await get(
        app, "/api/v1/missing-persons/search?q=Camila&limit=5"
    )
    await upstream.aclose()

    assert response.status_code == 200
    assert response.json()["items"][0]["publicCaseCode"] == (
        "MP-2026-DEMO01"
    )


@pytest.mark.anyio
async def test_missing_person_search_rate_limit():
    upstream = mock_client(
        lambda _: httpx.Response(200, json=SEARCH_RESPONSE)
    )
    app = create_app(
        custom_settings(search_rate_limit_per_minute=1), upstream
    )

    first = await get(app, "/api/v1/missing-persons/search?q=Camila")
    second = await get(app, "/api/v1/missing-persons/search?q=Camila")
    await upstream.aclose()

    assert first.status_code == 200
    assert second.status_code == 429
    assert (
        second.headers["content-type"] == "application/problem+json"
    )


@pytest.mark.anyio
async def test_missing_person_search_passes_through_client_errors():
    upstream = mock_client(
        lambda _: httpx.Response(
            422,
            json={
                "type": "about:blank",
                "title": "Consulta inválida",
                "status": 422,
                "detail": "La consulta requiere al menos dos caracteres.",
            },
        )
    )
    app = create_app(SETTINGS, upstream)

    response = await get(app, "/api/v1/missing-persons/search?q=%20a")
    await upstream.aclose()

    assert response.status_code == 422
    assert response.json()["title"] == "Consulta inválida"


@pytest.mark.anyio
async def test_report_is_forwarded_with_idempotency_header():
    def handler(request: httpx.Request):
        assert request.url.path == "/internal/v1/missing-person-reports"
        assert request.headers["idempotency-key"] == (
            "clave-idempotente-0001"
        )
        assert request.headers["content-type"].startswith(
            "multipart/form-data"
        )
        return httpx.Response(201, json=RECEIPT_RESPONSE)

    upstream = mock_client(handler)
    app = create_app(SETTINGS, upstream)

    response = await post(
        app,
        "/api/v1/missing-person-reports",
        headers=REPORT_HEADERS,
        data={"payload": "{}"},
        files=[("photos", ("f.jpg", b"datos", "image/jpeg"))],
    )
    await upstream.aclose()

    assert response.status_code == 201
    assert response.json() == RECEIPT_RESPONSE


@pytest.mark.anyio
async def test_report_requires_idempotency_header():
    upstream = mock_client(lambda _: httpx.Response(201, json={}))
    app = create_app(SETTINGS, upstream)

    response = await post(
        app,
        "/api/v1/missing-person-reports",
        data={"payload": "{}"},
    )
    await upstream.aclose()

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.anyio
async def test_report_rejects_oversized_body_early():
    def handler(_request: httpx.Request):
        raise AssertionError("el upstream no debe recibir la carga")

    upstream = mock_client(handler)
    app = create_app(
        custom_settings(max_report_body_bytes=10), upstream
    )

    response = await post(
        app,
        "/api/v1/missing-person-reports",
        headers=REPORT_HEADERS,
        content=b"x" * 100,
    )
    await upstream.aclose()

    assert response.status_code == 413


@pytest.mark.anyio
async def test_report_rate_limit():
    upstream = mock_client(
        lambda _: httpx.Response(201, json=RECEIPT_RESPONSE)
    )
    app = create_app(
        custom_settings(reports_rate_limit_per_minute=1), upstream
    )

    first = await post(
        app,
        "/api/v1/missing-person-reports",
        headers=REPORT_HEADERS,
        data={"payload": "{}"},
    )
    second = await post(
        app,
        "/api/v1/missing-person-reports",
        headers=REPORT_HEADERS,
        data={"payload": "{}"},
    )
    await upstream.aclose()

    assert first.status_code == 201
    assert second.status_code == 429


@pytest.mark.anyio
async def test_report_upstream_failure_returns_problem():
    upstream = mock_client(
        lambda _: httpx.Response(500, json={"detail": "boom"})
    )
    app = create_app(SETTINGS, upstream)

    response = await post(
        app,
        "/api/v1/missing-person-reports",
        headers=REPORT_HEADERS,
        data={"payload": "{}"},
    )
    await upstream.aclose()

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["title"] == (
        "Servicio de reportes no disponible"
    )
