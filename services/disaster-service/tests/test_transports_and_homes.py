"""CHG-161 / CHG-162 — Transportes humanitarios y «Mi casita partida».

Transportes: cuenta obligatoria, validación de tipo/ciudad de los
centros y recibo idempotente. Casita: alta anónima permitida y recibo.
Todo con repositorio falso (sin base de datos).
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.main import create_app

from test_missing_persons import FakeStorage, request_app


ACCOUNT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa7")
ORIGIN_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb7")
DESTINATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc7")
CREATED_AT = datetime(2026, 8, 18, 21, 0, tzinfo=UTC)

AUTH_HEADERS = {
    "Idempotency-Key": "clave-idempotente-0001",
    "X-Actor-Kind": "authenticated",
    "X-Account-Id": str(ACCOUNT_ID),
}
ANON_HEADERS = {
    "Idempotency-Key": "clave-idempotente-0002",
    "X-Actor-Kind": "anonymous",
}

TRANSPORT_BODY = {
    "kind": "mule",
    "originMunicipality": "Bucaramanga",
    "destinationMunicipality": "El Playón",
    "originLocationId": str(ORIGIN_ID),
    "destinationLocationId": str(DESTINATION_ID),
    "suppliesSummary": "Agua y alimentos no perecederos",
}


class FakeTransportsRepository:
    def __init__(self, outcome="ok"):
        self.outcome = outcome
        self.calls: list[dict] = []

    async def ping(self):
        return True

    async def create_humanitarian_transport(self, **kwargs):
        self.calls.append(kwargs)
        if self.outcome != "ok":
            return self.outcome
        return {
            "id": uuid4(),
            "kind": kwargs["kind"],
            "status": "registered",
            "origin_location_id": kwargs["origin_location_id"],
            "destination_location_id": kwargs[
                "destination_location_id"
            ],
            "created_at": CREATED_AT,
        }

    async def create_damaged_home_report(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": uuid4(), "created_at": CREATED_AT}


def transports_app(repository=None):
    return create_app(
        repository=repository or FakeTransportsRepository(),
        storage=FakeStorage(),
    )


@pytest.mark.anyio
async def test_transport_requires_authenticated_account():
    repository = FakeTransportsRepository()
    app = transports_app(repository)

    response = await request_app(
        app,
        "POST",
        "/internal/v1/humanitarian-transports",
        headers=ANON_HEADERS,
        json=TRANSPORT_BODY,
    )

    assert response.status_code == 401
    assert repository.calls == []


@pytest.mark.anyio
async def test_transport_creates_and_returns_receipt():
    repository = FakeTransportsRepository()
    app = transports_app(repository)

    response = await request_app(
        app,
        "POST",
        "/internal/v1/humanitarian-transports",
        headers=AUTH_HEADERS,
        json=TRANSPORT_BODY,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "mule"
    assert body["status"] == "registered"
    assert body["originLocationId"] == str(ORIGIN_ID)
    call = repository.calls[0]
    assert call["account_id"] == ACCOUNT_ID
    assert call["origin_municipality"] == "Bucaramanga"


@pytest.mark.anyio
async def test_transport_rejects_wrong_center_kind_with_422():
    repository = FakeTransportsRepository(outcome="origin_wrong_kind")
    app = transports_app(repository)

    response = await request_app(
        app,
        "POST",
        "/internal/v1/humanitarian-transports",
        headers=AUTH_HEADERS,
        json=TRANSPORT_BODY,
    )

    assert response.status_code == 422
    assert "acopio local" in response.json()["detail"]


@pytest.mark.anyio
async def test_transport_rejects_invalid_kind_and_missing_key():
    app = transports_app()

    invalid = await request_app(
        app,
        "POST",
        "/internal/v1/humanitarian-transports",
        headers=AUTH_HEADERS,
        json={**TRANSPORT_BODY, "kind": "truck"},
    )
    missing_key = await request_app(
        app,
        "POST",
        "/internal/v1/humanitarian-transports",
        headers={"X-Actor-Kind": "authenticated",
                 "X-Account-Id": str(ACCOUNT_ID)},
        json=TRANSPORT_BODY,
    )

    assert invalid.status_code == 422
    assert missing_key.status_code == 422


@pytest.mark.anyio
async def test_damaged_home_accepts_anonymous_report():
    repository = FakeTransportsRepository()
    app = transports_app(repository)

    response = await request_app(
        app,
        "POST",
        "/internal/v1/damaged-home-reports",
        headers=ANON_HEADERS,
        json={
            "description": "La casa perdió el techo y un muro lateral.",
            "department": "Santander",
            "municipality": "Bucaramanga",
            "address": "Calle 10 # 4-20, barrio La Feria",
            "latitude": 7.11935,
            "longitude": -73.12274,
        },
    )

    assert response.status_code == 201
    assert "id" in response.json()
    call = repository.calls[0]
    assert call["account_id"] is None
    assert call["latitude"] == 7.11935


@pytest.mark.anyio
async def test_damaged_home_rejects_short_description_and_odd_coords():
    app = transports_app()

    short = await request_app(
        app,
        "POST",
        "/internal/v1/damaged-home-reports",
        headers=ANON_HEADERS,
        json={
            "description": "corta",
            "department": "Santander",
            "municipality": "Bucaramanga",
            "address": "Calle 10 # 4-20",
        },
    )
    unpaired = await request_app(
        app,
        "POST",
        "/internal/v1/damaged-home-reports",
        headers=ANON_HEADERS,
        json={
            "description": "La casa perdió el techo y un muro.",
            "department": "Santander",
            "municipality": "Bucaramanga",
            "address": "Calle 10 # 4-20",
            "latitude": 7.1,
        },
    )

    assert short.status_code == 422
    assert unpaired.status_code == 422
