"""CHG-163 — «Ofrecer comida»: ofertas comunitarias de alimentos.

Mismas reglas que «Necesitamos ayuda»: alta anónima o con cuenta,
vigencia 1-720 horas calculada en servidor, coordenadas opcionales en
par, radio de aviso que exige punto y listado que solo devuelve
ofertas vigentes. Todo con repositorio falso (sin base de datos).
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.main import create_app

from test_missing_persons import FakeStorage, request_app


ACCOUNT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa63")
CREATED_AT = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)

AUTH_HEADERS = {
    "Idempotency-Key": "clave-idempotente-1631",
    "X-Actor-Kind": "authenticated",
    "X-Account-Id": str(ACCOUNT_ID),
}
ANON_HEADERS = {
    "Idempotency-Key": "clave-idempotente-1632",
    "X-Actor-Kind": "anonymous",
}

VALID_BODY = {
    "description": "Sancocho comunitario para cuarenta personas.",
    "address": "Calle 10 # 4-20, barrio La Feria, Bucaramanga",
    "latitude": 7.11935,
    "longitude": -73.12274,
    "durationHours": 6,
    "notificationRadiusKm": 5,
}


class FakeFoodOffersRepository:
    def __init__(self):
        self.calls: list[dict] = []
        self.rows: list[dict] = []

    async def ping(self):
        return True

    def seed(self, *, expired=False, **overrides):
        created = CREATED_AT - (
            timedelta(hours=10) if expired else timedelta(minutes=5)
        )
        row = {
            "id": uuid4(),
            "description": "Arroz con pollo para compartir hoy.",
            "address": "Carrera 27 # 30-15, Bucaramanga",
            "latitude": 7.12,
            "longitude": -73.12,
            "notification_radius_km": None,
            "created_at": created,
            "expires_at": created + timedelta(hours=6),
        }
        row.update(overrides)
        self.rows.append(row)
        return row

    async def create_food_offer(self, **kwargs):
        self.calls.append(kwargs)
        for row in self.rows:
            if row.get("idempotency_key") == kwargs["idempotency_key"]:
                return row, False
        row = {
            "id": uuid4(),
            "idempotency_key": kwargs["idempotency_key"],
            "public_code": kwargs["public_code"],
            "created_at": CREATED_AT,
            "expires_at": CREATED_AT
            + timedelta(hours=kwargs["duration_hours"]),
        }
        self.rows.append(row)
        return row, True

    async def list_active_food_offers(self, limit, offset):
        # Pacto DEC-125-02: solo vigentes; nada se borra al expirar.
        active = [
            row
            for row in self.rows
            if row["expires_at"] > CREATED_AT
        ]
        return active[offset : offset + limit], len(active)


def offers_app(repository=None):
    return create_app(
        repository=repository or FakeFoodOffersRepository(),
        storage=FakeStorage(),
    )


# --- Creación ---


@pytest.mark.anyio
async def test_anonymous_offer_creates_receipt_with_expiry():
    repository = FakeFoodOffersRepository()
    app = offers_app(repository)

    response = await request_app(
        app,
        "POST",
        "/internal/v1/food-offers",
        headers=ANON_HEADERS,
        json=VALID_BODY,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["publicCode"].startswith("FO-")
    assert body["status"] == "active"
    received = datetime.fromisoformat(body["receivedAt"])
    expires = datetime.fromisoformat(body["expiresAt"])
    assert expires - received == timedelta(hours=6)
    call = repository.calls[0]
    assert call["reporter_account_id"] is None
    assert call["notification_radius_km"] == 5


@pytest.mark.anyio
async def test_authenticated_offer_associates_account():
    repository = FakeFoodOffersRepository()
    app = offers_app(repository)

    response = await request_app(
        app,
        "POST",
        "/internal/v1/food-offers",
        headers=AUTH_HEADERS,
        json=VALID_BODY,
    )

    assert response.status_code == 201
    assert repository.calls[0]["reporter_account_id"] == ACCOUNT_ID


@pytest.mark.anyio
async def test_offer_without_coordinates_flows_with_address_only():
    repository = FakeFoodOffersRepository()
    app = offers_app(repository)
    body = dict(VALID_BODY)
    del body["latitude"], body["longitude"], body["notificationRadiusKm"]

    response = await request_app(
        app,
        "POST",
        "/internal/v1/food-offers",
        headers=ANON_HEADERS,
        json=body,
    )

    assert response.status_code == 201
    call = repository.calls[0]
    assert call["latitude"] is None
    assert call["notification_radius_km"] is None


@pytest.mark.anyio
async def test_retry_with_same_key_returns_original_receipt():
    repository = FakeFoodOffersRepository()
    app = offers_app(repository)

    first = await request_app(
        app,
        "POST",
        "/internal/v1/food-offers",
        headers=ANON_HEADERS,
        json=VALID_BODY,
    )
    second = await request_app(
        app,
        "POST",
        "/internal/v1/food-offers",
        headers=ANON_HEADERS,
        json=VALID_BODY,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.anyio
async def test_offer_rejects_contract_violations_with_422():
    repository = FakeFoodOffersRepository()
    app = offers_app(repository)

    cases = [
        {**VALID_BODY, "durationHours": 0},
        {**VALID_BODY, "durationHours": 721},
        # Radio sin punto: sin coordenadas no hay distancias que medir.
        {
            "description": VALID_BODY["description"],
            "address": VALID_BODY["address"],
            "durationHours": 6,
            "notificationRadiusKm": 5,
        },
        # Coordenadas sueltas: van las dos o ninguna.
        {
            "description": VALID_BODY["description"],
            "address": VALID_BODY["address"],
            "durationHours": 6,
            "latitude": 7.12,
        },
        # Texto basura: repetición sin vocabulario real.
        {**VALID_BODY, "description": "comida comida comida comida"},
    ]
    for body in cases:
        response = await request_app(
            app,
            "POST",
            "/internal/v1/food-offers",
            headers=ANON_HEADERS,
            json=body,
        )
        assert response.status_code == 422, body

    missing_key = await request_app(
        app,
        "POST",
        "/internal/v1/food-offers",
        headers={"X-Actor-Kind": "anonymous"},
        json=VALID_BODY,
    )
    assert missing_key.status_code == 422
    assert repository.calls == []


# --- Listado ---


@pytest.mark.anyio
async def test_list_returns_only_active_offers():
    repository = FakeFoodOffersRepository()
    active = repository.seed(notification_radius_km=8)
    repository.seed(expired=True)
    app = offers_app(repository)

    response = await request_app(
        app, "GET", "/internal/v1/food-offers"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == str(active["id"])
    assert item["notificationRadiusKm"] == 8
    assert item["latitude"] == active["latitude"]


@pytest.mark.anyio
async def test_list_rejects_unknown_page_size():
    app = offers_app()

    response = await request_app(
        app, "GET", "/internal/v1/food-offers", params={"limit": 17}
    )

    assert response.status_code == 422
