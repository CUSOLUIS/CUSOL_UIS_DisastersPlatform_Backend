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
    # CHG-171: conductor y tractocamión obligatorios en altas nuevas.
    "driverFullName": "Pedro Antonio Rojas",
    "driverDocumentType": "Cédula de ciudadanía",
    "driverDocumentNumber": "1098765432",
    "driverPhone": "+57 300 123 4567",
    "tractorPlate": "abc 123",
    "trailerPlate": "R-99881",
    "vehicleVisibleCharacteristics": (
        "Tractocamión blanco con tráiler gris y franja azul lateral."
    ),
}

TRANSPORT_ID = UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd7")


class FakeTransportsRepository:
    def __init__(self, outcome="ok", journey_outcome="ok"):
        self.outcome = outcome
        self.journey_outcome = journey_outcome
        self.calls: list[dict] = []
        self.journey_calls: list[tuple[str, dict]] = []

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

    # --- CHG-171 ---

    async def list_transport_cities(self):
        return [
            {"name": "Bucaramanga", "department": "Santander"},
            {"name": "Medellín", "department": "Antioquia"},
        ]

    def _journey_receipt(self, status):
        return {
            "id": TRANSPORT_ID,
            "status": status,
            "departed_at": CREATED_AT if status != "registered" else None,
            "arrived_at": CREATED_AT if status == "arrived" else None,
            "last_position_at": None,
        }

    async def start_transport_journey(self, **kwargs):
        self.journey_calls.append(("start", kwargs))
        if self.journey_outcome != "ok":
            return (
                None
                if self.journey_outcome == "missing"
                else self.journey_outcome
            )
        return self._journey_receipt("in_transit")

    async def arrive_transport_journey(self, **kwargs):
        self.journey_calls.append(("arrive", kwargs))
        if self.journey_outcome != "ok":
            return (
                None
                if self.journey_outcome == "missing"
                else self.journey_outcome
            )
        return self._journey_receipt("arrived")

    async def record_transport_position(self, **kwargs):
        self.journey_calls.append(("position", kwargs))
        if self.journey_outcome != "ok":
            return (
                None
                if self.journey_outcome == "missing"
                else self.journey_outcome
            )
        return self._journey_receipt("in_transit")

    async def list_active_transports(self):
        return [
            {
                "id": TRANSPORT_ID,
                "kind": "mule",
                "status": "in_transit",
                "origin_name": "Acopio La Feria",
                "origin_municipality": "Bucaramanga",
                "origin_latitude": 7.11,
                "origin_longitude": -73.12,
                "destination_name": "Receptor Santander",
                "destination_municipality": "El Playón",
                "destination_latitude": 7.47,
                "destination_longitude": -73.2,
                "supplies_summary": "Agua",
                "tractor_plate": "ABC123",
                "trailer_plate": "R99881",
                "vehicle_visible_characteristics": "Blanco, franja azul",
                "departed_at": CREATED_AT,
                "arrived_at": None,
                "last_latitude": 7.2,
                "last_longitude": -73.15,
                "last_position_at": CREATED_AT,
                "created_at": CREATED_AT,
                "trail": [
                    {
                        "latitude": 7.11,
                        "longitude": -73.12,
                        "recorded_at": CREATED_AT,
                    },
                    {
                        "latitude": 7.2,
                        "longitude": -73.15,
                        "recorded_at": CREATED_AT,
                    },
                ],
            }
        ]


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


# --- CHG-171: contrato de La Mulera + rastreo GPS --------------------


@pytest.mark.anyio
async def test_transport_persists_driver_and_vehicle_normalized():
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
    call = repository.calls[0]
    assert call["driver_full_name"] == "Pedro Antonio Rojas"
    assert call["driver_document_type"] == "Cédula de ciudadanía"
    # §30/§59: los sensibles llegan cifrados al repositorio, jamás en
    # claro.
    assert isinstance(call["driver_document_number_encrypted"], bytes)
    assert b"1098765432" not in call["driver_document_number_encrypted"]
    assert isinstance(call["driver_phone_encrypted"], bytes)
    # §32-33: placas normalizadas (mayúsculas, sin espacios/guiones).
    assert call["tractor_plate"] == "ABC123"
    assert call["trailer_plate"] == "R99881"
    assert "franja azul" in call["vehicle_visible_characteristics"]


