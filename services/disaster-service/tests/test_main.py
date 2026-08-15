from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from pydantic import ValidationError

from app.main import create_app, protect_missing_person
from app.models import (
    DisasterEvent,
    HumanImpactSummary,
    OperationalMapPoint,
    PersonRecord,
    SourceReference,
)


def map_point(**overrides) -> OperationalMapPoint:
    values = {
        "id": UUID("44444444-4444-4444-8444-444444444401"),
        "category": "collection_center",
        "title": "Centro de acopio — Coliseo Bicentenario",
        "location_label": "Coliseo Bicentenario, Bucaramanga",
        "latitude": 7.1069,
        "longitude": -73.1289,
        "coordinate_precision": "exact",
        "verification_status": "verified",
        "related_disaster_id": None,
        "description": None,
        "source": SourceReference(
            name="UNGRD",
            source_type="official",
            url=None,
        ),
        "updated_at": datetime(2026, 8, 12, 14, 30, tzinfo=UTC),
    }
    values.update(overrides)
    return OperationalMapPoint(**values)


class FakeRepository:
    def __init__(self, ready: bool = True, map_points=None):
        self.ready = ready
        self.last_filters = None
        self.map_points = map_points or []
        self.map_classification = "demonstrative"
        self.last_map_limit = None

    async def ping(self) -> bool:
        return self.ready

    async def list_events(
        self,
        disaster_type,
        verification_status,
        limit,
        offset,
    ):
        self.last_filters = {
            "disaster_type": disaster_type,
            "verification_status": verification_status,
            "limit": limit,
            "offset": offset,
        }
        event = DisasterEvent(
            id=UUID("4a96f444-0ad3-49e2-93a2-762528b0e282"),
            title="Evento de prueba",
            description=None,
            disaster_type="flood",
            severity=None,
            verification_status="verified",
            latitude=7.1193,
            longitude=-73.1227,
            occurred_at=None,
            updated_at=datetime(2026, 8, 12, tzinfo=UTC),
            source=SourceReference(
                name="Fuente de prueba",
                source_type="official",
                url="https://example.org/event",
            ),
        )
        return [event], 1

    async def people_overview(self, recent_limit):
        self.last_recent_limit = recent_limit
        summary = HumanImpactSummary(
            missing=5,
            reported_deceased=2,
            confirmed_alive=3,
            confirmed_deceased=2,
        )
        person = PersonRecord(
            id=UUID("9c1f6f6e-3f6a-4a6e-8f9d-2a1b3c4d5e6f"),
            display_name="Persona demo 01 — M.R.",
            status="missing",
            location="Café Madrid, Bucaramanga",
            related_event="Inundación en el norte de Bucaramanga",
            created_at=datetime(2026, 8, 12, 10, 30, tzinfo=UTC),
            source=SourceReference(
                name="Reporte ciudadano — plataforma CUSOL",
                source_type="citizen",
                url=None,
            ),
        )
        return summary, [person]

    async def operational_map_overview(self, limit):
        self.last_map_limit = limit
        return self.map_points[:limit], self.map_classification


async def get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get(path)


@pytest.mark.anyio
async def test_liveness():
    app = create_app(repository=FakeRepository())

    response = await get(app, "/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "disaster-service",
    }


