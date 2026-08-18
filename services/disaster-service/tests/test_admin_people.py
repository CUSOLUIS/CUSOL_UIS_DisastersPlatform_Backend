"""CHG-154 — Gestión admin de registros de personas (disaster-service).

Defensa del rol, filtros del listado, edición acotada (estado bloqueado
con caso vinculado), ocultamiento reversible con actor y restauración.
"""

import base64
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.main import create_app

from test_missing_persons import FakeStorage, request_app

ACTOR_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
PERSON_ID = UUID("99999999-9999-4999-8999-999999999901")
CASE_ID = UUID("99999999-9999-4999-8999-999999999902")
CREATED_AT = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)

ADMIN_HEADERS = {
    "X-Actor-Role": "super_admin",
    "X-Actor-Account-Id": str(ACTOR_ID),
    "X-Actor-Display": base64.b64encode("Admin CUSOL".encode()).decode(),
}


def person_row(**overrides) -> dict:
    row = {
        "id": PERSON_ID,
        "display_name": "Marina Rueda",
        "status": "missing",
        "location": "Bucaramanga, Santander",
        "related_event": "Deslizamiento Mesa de los Santos",
        "latitude": 7.1,
        "longitude": -73.1,
        "missing_person_case_id": CASE_ID,
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
        "hidden_at": None,
        "hidden_by": None,
        "source_name": "Reporte ciudadano",
        "source_type": "citizen",
        "source_url": None,
    }
    row.update(overrides)
    return row


class FakePeopleAdminRepository:
    def __init__(self, row: dict | None = None):
        self.row = row if row is not None else person_row()
        self.list_args: dict = {}
        self.update_args: dict = {}
        self.hide_args: dict = {}
        self.restored_with: UUID | None = None

    async def admin_list_people(
        self, statuses, search, visibility, limit, offset
    ):
        self.list_args = {
            "statuses": statuses,
            "search": search,
            "visibility": visibility,
            "limit": limit,
            "offset": offset,
        }
        return [self.row], 1

    async def admin_update_person(self, person_id, fields):
        self.update_args = {"person_id": person_id, "fields": fields}
        if self.row is None:
            return None
        if (
            fields.get("status") is not None
            and self.row["missing_person_case_id"] is not None
        ):
            return "status_locked"
        return {**self.row, **fields}

    async def admin_hide_person(self, person_id, hidden_by):
        self.hide_args = {"person_id": person_id, "hidden_by": hidden_by}
        if self.row is None:
            return None
        return {
            **self.row,
            "hidden_at": CREATED_AT,
            "hidden_by": hidden_by,
        }

    async def admin_restore_person(self, person_id):
        self.restored_with = person_id
        if self.row is None:
            return None
        return {**self.row, "hidden_at": None, "hidden_by": None}


def people_app(repository=None):
    return create_app(
        repository=repository or FakePeopleAdminRepository(),
        storage=FakeStorage(),
    )


@pytest.mark.anyio
async def test_people_routes_reject_missing_or_insufficient_role():
    app = people_app()

    without = await request_app(app, "GET", "/internal/v1/admin/people")
    as_user = await request_app(
        app,
        "GET",
        "/internal/v1/admin/people",
        headers={**ADMIN_HEADERS, "X-Actor-Role": "user"},
    )
    hide_as_moderator = await request_app(
        app,
        "POST",
        f"/internal/v1/admin/people/{PERSON_ID}/hide",
        headers={**ADMIN_HEADERS, "X-Actor-Role": "moderator"},
    )

    assert without.status_code == 403
    assert as_user.status_code == 403
    assert hide_as_moderator.status_code == 403


