"""CHG-125 — «Necesitamos ayuda»: creación pública (anónima o con
cuenta), listado vigente con conteo de atención, atención idempotente
y fotografía pública. La expiración es del backend (DEC-125-02)."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.main import create_app

from test_missing_persons import (
    FakeStorage,
    make_jpeg,
    photos_form,
    request_app,
)

ACCOUNT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb7"
OTHER_ACCOUNT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb8"
AUTH_HEADERS = {
    "X-Actor-Kind": "authenticated",
    "X-Account-Id": ACCOUNT_ID,
}
IDEMPOTENCY = {"Idempotency-Key": "clave-idempotente-ayuda-01"}
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class FakeHelpRequestRepository:
    """Reproduce el pacto del repositorio real: filtra por vigencia y
    la atención es idempotente por (solicitud, cuenta)."""

    def __init__(self):
        self.rows: dict[UUID, dict] = {}
        self.attenders: set[tuple[UUID, UUID]] = set()
        self.by_key: dict[str, UUID] = {}

    async def ping(self):
        return True

    def _active(self, row: dict) -> bool:
        return row["expires_at"] > datetime.now(UTC)

    def seed(self, *, expired: bool = False, photo: bool = False) -> UUID:
        request_id = uuid4()
        created = datetime.now(UTC) - timedelta(hours=2)
        self.rows[request_id] = {
            "id": request_id,
            "description": "Necesitamos ayuda urgente con rescate.",
            "address": "Calle 10 #5-20, Bucaramanga",
            "latitude": 7.12,
            "longitude": -73.12,
            "created_at": created,
            "expires_at": created
            + timedelta(hours=1 if expired else 24),
            "photo_derived_storage_key": (
                f"help-requests/{request_id}/derived/foto.jpg"
                if photo
                else None
            ),
            "photo_content_type": "image/jpeg" if photo else None,
            "public_code": "HR-2026-SEEDED01",
        }
        return request_id

    async def create_help_request(self, **kwargs):
        key = kwargs["idempotency_key"]
        if key in self.by_key:
            row = self.rows[self.by_key[key]]
            return (
                {
                    "id": row["id"],
                    "public_code": row["public_code"],
                    "created_at": row["created_at"],
                    "expires_at": row["expires_at"],
                },
                False,
            )
        request_id = uuid4()
        created = datetime.now(UTC)
        row = {
            "id": request_id,
            "description": kwargs["description"],
            "address": kwargs["address"],
            "latitude": kwargs["latitude"],
            "longitude": kwargs["longitude"],
            "created_at": created,
            "expires_at": created
            + timedelta(hours=kwargs["duration_hours"]),
            "photo_derived_storage_key": kwargs[
                "photo_derived_storage_key"
            ],
            "photo_content_type": kwargs["photo_content_type"],
            "public_code": kwargs["public_code"],
        }
        self.rows[request_id] = row
        self.by_key[key] = request_id
        return (
            {
                "id": request_id,
                "public_code": row["public_code"],
                "created_at": created,
                "expires_at": row["expires_at"],
            },
            True,
        )

    async def list_active_help_requests(self, limit, offset, account_id):
        active = sorted(
            (row for row in self.rows.values() if self._active(row)),
            key=lambda row: row["created_at"],
            reverse=True,
        )
        page = []
        for row in active[offset : offset + limit]:
            page.append(
                {
                    **row,
                    "attenders_count": sum(
                        1
                        for rid, _aid in self.attenders
                        if rid == row["id"]
                    ),
                    "attended_by_me": account_id is not None
                    and (row["id"], account_id) in self.attenders,
                    "has_photo": row["photo_derived_storage_key"]
                    is not None,
                }
            )
        return page, len(active)

    async def attend_help_request(self, request_id, account_id):
        row = self.rows.get(request_id)
        if row is None or not self._active(row):
            return None
        self.attenders.add((request_id, account_id))
        count = sum(
            1 for rid, _aid in self.attenders if rid == request_id
        )
        return {"id": request_id, "attenders_count": count}

    async def get_help_request_photo(self, request_id):
        row = self.rows.get(request_id)
        if (
            row is None
            or not self._active(row)
            or row["photo_derived_storage_key"] is None
        ):
            return None
        return {
            "object_key": row["photo_derived_storage_key"],
            "content_type": row["photo_content_type"],
        }


def help_app(repository=None, storage=None):
    return create_app(
        repository=repository or FakeHelpRequestRepository(),
        storage=storage if storage is not None else FakeStorage(),
    )


def valid_payload(**overrides):
    payload = {
        "description": (
            "Necesitamos agua potable y frazadas para veinte familias."
        ),
        "address": "Calle 10 #5-20, Bucaramanga",
        "latitude": 7.12,
        "longitude": -73.12,
        "durationHours": 6,
    }
    payload.update(overrides)
    return payload


async def post_help_request(app, payload=None, photos=0, headers=None):
    return await request_app(
        app,
        "POST",
        "/internal/v1/help-requests",
        data={
            "payload": json.dumps(
                payload if payload is not None else valid_payload()
            )
        },
        files=photos_form(photos) if photos else None,
        headers={**IDEMPOTENCY, **(headers or {})},
    )


# --- Creación ---


@pytest.mark.anyio
async def test_create_requires_idempotency_key():
    app = help_app()
    response = await request_app(
        app,
        "POST",
        "/internal/v1/help-requests",
        data={"payload": json.dumps(valid_payload())},
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_create_anonymous_without_photo():
    repository = FakeHelpRequestRepository()
    app = help_app(repository=repository)

    response = await post_help_request(app)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "active"
    assert body["publicCode"].startswith("HR-")
    received = datetime.fromisoformat(body["receivedAt"])
    expires = datetime.fromisoformat(body["expiresAt"])
    assert expires - received == timedelta(hours=6)
    row = next(iter(repository.rows.values()))
    assert row["photo_derived_storage_key"] is None


# CHG-127 — la dirección escrita basta: sin coordenadas se crea igual y
# el listado las proyecta como null (sin marcador en el mapa).
@pytest.mark.anyio
async def test_create_without_coordinates():
    repository = FakeHelpRequestRepository()
    app = help_app(repository=repository)

    payload = valid_payload()
    del payload["latitude"]
    del payload["longitude"]
    response = await post_help_request(app, payload=payload)

    assert response.status_code == 201
    row = next(iter(repository.rows.values()))
    assert row["latitude"] is None
    assert row["longitude"] is None

    listing = await request_app(app, "GET", "/internal/v1/help-requests")
    assert listing.status_code == 200
    item = listing.json()["items"][0]
    assert item["latitude"] is None
    assert item["longitude"] is None


# CHG-127 / DEC-127-01 — el par viaja completo o no viaja.
@pytest.mark.anyio
@pytest.mark.parametrize("kept", ["latitude", "longitude"])
async def test_create_rejects_lone_coordinate(kept):
    app = help_app()

    payload = valid_payload()
    removed = "longitude" if kept == "latitude" else "latitude"
    del payload[removed]
    response = await post_help_request(app, payload=payload)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(
        "application/problem+json"
    )


@pytest.mark.anyio
async def test_create_with_optional_photo_stores_derived_copy():
    repository = FakeHelpRequestRepository()
    storage = FakeStorage()
    app = help_app(repository=repository, storage=storage)

    response = await post_help_request(app, photos=1)

    assert response.status_code == 201
    row = next(iter(repository.rows.values()))
    assert row["photo_derived_storage_key"] is not None
    assert row["photo_derived_storage_key"] in storage.objects
    assert "/derived/" in row["photo_derived_storage_key"]


@pytest.mark.anyio
async def test_create_rejects_second_photo():
    app = help_app()
    response = await post_help_request(app, photos=2)
    assert response.status_code == 422


@pytest.mark.anyio
async def test_create_rejects_junk_description():
    app = help_app()
    response = await post_help_request(
        app,
        payload=valid_payload(
            description="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        ),
    )
    assert response.status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize("hours", [0, 73, -4])
async def test_create_rejects_out_of_range_duration(hours):
    app = help_app()
    response = await post_help_request(
        app, payload=valid_payload(durationHours=hours)
    )
    assert response.status_code == 422
    assert "durationHours" in response.json().get("fields", [])


@pytest.mark.anyio
async def test_create_is_idempotent_and_discards_retry_photos():
    repository = FakeHelpRequestRepository()
    storage = FakeStorage()
    app = help_app(repository=repository, storage=storage)

    first = await post_help_request(app, photos=1)
    second = await post_help_request(app, photos=1)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert len(repository.rows) == 1
    # Los archivos del reintento se limpian: solo quedan los 2 objetos
    # (original + derivado) del primer intento.
    assert len(storage.objects) == 2


@pytest.mark.anyio
async def test_create_links_account_when_authenticated():
    repository = FakeHelpRequestRepository()
    app = help_app(repository=repository)

    captured: dict = {}
    original = repository.create_help_request

    async def spy(**kwargs):
        captured.update(kwargs)
        return await original(**kwargs)

    repository.create_help_request = spy

    response = await post_help_request(app, headers=AUTH_HEADERS)

    assert response.status_code == 201
    assert captured["reporter_account_id"] == UUID(ACCOUNT_ID)


# --- Listado vigente ---


@pytest.mark.anyio
async def test_list_excludes_expired_requests():
    repository = FakeHelpRequestRepository()
    active_id = repository.seed()
    repository.seed(expired=True)
    app = help_app(repository=repository)

    response = await request_app(
        app, "GET", "/internal/v1/help-requests"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(active_id)
    assert body["items"][0]["attendedByMe"] is False


@pytest.mark.anyio
async def test_list_reports_attention_and_photo_url():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed(photo=True)
    repository.attenders.add((request_id, UUID(OTHER_ACCOUNT_ID)))
    repository.attenders.add((request_id, UUID(ACCOUNT_ID)))
    app = help_app(repository=repository)

    response = await request_app(
        app, "GET", "/internal/v1/help-requests", headers=AUTH_HEADERS
    )

    item = response.json()["items"][0]
    assert item["attendersCount"] == 2
    assert item["attendedByMe"] is True
    assert item["photoUrl"] == (
        f"/api/v1/public/help-requests/{request_id}/photo"
    )


@pytest.mark.anyio
async def test_list_rejects_unknown_page_size():
    app = help_app()
    response = await request_app(
        app, "GET", "/internal/v1/help-requests", params={"limit": 7}
    )
    assert response.status_code == 422


# --- Atención ---


@pytest.mark.anyio
async def test_attend_requires_account():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed()
    app = help_app(repository=repository)

    response = await request_app(
        app, "POST", f"/internal/v1/help-requests/{request_id}/attend"
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_attend_is_idempotent_per_account():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed()
    app = help_app(repository=repository)

    first = await request_app(
        app,
        "POST",
        f"/internal/v1/help-requests/{request_id}/attend",
        headers=AUTH_HEADERS,
    )
    second = await request_app(
        app,
        "POST",
        f"/internal/v1/help-requests/{request_id}/attend",
        headers=AUTH_HEADERS,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["attendersCount"] == 1
    assert second.json()["attendersCount"] == 1
    assert second.json()["attending"] is True


@pytest.mark.anyio
async def test_attend_counts_distinct_accounts():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed()
    app = help_app(repository=repository)

    await request_app(
        app,
        "POST",
        f"/internal/v1/help-requests/{request_id}/attend",
        headers=AUTH_HEADERS,
    )
    second = await request_app(
        app,
        "POST",
        f"/internal/v1/help-requests/{request_id}/attend",
        headers={
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": OTHER_ACCOUNT_ID,
        },
    )

    assert second.json()["attendersCount"] == 2


@pytest.mark.anyio
async def test_attend_expired_request_is_not_found():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed(expired=True)
    app = help_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        f"/internal/v1/help-requests/{request_id}/attend",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 404


# --- Fotografía pública ---


@pytest.mark.anyio
async def test_photo_not_found_without_photo_or_expired():
    repository = FakeHelpRequestRepository()
    without_photo = repository.seed()
    expired = repository.seed(expired=True, photo=True)
    app = help_app(repository=repository)

    for request_id in (without_photo, expired):
        response = await request_app(
            app,
            "GET",
            f"/internal/v1/public/help-requests/{request_id}/photo",
        )
        assert response.status_code == 404


@pytest.mark.anyio
async def test_photo_served_with_content_type():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed(photo=True)
    storage = FakeStorage()
    storage.objects[
        f"help-requests/{request_id}/derived/foto.jpg"
    ] = make_jpeg()
    app = help_app(repository=repository, storage=storage)

    response = await request_app(
        app,
        "GET",
        f"/internal/v1/public/help-requests/{request_id}/photo",
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "public, max-age=300"