@pytest.mark.anyio
async def test_readiness_reports_database_state():
    app = create_app(repository=FakeRepository(ready=False))

    response = await get(app, "/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


@pytest.mark.anyio
async def test_list_disasters_uses_filters_and_camel_case_contract():
    repository = FakeRepository()
    app = create_app(repository=repository)

    response = await get(
        app,
        "/internal/v1/disasters"
        "?disasterType=flood"
        "&verificationStatus=verified"
        "&limit=20",
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["disasterType"] == "flood"
    assert response.json()["items"][0]["source"]["sourceType"] == "official"
    assert repository.last_filters == {
        "disaster_type": "flood",
        "verification_status": "verified",
        "limit": 20,
        "offset": 0,
    }


@pytest.mark.anyio
async def test_rejects_invalid_pagination():
    app = create_app(repository=FakeRepository())

    response = await get(app, "/internal/v1/disasters?limit=101")

    assert response.status_code == 422


@pytest.mark.anyio
async def test_people_overview_returns_camel_case_contract():
    repository = FakeRepository()
    app = create_app(repository=repository)

    response = await get(
        app, "/internal/v1/people/overview?recentLimit=15"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "missing": 5,
        "reportedDeceased": 2,
        "confirmedAlive": 3,
        "confirmedDeceased": 2,
    }
    assert body["recentPeople"][0]["displayName"] == (
        "Persona demo 01 — M.R."
    )
    assert body["recentPeople"][0]["status"] == "missing"
    assert body["recentPeople"][0]["source"]["sourceType"] == "citizen"
    assert "generatedAt" in body
    assert repository.last_recent_limit == 15


@pytest.mark.anyio
async def test_people_overview_rejects_out_of_range_limit():
    app = create_app(repository=FakeRepository())

    below = await get(app, "/internal/v1/people/overview?recentLimit=9")
    above = await get(app, "/internal/v1/people/overview?recentLimit=51")

    assert below.status_code == 422
    assert above.status_code == 422


FIVE_CATEGORY_POINTS = [
    map_point(
        id=UUID("44444444-4444-4444-8444-444444444405"),
        category="building_pending",
        title="Edificio sin inspección registrada — sector comercial",
        location_label="Centro, Bucaramanga",
        coordinate_precision="approximate",
        verification_status="unverified",
        updated_at=datetime(2026, 8, 12, 16, 0, tzinfo=UTC),
    ),
    map_point(
        id=UUID("44444444-4444-4444-8444-444444444404"),
        category="rubble_pending",
        title="Escombros por revisar — bodega colapsada",
        coordinate_precision="approximate",
        verification_status="unverified",
        updated_at=datetime(2026, 8, 12, 15, 30, tzinfo=UTC),
    ),
    map_point(
        id=UUID("44444444-4444-4444-8444-444444444403"),
        category="rubble_reviewed",
        title="Revisión registrada — ribera norte",
        updated_at=datetime(2026, 8, 12, 15, 0, tzinfo=UTC),
    ),
    map_point(
        id=UUID("44444444-4444-4444-8444-444444444402"),
        category="collection_center",
        updated_at=datetime(2026, 8, 12, 14, 30, tzinfo=UTC),
    ),
    map_point(
        id=UUID("44444444-4444-4444-8444-444444444401"),
        category="missing_person",
        title="Zona de búsqueda — sector Café Madrid",
        coordinate_precision="approximate",
        verification_status="under_review",
        related_disaster_id=UUID(
            "22222222-2222-4222-8222-222222222201"
        ),
        updated_at=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
    ),
]


@pytest.mark.anyio
async def test_operational_map_contract_summary_and_order():
    repository = FakeRepository(map_points=FIVE_CATEGORY_POINTS)
    app = create_app(repository=repository)

    response = await get(app, "/internal/v1/operational-map/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "missingPerson": 1,
        "collectionCenter": 1,
        "rubbleReviewed": 1,
        "rubblePending": 1,
        "buildingPending": 1,
        "collectionPoint": 0,
        "communityMeal": 0,
        "temporaryShelter": 0,
    }
    assert body["dataClassification"] == "demonstrative"
    assert "generatedAt" in body
    updated = [item["updatedAt"] for item in body["items"]]
    assert updated == sorted(updated, reverse=True)
    first = body["items"][0]
    assert first["category"] == "building_pending"
    assert first["locationLabel"] == "Centro, Bucaramanga"
    assert first["coordinatePrecision"] == "approximate"
    assert first["verificationStatus"] == "unverified"
    assert first["relatedDisasterId"] is None
    assert first["source"]["sourceType"] == "official"
    assert repository.last_map_limit == 200


@pytest.mark.anyio
async def test_operational_map_empty_set_is_valid():
    app = create_app(repository=FakeRepository(map_points=[]))

    response = await get(app, "/internal/v1/operational-map/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["summary"] == {
        "missingPerson": 0,
        "collectionCenter": 0,
        "rubbleReviewed": 0,
        "rubblePending": 0,
        "buildingPending": 0,
        "collectionPoint": 0,
        "communityMeal": 0,
        "temporaryShelter": 0,
    }
    assert body["dataClassification"] == "demonstrative"


@pytest.mark.anyio
async def test_operational_map_limit_bounds():
    repository = FakeRepository(map_points=FIVE_CATEGORY_POINTS)
    app = create_app(repository=repository)

    minimum = await get(
        app, "/internal/v1/operational-map/overview?limit=1"
    )
    maximum = await get(
        app, "/internal/v1/operational-map/overview?limit=500"
    )
    below = await get(
        app, "/internal/v1/operational-map/overview?limit=0"
    )
    above = await get(
        app, "/internal/v1/operational-map/overview?limit=501"
    )

    assert minimum.status_code == 200
    assert len(minimum.json()["items"]) == 1
    # El resumen se calcula sobre el mismo subconjunto devuelto tras
    # aplicar limit: solo queda el edificio más reciente.
    assert minimum.json()["summary"] == {
        "missingPerson": 0,
        "collectionCenter": 0,
        "rubbleReviewed": 0,
        "rubblePending": 0,
        "buildingPending": 1,
        "collectionPoint": 0,
        "communityMeal": 0,
        "temporaryShelter": 0,
    }
    assert maximum.status_code == 200
    assert below.status_code == 422
    assert above.status_code == 422


@pytest.mark.anyio
async def test_missing_person_never_exposes_exact_precision():
    stored_with_exact = map_point(
        category="missing_person",
        title="Zona de búsqueda — dato incorrecto en base",
        coordinate_precision="exact",
        latitude=7.123456,
        longitude=-73.123456,
        verification_status="under_review",
    )
    app = create_app(
        repository=FakeRepository(map_points=[stored_with_exact])
    )

    response = await get(app, "/internal/v1/operational-map/overview")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["coordinatePrecision"] == "approximate"
    assert item["latitude"] == 7.12
    assert item["longitude"] == -73.12
    identity_fields = {
        "displayName", "name", "document", "phone", "contact"
    }
    assert identity_fields.isdisjoint(item.keys())


def test_protect_missing_person_keeps_other_categories_intact():
    exact_center = map_point(category="collection_center")

    assert protect_missing_person(exact_center) is exact_center


def test_out_of_range_coordinates_are_rejected_deterministically():
    with pytest.raises(ValidationError):
        map_point(latitude=91.0)
    with pytest.raises(ValidationError):
        map_point(longitude=-181.0)


@pytest.mark.anyio
async def test_operational_map_without_buildings_reports_zero():
    no_buildings = [
        point
        for point in FIVE_CATEGORY_POINTS
        if point.category != "building_pending"
    ]
    app = create_app(repository=FakeRepository(map_points=no_buildings))

    response = await get(app, "/internal/v1/operational-map/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["buildingPending"] == 0
    categories = {item["category"] for item in body["items"]}
    assert "building_pending" not in categories


@pytest.mark.anyio
async def test_building_pending_serializes_only_public_contract_fields():
    building = map_point(
        category="building_pending",
        title="Edificio sin inspección registrada — sector comercial",
        location_label="Centro, Bucaramanga",
        coordinate_precision="approximate",
        verification_status="unverified",
    )
    app = create_app(repository=FakeRepository(map_points=[building]))

    response = await get(app, "/internal/v1/operational-map/overview")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["category"] == "building_pending"
    # Solo los campos públicos del contrato: sin identidad, propiedad,
    # residentes ni diagnóstico estructural.
    assert set(item.keys()) == {
        "id", "category", "title", "locationLabel", "latitude",
        "longitude", "coordinatePrecision", "verificationStatus",
        "relatedDisasterId", "description", "source", "updatedAt",
    }
    forbidden = {
        "residents", "owner", "ownerName", "address", "structuralRisk",
        "unsafe", "collapsed", "occupants",
    }
    assert forbidden.isdisjoint(item.keys())
    # Conserva fuente, estado de verificación, precisión y actualización.
    assert item["source"]["sourceType"] == "official"
    assert item["verificationStatus"] == "unverified"
    assert item["coordinatePrecision"] == "approximate"
    assert item["updatedAt"].startswith("2026-08-12")


def test_protect_missing_person_keeps_building_pending_intact():
    building = map_point(
        category="building_pending",
        coordinate_precision="exact",
        latitude=7.123456,
        longitude=-73.123456,
    )

    assert protect_missing_person(building) is building
