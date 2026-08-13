"""Pruebas CHG-018 — tabla paginada de personas publicables."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest

from app.main import create_app
from app.models import PersonRecord, SourceReference


def person(index: int, status: str = "missing") -> PersonRecord:
    return PersonRecord(
        id=UUID(int=index + 1),
        display_name=f"Persona demo {index:04d} — A.B.",
        status=status,
        location="Bogotá, D.C.",
        related_event="Inundación en el norte de Bucaramanga",
        created_at=datetime(2026, 8, 9, tzinfo=UTC)
        + timedelta(minutes=3 * index),
        source=SourceReference(
            name="Reporte ciudadano — plataforma CUSOL",
            source_type="citizen",
            url=None,
        ),
    )


class FakeRecordsRepository:
    def __init__(self, items=None, total: int = 0):
        self.items = items if items is not None else []
        self.total = total
        self.last_query = None

    async def ping(self) -> bool:
        return True

    async def list_people_records(
        self, statuses, search, limit, offset
    ):
        self.last_query = {
            "statuses": statuses,
            "search": search,
            "limit": limit,
            "offset": offset,
        }
        return self.items[:limit], self.total

async def get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get(path)


@pytest.mark.anyio
async def test_records_page_serializes_only_public_contract():
    repository = FakeRecordsRepository(
        items=[person(0), person(1, "confirmed_alive")], total=2012
    )
    app = create_app(repository=repository)

    response = await get(
        app, "/internal/v1/people/records?limit=10&offset=20"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2012
    assert body["limit"] == 10
    assert body["offset"] == 20
    assert "generatedAt" in body
    assert repository.last_query == {
        "statuses": None,
        "search": None,
        "limit": 10,
        "offset": 20,
    }
    item = body["items"][0]
    assert set(item.keys()) == {
        "id", "displayName", "status", "location", "relatedEvent",
        "source", "createdAt",
    }
    forbidden = {
        "documentNumber", "document", "phone", "contact", "email",
        "medicalInformation", "latitude", "longitude", "photo",
        "sourceId", "personId",
    }
    assert forbidden.isdisjoint(item.keys())
    assert set(item["source"].keys()) == {"name", "sourceType", "url"}


@pytest.mark.anyio
async def test_records_deduplicates_statuses_and_strips_search():
    repository = FakeRecordsRepository(total=0)
    app = create_app(repository=repository)

    response = await get(
        app,
        "/internal/v1/people/records"
        "?statuses=missing&statuses=missing&statuses=confirmed_alive"
        "&q=%20bogota%20",
    )

    assert response.status_code == 200
    assert repository.last_query["statuses"] == [
        "missing", "confirmed_alive"
    ]
    assert repository.last_query["search"] == "bogota"


@pytest.mark.anyio
async def test_records_empty_search_means_no_filter():
    repository = FakeRecordsRepository(total=5)
    app = create_app(repository=repository)

    response = await get(app, "/internal/v1/people/records?q=")

    assert response.status_code == 200
    assert repository.last_query["search"] is None


@pytest.mark.anyio
async def test_records_empty_result_is_valid():
    app = create_app(repository=FakeRecordsRepository(total=0))

    response = await get(
        app, "/internal/v1/people/records?statuses=missing&q=nadie"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.anyio
async def test_records_invalid_parameters_return_422():
    app = create_app(repository=FakeRecordsRepository())
    base = "/internal/v1/people/records"

    for path in (
        f"{base}?limit=0",
        f"{base}?limit=11",
        f"{base}?limit=51",
        f"{base}?offset=-1",
        f"{base}?q=a",
        f"{base}?q=%20a%20",
        f"{base}?statuses=desconocido",
        f"{base}?q=" + "x" * 101,
    ):
        response = await get(app, path)
        assert response.status_code == 422, path
