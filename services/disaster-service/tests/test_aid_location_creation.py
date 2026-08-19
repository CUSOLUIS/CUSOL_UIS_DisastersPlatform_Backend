"""CHG-153 (F2) / CHG-161 (F2) — Alta de puntos logísticos.

Cubre lo que el expediente CHG-153 dejó anotado como pendiente —las
pruebas del endpoint de alta, camino feliz y errores— y el refuerzo
server-side del portón de sesión de CHG-161: el acopio local y el punto
de distribución no admiten alta anónima aunque alguien salte el
formulario.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.main import create_app
from app.models import AID_LOCATION_KINDS_REQUIRING_ACCOUNT

from test_missing_persons import FakeStorage, request_app

PATH = "/internal/v1/aid-locations"
ACCOUNT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
PARENT_ID = UUID("88888888-8888-4888-8888-888888888801")
CREATED_AT = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
IDEMPOTENCY = {"Idempotency-Key": "clave-idempotente-153-alta"}
ANONYMOUS = {"X-Actor-Kind": "anonymous"}
AUTHENTICATED = {
    "X-Actor-Kind": "authenticated",
    "X-Account-Id": str(ACCOUNT_ID),
}


class FakeCreationRepository:
    """Guarda la llamada; el resultado se decide en cada prueba."""

    def __init__(self, result: str | None = None):
        self.calls: list[dict] = []
        self.result = result

    async def create_aid_location(self, **kwargs):
        self.calls.append(kwargs)
        if self.result is not None:
            return self.result
        return (
            {
                "id": uuid4(),
                "kind": kwargs["kind"],
                "operational_status": kwargs["operational_status"],
                "created_at": CREATED_AT,
            },
            True,
        )


def creation_app(repository=None):
    return create_app(
        repository=repository or FakeCreationRepository(),
        storage=FakeStorage(),
    )


def draft(**overrides) -> dict:
    body = {
        "kind": "receiver_center",
        "name": "  Acopio Receptor Norte  ",
        "address": "  Calle 10 # 5-51  ",
        "municipality": " Bucaramanga ",
        "department": " Santander ",
        "latitude": 7.1,
        "longitude": -73.1,
    }
    body.update(overrides)
    return body


@pytest.mark.anyio
async def test_anonymous_creates_independent_point_and_trims_fields():
    repository = FakeCreationRepository()
    app = creation_app(repository)

    response = await request_app(
        app,
        "POST",
        PATH,
        headers={**IDEMPOTENCY, **ANONYMOUS},
        json=draft(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "receiver_center"
    assert body["operationalStatus"] == "open"
    call = repository.calls[0]
    assert call["name"] == "Acopio Receptor Norte"
    assert call["location_label"] == "Calle 10 # 5-51"
    assert call["municipality"] == "Bucaramanga"
    assert call["created_by_account_id"] is None
    assert call["idempotency_key"] == IDEMPOTENCY["Idempotency-Key"]


@pytest.mark.anyio
async def test_anonymous_creates_dependent_collection_point():
    repository = FakeCreationRepository()
    app = creation_app(repository)

    response = await request_app(
        app,
        "POST",
        PATH,
        headers={**IDEMPOTENCY, **ANONYMOUS},
        json=draft(kind="collection_point", parentId=str(PARENT_ID)),
    )

    assert response.status_code == 201
    assert repository.calls[0]["parent_id"] == PARENT_ID


@pytest.mark.parametrize("kind", sorted(AID_LOCATION_KINDS_REQUIRING_ACCOUNT))
@pytest.mark.anyio
async def test_kinds_requiring_account_reject_anonymous(kind):
    repository = FakeCreationRepository()
    app = creation_app(repository)
    body = draft(kind=kind)
    if kind == "distribution_point":
        body["parentId"] = str(PARENT_ID)

    response = await request_app(
        app,
        "POST",
        PATH,
        headers={**IDEMPOTENCY, **ANONYMOUS},
        json=body,
    )

    assert response.status_code == 401
    assert response.json()["title"] == "Sesión requerida"
    # Nada tocó la base: la regla corta antes de escribir.
    assert repository.calls == []


@pytest.mark.anyio
async def test_collection_center_accepts_authenticated_account():
    repository = FakeCreationRepository()
    app = creation_app(repository)

    response = await request_app(
        app,
        "POST",
        PATH,
        headers={**IDEMPOTENCY, **AUTHENTICATED},
        json=draft(kind="collection_center"),
    )

    assert response.status_code == 201
    assert repository.calls[0]["created_by_account_id"] == ACCOUNT_ID


@pytest.mark.anyio
async def test_distribution_point_accepts_authenticated_account():
    repository = FakeCreationRepository()
    app = creation_app(repository)

    response = await request_app(
        app,
        "POST",
        PATH,
        headers={**IDEMPOTENCY, **AUTHENTICATED},
        json=draft(kind="distribution_point", parentId=str(PARENT_ID)),
    )

    assert response.status_code == 201
    assert repository.calls[0]["kind"] == "distribution_point"


@pytest.mark.anyio
async def test_dependent_kind_without_parent_is_rejected():
    repository = FakeCreationRepository()
    app = creation_app(repository)

    response = await request_app(
        app,
        "POST",
        PATH,
        headers={**IDEMPOTENCY, **ANONYMOUS},
        json=draft(kind="collection_point"),
    )

    assert response.status_code == 422
    assert repository.calls == []


@pytest.mark.anyio
async def test_parent_from_another_city_is_rejected_by_repository():
    repository = FakeCreationRepository(result="parent_other_city")
    app = creation_app(repository)

    response = await request_app(
        app,
        "POST",
        PATH,
        headers={**IDEMPOTENCY, **ANONYMOUS},
        json=draft(kind="collection_point", parentId=str(PARENT_ID)),
    )

    assert response.status_code == 422
    body = response.json()
    assert "misma ciudad" in body["detail"]
    # CHG-114: el formulario resalta el selector del centro asociado.
    assert body["fields"] == ["parentId"]


@pytest.mark.anyio
async def test_half_coordinates_are_rejected():
    repository = FakeCreationRepository()
    app = creation_app(repository)

    response = await request_app(
        app,
        "POST",
        PATH,
        headers={**IDEMPOTENCY, **ANONYMOUS},
        json=draft(longitude=None),
    )

    assert response.status_code == 422
    assert repository.calls == []


@pytest.mark.anyio
async def test_creation_requires_idempotency_key():
    repository = FakeCreationRepository()
    app = creation_app(repository)

    response = await request_app(
        app, "POST", PATH, headers=ANONYMOUS, json=draft()
    )

    assert response.status_code == 422
    assert repository.calls == []