@pytest.mark.anyio
async def test_transport_rejects_incomplete_driver_or_vehicle():
    repository = FakeTransportsRepository()
    app = transports_app(repository)

    without_document = await request_app(
        app,
        "POST",
        "/internal/v1/humanitarian-transports",
        headers=AUTH_HEADERS,
        json={
            key: value
            for key, value in TRANSPORT_BODY.items()
            if key != "driverDocumentNumber"
        },
    )
    without_trailer = await request_app(
        app,
        "POST",
        "/internal/v1/humanitarian-transports",
        headers=AUTH_HEADERS,
        json={
            key: value
            for key, value in TRANSPORT_BODY.items()
            if key != "trailerPlate"
        },
    )
    bad_document_type = await request_app(
        app,
        "POST",
        "/internal/v1/humanitarian-transports",
        headers=AUTH_HEADERS,
        json={**TRANSPORT_BODY, "driverDocumentType": "Sin documento"},
    )

    assert without_document.status_code == 422
    assert without_trailer.status_code == 422
    assert bad_document_type.status_code == 422
    assert repository.calls == []


@pytest.mark.anyio
async def test_transport_cities_catalog_is_listed():
    app = transports_app()

    response = await request_app(app, "GET", "/internal/v1/transport-cities")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["items"][0] == {
        "name": "Bucaramanga",
        "department": "Santander",
    }


def journey_path(action: str) -> str:
    return (
        f"/internal/v1/me/humanitarian-transports/{TRANSPORT_ID}/{action}"
    )


@pytest.mark.anyio
async def test_journey_start_and_arrive_move_the_status():
    repository = FakeTransportsRepository()
    app = transports_app(repository)

    started = await request_app(
        app, "POST", journey_path("start"), headers=AUTH_HEADERS
    )
    arrived = await request_app(
        app, "POST", journey_path("arrive"), headers=AUTH_HEADERS
    )

    assert started.status_code == 200
    assert started.json()["status"] == "in_transit"
    assert started.json()["departedAt"] is not None
    assert arrived.status_code == 200
    assert arrived.json()["status"] == "arrived"
    assert [name for name, _ in repository.journey_calls] == [
        "start",
        "arrive",
    ]


@pytest.mark.anyio
async def test_journey_positions_require_account_and_valid_coords():
    repository = FakeTransportsRepository()
    app = transports_app(repository)

    anonymous = await request_app(
        app,
        "POST",
        journey_path("positions"),
        headers={"X-Actor-Kind": "anonymous"},
        json={"latitude": 7.2, "longitude": -73.15},
    )
    invalid = await request_app(
        app,
        "POST",
        journey_path("positions"),
        headers=AUTH_HEADERS,
        json={"latitude": 120, "longitude": -73.15},
    )
    valid = await request_app(
        app,
        "POST",
        journey_path("positions"),
        headers=AUTH_HEADERS,
        json={"latitude": 7.2, "longitude": -73.15},
    )

    assert anonymous.status_code == 401
    assert invalid.status_code == 422
    assert valid.status_code == 200
    action, kwargs = repository.journey_calls[-1]
    assert action == "position"
    assert kwargs["latitude"] == 7.2
    assert kwargs["account_id"] == ACCOUNT_ID


@pytest.mark.anyio
async def test_journey_owner_and_status_rules_are_enforced():
    not_owner = transports_app(
        FakeTransportsRepository(journey_outcome="not_owner")
    )
    wrong_status = transports_app(
        FakeTransportsRepository(journey_outcome="wrong_status")
    )
    missing = transports_app(
        FakeTransportsRepository(journey_outcome="missing")
    )

    forbidden = await request_app(
        not_owner, "POST", journey_path("start"), headers=AUTH_HEADERS
    )
    conflict = await request_app(
        wrong_status, "POST", journey_path("arrive"), headers=AUTH_HEADERS
    )
    absent = await request_app(
        missing, "POST", journey_path("start"), headers=AUTH_HEADERS
    )

    assert forbidden.status_code == 403
    assert conflict.status_code == 409
    assert absent.status_code == 404


@pytest.mark.anyio
async def test_active_transports_feed_never_leaks_driver_data():
    app = transports_app()

    response = await request_app(
        app, "GET", "/internal/v1/humanitarian-transports/active"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    trip = body["items"][0]
    assert trip["status"] == "in_transit"
    assert trip["originName"] == "Acopio La Feria"
    assert trip["lastLatitude"] == 7.2
    assert len(trip["trail"]) == 2
    # §30: nada del conductor sale por el feed público.
    serialized = str(body)
    assert "driver" not in serialized
    assert "1098765432" not in serialized
