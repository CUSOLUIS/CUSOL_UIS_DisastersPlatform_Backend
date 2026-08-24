"""CHG-205 — «Ofrecer alojamiento temporal»: gemela de la oferta de comida.

Mismas reglas que CHG-163 (alta anónima o con cuenta, vigencia 1-720
horas calculada en servidor, coordenadas opcionales en par, radio que
exige punto y listado que solo devuelve vigentes) más lo propio de una
casa que se abre: plazas, si el espacio se comparte, mascotas y notas de
accesibilidad. Todo con repositorio falso (sin base de datos).
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.main import create_app

from test_missing_persons import FakeStorage, request_app


ACCOUNT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa05")
CREATED_AT = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

AUTH_HEADERS = {
    "Idempotency-Key": "clave-idempotente-2051",
    "X-Actor-Kind": "authenticated",
    "X-Account-Id": str(ACCOUNT_ID),
}
ANON_HEADERS = {
    "Idempotency-Key": "clave-idempotente-2052",
    "X-Actor-Kind": "anonymous",
}

VALID_BODY = {
    "description": "Tengo dos habitaciones libres con camas y baño.",
    "address": "Calle 10 # 4-20, barrio La Feria, Bucaramanga",
    "latitude": 7.11935,
    "longitude": -73.12274,
    "durationHours": 48,
    "notificationRadiusKm": 5,
    "spacesAvailable": 4,
    "sharedSpace": True,
    "acceptsPets": True,
    "accessibilityNotes": "Hay dos escalones en la entrada.",
}


class FakeShelterOffersRepository:
    def __init__(self):
        self.calls: list[dict] = []
        self.rows: list[dict] = []

    async def ping(self):
        return True

    def seed(self, *, expired=False, **overrides):
        created = CREATED_AT - (
            timedelta(hours=60) if expired else timedelta(minutes=5)
        )
        row = {
            "id": uuid4(),
            "description": "Un cuarto con dos camas para quien lo necesite.",
            "address": "Carrera 27 # 30-15, Bucaramanga",
            "latitude": 7.12,
            "longitude": -73.12,
            "notification_radius_km": None,
            "spaces_available": 2,
            "shared_space": False,
            "accepts_pets": False,
            "accessibility_notes": None,
            "created_at": created,
            "expires_at": created + timedelta(hours=48),
        }
        row.update(overrides)
        self.rows.append(row)
        return row

    async def create_shelter_offer(self, **kwargs):
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

    async def list_active_shelter_offers(self, limit, offset):
        active = [
            row for row in self.rows if row["expires_at"] > CREATED_AT
        ]
        return active[offset : offset + limit], len(active)


def offers_app(repository=None):
    return create_app(
        repository=repository or FakeShelterOffersRepository(),
        storage=FakeStorage(),
    )


# --- Creación ---


@pytest.mark.anyio
async def test_anonymous_offer_creates_receipt_with_expiry():
    repository = FakeShelterOffersRepository()
    app = offers_app(repository)

    response = await request_app(
        app,
        "POST",
        "/internal/v1/shelter-offers",
        headers=ANON_HEADERS,
        json=VALID_BODY,
    )

    assert response.status_code == 201
    body = response.json()
    # Su propio prefijo: una constancia de alojamiento no se confunde
    # con una de comida.
    assert body["publicCode"].startswith("AL-")
    assert body["status"] == "active"
    received = datetime.fromisoformat(body["receivedAt"])
    expires = datetime.fromisoformat(body["expiresAt"])
    assert expires - received == timedelta(hours=48)
    call = repository.calls[0]
    assert call["reporter_account_id"] is None
    assert call["spaces_available"] == 4
    assert call["shared_space"] is True
    assert call["accepts_pets"] is True


@pytest.mark.anyio
async def test_authenticated_offer_associates_account():
    repository = FakeShelterOffersRepository()
    app = offers_app(repository)

    response = await request_app(
        app,
        "POST",
        "/internal/v1/shelter-offers",
        headers=AUTH_HEADERS,
        json=VALID_BODY,
    )

    assert response.status_code == 201
    assert repository.calls[0]["reporter_account_id"] == ACCOUNT_ID


@pytest.mark.anyio
async def test_offer_without_coordinates_flows_with_address_only():
    repository = FakeShelterOffersRepository()
    app = offers_app(repository)
    body = dict(VALID_BODY)
    del body["latitude"], body["longitude"], body["notificationRadiusKm"]

    response = await request_app(
        app,
        "POST",
        "/internal/v1/shelter-offers",
        headers=ANON_HEADERS,
        json=body,
    )

    assert response.status_code == 201
    call = repository.calls[0]
    assert call["latitude"] is None
    assert call["notification_radius_km"] is None


@pytest.mark.anyio
async def test_pets_and_notes_are_optional():
    """Sin mascotas declaradas se asume que no, y una nota en blanco no
    se guarda como cadena vacía: es ausencia."""
    repository = FakeShelterOffersRepository()
    app = offers_app(repository)
    body = dict(VALID_BODY)
    del body["acceptsPets"]
    body["accessibilityNotes"] = "   "

    response = await request_app(
        app,
        "POST",
        "/internal/v1/shelter-offers",
        headers=ANON_HEADERS,
        json=body,
    )

    assert response.status_code == 201
    call = repository.calls[0]
    assert call["accepts_pets"] is False
    assert call["accessibility_notes"] is None


@pytest.mark.anyio
async def test_retry_with_same_key_returns_original_receipt():
    repository = FakeShelterOffersRepository()
    app = offers_app(repository)

    first = await request_app(
        app,
        "POST",
        "/internal/v1/shelter-offers",
        headers=ANON_HEADERS,
        json=VALID_BODY,
    )
    second = await request_app(
        app,
        "POST",
        "/internal/v1/shelter-offers",
        headers=ANON_HEADERS,
        json=VALID_BODY,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.anyio
async def test_offer_rejects_contract_violations_with_422():
    repository = FakeShelterOffersRepository()
    app = offers_app(repository)

    cases = [
        {**VALID_BODY, "durationHours": 0},
        {**VALID_BODY, "durationHours": 721},
        # Plazas: al menos una, y el tope del contrato manda.
        {**VALID_BODY, "spacesAvailable": 0},
        {**VALID_BODY, "spacesAvailable": 1001},
        # «Compartido» es obligatorio: no contestar no es lo mismo que
        # decir que no.
        {k: v for k, v in VALID_BODY.items() if k != "sharedSpace"},
        # Radio sin punto.
        {
            "description": VALID_BODY["description"],
            "address": VALID_BODY["address"],
            "durationHours": 48,
            "notificationRadiusKm": 5,
            "spacesAvailable": 2,
            "sharedSpace": False,
        },
        # Coordenadas sueltas: van las dos o ninguna.
        {
            "description": VALID_BODY["description"],
            "address": VALID_BODY["address"],
            "durationHours": 48,
            "latitude": 7.12,
            "spacesAvailable": 2,
            "sharedSpace": False,
        },
        # Texto basura: repetición sin vocabulario real.
        {**VALID_BODY, "description": "cuarto cuarto cuarto cuarto"},
        # Notas de accesibilidad más largas que el tope.
        {**VALID_BODY, "accessibilityNotes": "x" * 501},
    ]
    for body in cases:
        response = await request_app(
            app,
            "POST",
            "/internal/v1/shelter-offers",
            headers=ANON_HEADERS,
            json=body,
        )
        assert response.status_code == 422, body

    missing_key = await request_app(
        app,
        "POST",
        "/internal/v1/shelter-offers",
        headers={"X-Actor-Kind": "anonymous"},
        json=VALID_BODY,
    )
    assert missing_key.status_code == 422
    assert repository.calls == []


# --- Listado ---


@pytest.mark.anyio
async def test_listing_returns_only_active_offers_with_own_fields():
    repository = FakeShelterOffersRepository()
    repository.seed(spaces_available=6, shared_space=True, accepts_pets=True)
    repository.seed(expired=True)
    app = offers_app(repository)

    response = await request_app(
        app, "GET", "/internal/v1/shelter-offers?limit=10"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    # Lo propio del alojamiento viaja al mapa, no se queda en la base.
    assert item["spacesAvailable"] == 6
    assert item["sharedSpace"] is True
    assert item["acceptsPets"] is True
    assert "accessibilityNotes" in item


@pytest.mark.anyio
async def test_listing_rejects_page_sizes_outside_the_contract():
    app = offers_app()

    response = await request_app(
        app, "GET", "/internal/v1/shelter-offers?limit=17"
    )

    assert response.status_code == 422