@pytest.mark.anyio
async def test_list_people_passes_filters_and_maps_rows():
    repository = FakePeopleAdminRepository()
    app = people_app(repository)

    response = await request_app(
        app,
        "GET",
        "/internal/v1/admin/people"
        "?statuses=missing&statuses=confirmed_alive"
        "&q=marina&visibility=hidden&limit=10&offset=20",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert repository.list_args == {
        "statuses": ["missing", "confirmed_alive"],
        "search": "marina",
        "visibility": "hidden",
        "limit": 10,
        "offset": 20,
    }
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["displayName"] == "Marina Rueda"
    assert item["hasLinkedCase"] is True
    assert item["source"]["sourceType"] == "citizen"
    assert item["hiddenAt"] is None


@pytest.mark.anyio
async def test_update_person_validates_and_respects_status_lock():
    repository = FakePeopleAdminRepository()
    app = people_app(repository)

    edited = await request_app(
        app,
        "PATCH",
        f"/internal/v1/admin/people/{PERSON_ID}",
        headers=ADMIN_HEADERS,
        json={"displayName": "Marina R.", "location": "Girón, Santander"},
    )
    assert edited.status_code == 200
    assert edited.json()["displayName"] == "Marina R."
    assert repository.update_args["fields"] == {
        "display_name": "Marina R.",
        "location": "Girón, Santander",
    }

    empty = await request_app(
        app,
        "PATCH",
        f"/internal/v1/admin/people/{PERSON_ID}",
        headers=ADMIN_HEADERS,
        json={},
    )
    assert empty.status_code == 422

    unknown_field = await request_app(
        app,
        "PATCH",
        f"/internal/v1/admin/people/{PERSON_ID}",
        headers=ADMIN_HEADERS,
        json={"publicCaseCode": "MP-X"},
    )
    assert unknown_field.status_code == 422

    # Con caso vinculado el estado lo derivan las novedades (CHG-107+).
    locked = await request_app(
        app,
        "PATCH",
        f"/internal/v1/admin/people/{PERSON_ID}",
        headers=ADMIN_HEADERS,
        json={"status": "confirmed_alive"},
    )
    assert locked.status_code == 409

    seed_repository = FakePeopleAdminRepository(
        person_row(missing_person_case_id=None)
    )
    seed_app = people_app(seed_repository)
    seed_edit = await request_app(
        seed_app,
        "PATCH",
        f"/internal/v1/admin/people/{PERSON_ID}",
        headers=ADMIN_HEADERS,
        json={"status": "confirmed_alive"},
    )
    assert seed_edit.status_code == 200
    assert seed_edit.json()["status"] == "confirmed_alive"


@pytest.mark.anyio
async def test_hide_records_actor_and_restore_clears_visibility():
    repository = FakePeopleAdminRepository()
    app = people_app(repository)

    hidden = await request_app(
        app,
        "POST",
        f"/internal/v1/admin/people/{PERSON_ID}/hide",
        headers=ADMIN_HEADERS,
    )
    assert hidden.status_code == 200
    assert hidden.json()["hiddenBy"] == "Admin CUSOL"
    assert hidden.json()["hiddenAt"] is not None
    assert repository.hide_args == {
        "person_id": PERSON_ID,
        "hidden_by": "Admin CUSOL",
    }

    restored = await request_app(
        app,
        "POST",
        f"/internal/v1/admin/people/{PERSON_ID}/restore",
        headers=ADMIN_HEADERS,
    )
    assert restored.status_code == 200
    assert restored.json()["hiddenAt"] is None
    assert repository.restored_with == PERSON_ID


@pytest.mark.anyio
async def test_people_mutations_return_404_for_unknown_record():
    class EmptyRepository(FakePeopleAdminRepository):
        def __init__(self):
            super().__init__()
            self.row = None

    app = people_app(EmptyRepository())

    hidden = await request_app(
        app,
        "POST",
        f"/internal/v1/admin/people/{PERSON_ID}/hide",
        headers=ADMIN_HEADERS,
    )
    restored = await request_app(
        app,
        "POST",
        f"/internal/v1/admin/people/{PERSON_ID}/restore",
        headers=ADMIN_HEADERS,
    )
    edited = await request_app(
        app,
        "PATCH",
        f"/internal/v1/admin/people/{PERSON_ID}",
        headers=ADMIN_HEADERS,
        json={"displayName": "Nadie"},
    )

    assert hidden.status_code == 404
    assert restored.status_code == 404
    assert edited.status_code == 404
