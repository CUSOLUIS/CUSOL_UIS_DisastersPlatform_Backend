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
        # CHG-193: lo que cada quien consintió compartir al atender.
        self.attender_consent: dict[tuple[UUID, UUID], dict] = {}
        # CHG-148: voluntarios anónimos (una fila por envío).
        self.volunteers: list[dict] = []
        self.by_key: dict[str, UUID] = {}
        # CHG-138: (acción, resultado) de la auditoría admin.
        self.audit: list[tuple[str, str]] = []

    async def ping(self):
        return True

    def _active(self, row: dict) -> bool:
        return row["expires_at"] > datetime.now(UTC)

    def seed(
        self,
        *,
        expired: bool = False,
        photo: bool = False,
        reporter_account_id: UUID | None = None,
    ) -> UUID:
        request_id = uuid4()
        created = datetime.now(UTC) - timedelta(hours=2)
        self.rows[request_id] = {
            "id": request_id,
            # CHG-190: nulo = solicitud creada sin cuenta.
            "reporter_account_id": reporter_account_id,
            "description": "Necesitamos ayuda urgente con rescate.",
            "address": "Calle 10 #5-20, Bucaramanga",
            "latitude": 7.12,
            "longitude": -73.12,
            "notification_radius_km": None,
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
            "notification_radius_km": kwargs["notification_radius_km"],
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

    # CHG-138 — gestión desde la consola: todo, borrar una, vaciar.
    async def admin_list_help_requests(self, limit, offset):
        rows = sorted(
            self.rows.values(),
            key=lambda row: row["created_at"],
            reverse=True,
        )
        page = [
            {
                "id": row["id"],
                "public_code": row["public_code"],
                "description": row["description"],
                "address": row["address"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "notification_radius_km": row.get(
                    "notification_radius_km"
                ),
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "expired": row["expires_at"] <= datetime.now(UTC),
                "attenders_count": self._attenders_count(row["id"]),
                "volunteers_count": sum(
                    1
                    for v in self.volunteers
                    if v["help_request_id"] == row["id"]
                ),
                "has_photo": row["photo_derived_storage_key"]
                is not None,
            }
            for row in rows[offset : offset + limit]
        ]
        return page, len(rows)

    async def admin_list_help_request_volunteers(self, request_id):
        return [
            {
                "id": uuid4(),
                "name_encrypted": v.get("name_encrypted"),
                "phone_encrypted": v.get("phone_encrypted"),
                "email_encrypted": v.get("email_encrypted"),
                "has_photo": v.get("photo_derived_storage_key") is not None,
                "created_at": datetime.now(UTC),
            }
            for v in self.volunteers
            if v["help_request_id"] == request_id
        ]

    async def get_help_request_volunteer_photo(self, volunteer_id):
        return None

    async def admin_delete_help_request(self, request_id):
        row = self.rows.pop(request_id, None)
        if row is None:
            return None
        self.attenders = {
            entry for entry in self.attenders if entry[0] != request_id
        }
        return {
            "photo_storage_key": row.get("photo_storage_key"),
            "photo_derived_storage_key": row.get(
                "photo_derived_storage_key"
            ),
        }

    # CHG-196 — mismo borrado, con la puerta de propiedad.
    async def delete_own_help_request(self, request_id, account_id):
        row = self.rows.get(request_id)
        if row is None or row.get("reporter_account_id") != account_id:
            return None
        self.rows.pop(request_id, None)
        self.attenders = {
            entry for entry in self.attenders if entry[0] != request_id
        }
        self.volunteers = [
            volunteer
            for volunteer in self.volunteers
            if volunteer["help_request_id"] != request_id
        ]
        return {
            "photo_storage_key": row.get("photo_storage_key"),
            "photo_derived_storage_key": row.get(
                "photo_derived_storage_key"
            ),
        }

    async def admin_purge_help_requests(self):
        keys = [
            key
            for row in self.rows.values()
            for key in (
                row.get("photo_storage_key"),
                row.get("photo_derived_storage_key"),
            )
            if key
        ]
        deleted = len(self.rows)
        self.rows.clear()
        self.attenders.clear()
        return deleted, keys

    async def admin_write_audit(self, *args, **kwargs):
        # (acción, resultado) — suficiente para auditar en pruebas.
        self.audit.append((args[2], args[5]))
        return uuid4()

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
                    "attenders_count": self._attenders_count(row["id"]),
                    "attended_by_me": account_id is not None
                    and (row["id"], account_id) in self.attenders,
                    # CHG-190: espejo de la consulta real.
                    "created_by_me": account_id is not None
                    and row.get("reporter_account_id") == account_id,
                    "has_photo": row["photo_derived_storage_key"]
                    is not None,
                }
            )
        return page, len(active)

    def _attenders_count(self, request_id: UUID) -> int:
        # CHG-148: atenciones autenticadas + voluntarios anónimos.
        authenticated = sum(
            1 for rid, _aid in self.attenders if rid == request_id
        )
        anonymous = sum(
            1 for v in self.volunteers if v["help_request_id"] == request_id
        )
        return authenticated + anonymous

    async def attend_help_request(
        self,
        request_id,
        account_id,
        *,
        shares_identity=False,
        name_encrypted=None,
        phone_encrypted=None,
    ):
        row = self.rows.get(request_id)
        if row is None or not self._active(row):
            return None
        self.attenders.add((request_id, account_id))
        key = (request_id, account_id)
        # Espejo del repositorio real: el consentimiento solo se añade,
        # repetir la atención nunca lo retira.
        if shares_identity or key not in self.attender_consent:
            self.attender_consent[key] = {
                "shares_identity": shares_identity
                or self.attender_consent.get(key, {}).get(
                    "shares_identity", False
                ),
                "name_encrypted": name_encrypted,
                "phone_encrypted": phone_encrypted,
                "created_at": datetime.now(UTC),
            }
        return {
            "id": request_id,
            "attenders_count": self._attenders_count(request_id),
        }

    async def list_help_request_attenders(self, request_id, account_id):
        row = self.rows.get(request_id)
        if row is None or row.get("reporter_account_id") != account_id:
            return None
        items = []
        for entry in self.attenders:
            if entry[0] != request_id:
                continue
            detail = self.attender_consent.get(entry, {})
            items.append(
                {
                    "kind": "account",
                    "id": entry[1],
                    "created_at": detail.get(
                        "created_at", datetime.now(UTC)
                    ),
                    "shares_contact": detail.get("shares_identity", False),
                    "name_encrypted": detail.get("name_encrypted"),
                    "phone_encrypted": detail.get("phone_encrypted"),
                    "has_photo": False,
                }
            )
        for volunteer in self.volunteers:
            if volunteer["help_request_id"] != request_id:
                continue
            items.append(
                {
                    "kind": "volunteer",
                    "id": volunteer["id"],
                    "created_at": volunteer["created_at"],
                    "shares_contact": volunteer.get("shares_contact", False),
                    "name_encrypted": volunteer.get("name_encrypted"),
                    "phone_encrypted": volunteer.get("phone_encrypted"),
                    "has_photo": volunteer.get("photo_derived_storage_key")
                    is not None,
                }
            )
        items.sort(key=lambda item: item["created_at"], reverse=True)
        return items

    async def get_help_request_volunteer_photo(
        self, request_id, volunteer_id, account_id
    ):
        row = self.rows.get(request_id)
        if row is None or row.get("reporter_account_id") != account_id:
            return None
        for volunteer in self.volunteers:
            if (
                volunteer["id"] == volunteer_id
                and volunteer["help_request_id"] == request_id
                and volunteer.get("shares_contact")
                and volunteer.get("photo_derived_storage_key")
            ):
                return {
                    "object_key": volunteer["photo_derived_storage_key"],
                    "content_type": volunteer.get(
                        "photo_content_type", "image/jpeg"
                    ),
                }
        return None

    async def create_help_request_volunteer(
        self, *, idempotency_key, request_id, **kwargs
    ):
        row = self.rows.get(request_id)
        if row is None or not self._active(row):
            return None
        already = any(
            v["idempotency_key"] == idempotency_key for v in self.volunteers
        )
        if not already:
            self.volunteers.append(
                {
                    "id": uuid4(),
                    "created_at": datetime.now(UTC),
                    "idempotency_key": idempotency_key,
                    "help_request_id": request_id,
                    **kwargs,
                }
            )
        return (
            {
                "id": request_id,
                "attenders_count": self._attenders_count(request_id),
            },
            not already,
        )

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


# CHG-137 — los clientes con bundle anterior a CHG-136 aún adjuntan la
# instantánea del reportante: se acepta y se descarta sin almacenarla.
@pytest.mark.anyio
async def test_create_ignores_legacy_reporter_snapshot():
    repository = FakeHelpRequestRepository()
    app = help_app(repository=repository)

    response = await post_help_request(
        app,
        payload=valid_payload(
            reporterLatitude=7.11, reporterLongitude=-73.12
        ),
    )

    assert response.status_code == 201
    row = next(iter(repository.rows.values()))
    assert "reporter_latitude" not in row
    assert "reporter_longitude" not in row


# CHG-131 — el radio de aviso viaja con la solicitud y se proyecta en
# el listado; exige coordenadas y rango 1-100 km.
@pytest.mark.anyio
async def test_create_stores_notification_radius():
    repository = FakeHelpRequestRepository()
    app = help_app(repository=repository)

    response = await post_help_request(
        app, payload=valid_payload(notificationRadiusKm=15)
    )

    assert response.status_code == 201
    row = next(iter(repository.rows.values()))
    assert row["notification_radius_km"] == 15

    listing = await request_app(app, "GET", "/internal/v1/help-requests")
    assert listing.json()["items"][0]["notificationRadiusKm"] == 15


@pytest.mark.anyio
async def test_create_rejects_radius_without_coordinates():
    app = help_app()

    payload = valid_payload(notificationRadiusKm=15)
    del payload["latitude"]
    del payload["longitude"]
    response = await post_help_request(app, payload=payload)

    assert response.status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize("radius", [0, 101, -3])
async def test_create_rejects_out_of_range_radius(radius):
    app = help_app()
    response = await post_help_request(
        app, payload=valid_payload(notificationRadiusKm=radius)
    )
    assert response.status_code == 422


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


# CHG-146: una descripción de emergencia breve pero real (3-4 palabras)
# se aceptaba antes solo con 5; ahora la solicitud de ayuda pide 3.
@pytest.mark.anyio
@pytest.mark.parametrize(
    "description",
    ["Necesito ayuda urgente aqui", "Atrapados bajo escombros"],
)
async def test_create_accepts_short_emergency_description(description):
    app = help_app()
    response = await post_help_request(
        app, payload=valid_payload(description=description)
    )
    assert response.status_code == 201


# CHG-146: 1-2 palabras siguen siendo demasiado pobres.
@pytest.mark.anyio
@pytest.mark.parametrize("description", ["ayuda ayuda ayuda", "Necesito ayuda"])
async def test_create_rejects_too_few_words(description):
    app = help_app()
    response = await post_help_request(
        app, payload=valid_payload(description=description)
    )
    assert response.status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize("hours", [0, 721, -4])
async def test_create_rejects_out_of_range_duration(hours):
    app = help_app()
    response = await post_help_request(
        app, payload=valid_payload(durationHours=hours)
    )
    assert response.status_code == 422
    assert "durationHours" in response.json().get("fields", [])


# CHG-130 — la vigencia también se expresa en días (el cliente los
# convierte a horas); el tope es 30 días = 720 horas.
@pytest.mark.anyio
async def test_create_accepts_duration_in_days():
    repository = FakeHelpRequestRepository()
    app = help_app(repository=repository)

    response = await post_help_request(
        app, payload=valid_payload(durationHours=720)
    )

    assert response.status_code == 201
    body = response.json()
    received = datetime.fromisoformat(body["receivedAt"])
    expires = datetime.fromisoformat(body["expiresAt"])
    assert expires - received == timedelta(days=30)


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


# CHG-190 — la solicitud propia se marca para su dueño, y solo para él:
# «Mi espacio» la esconde porque allí las solicitudes están para atenderlas.
@pytest.mark.anyio
async def test_list_marks_own_request_for_its_author():
    repository = FakeHelpRequestRepository()
    mine = repository.seed(reporter_account_id=UUID(ACCOUNT_ID))
    others = repository.seed(reporter_account_id=UUID(OTHER_ACCOUNT_ID))
    anonymous = repository.seed()
    app = help_app(repository=repository)

    response = await request_app(
        app, "GET", "/internal/v1/help-requests", headers=AUTH_HEADERS
    )

    assert response.status_code == 200
    flags = {
        item["id"]: item["createdByMe"] for item in response.json()["items"]
    }
    assert flags[str(mine)] is True
    assert flags[str(others)] is False
    assert flags[str(anonymous)] is False


@pytest.mark.anyio
async def test_list_without_session_never_marks_ownership():
    repository = FakeHelpRequestRepository()
    repository.seed(reporter_account_id=UUID(ACCOUNT_ID))
    app = help_app(repository=repository)

    response = await request_app(app, "GET", "/internal/v1/help-requests")

    assert response.status_code == 200
    assert response.json()["items"][0]["createdByMe"] is False


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


# --- Voluntario anónimo (CHG-148) ---


def volunteer_payload(**overrides):
    payload = {
        "name": "María Restrepo",
        "phone": "+57 300 123 4567",
        "email": "maria@example.com",
    }
    payload.update(overrides)
    return payload


async def post_volunteer(app, request_id, payload=None, key="voluntario-anon-01"):
    return await request_app(
        app,
        "POST",
        f"/internal/v1/help-requests/{request_id}/volunteers",
        data={
            "payload": json.dumps(
                payload if payload is not None else volunteer_payload()
            )
        },
        headers={"Idempotency-Key": key},
    )


@pytest.mark.anyio
async def test_volunteer_anonymous_increments_count():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed()
    app = help_app(repository=repository)

    response = await post_volunteer(app, request_id)

    assert response.status_code == 200
    assert response.json()["attendersCount"] == 1
    # La PII no viaja de vuelta: solo el contador.
    assert "name" not in response.json()
    assert len(repository.volunteers) == 1


@pytest.mark.anyio
async def test_volunteer_and_attend_combine_in_count():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed()
    app = help_app(repository=repository)

    await request_app(
        app,
        "POST",
        f"/internal/v1/help-requests/{request_id}/attend",
        headers=AUTH_HEADERS,
    )
    response = await post_volunteer(app, request_id)

    assert response.json()["attendersCount"] == 2


@pytest.mark.anyio
async def test_volunteer_is_idempotent():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed()
    app = help_app(repository=repository)

    first = await post_volunteer(app, request_id, key="mismo-voluntario-01")
    second = await post_volunteer(app, request_id, key="mismo-voluntario-01")

    assert first.json()["attendersCount"] == 1
    assert second.json()["attendersCount"] == 1
    assert len(repository.volunteers) == 1


@pytest.mark.anyio
async def test_volunteer_on_expired_request_is_not_found():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed(expired=True)
    app = help_app(repository=repository)

    response = await post_volunteer(app, request_id)

    assert response.status_code == 404


@pytest.mark.anyio
async def test_volunteer_rejects_blank_name():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed()
    app = help_app(repository=repository)

    response = await post_volunteer(app, request_id, payload=volunteer_payload(name=""))

    assert response.status_code == 422


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


# CHG-138 — Gestión desde la consola de superadministración: ver TODO
# (activas y expiradas), borrar una a una o vaciar. Solo super_admin;
# cada operación queda auditada y limpia las fotos del storage.

ADMIN_HEADERS = {
    "X-Actor-Role": "super_admin",
    "X-Actor-Account-Id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
    "X-Actor-Display": "QWRtaW4gQ1VTT0w=",
}


@pytest.mark.anyio
async def test_admin_list_requires_super_admin():
    app = help_app()
    response = await request_app(
        app,
        "GET",
        "/internal/v1/admin/help-requests",
        headers={**ADMIN_HEADERS, "X-Actor-Role": "moderator"},
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_admin_list_includes_expired_with_flag():
    repository = FakeHelpRequestRepository()
    active_id = repository.seed()
    expired_id = repository.seed(expired=True)
    app = help_app(repository=repository)

    response = await request_app(
        app,
        "GET",
        "/internal/v1/admin/help-requests",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    by_id = {item["id"]: item for item in body["items"]}
    assert by_id[str(active_id)]["expired"] is False
    assert by_id[str(expired_id)]["expired"] is True
    assert by_id[str(active_id)]["publicCode"].startswith("HR-")


@pytest.mark.anyio
async def test_admin_delete_one_cleans_photos_and_audits():
    repository = FakeHelpRequestRepository()
    storage = FakeStorage()
    request_id = repository.seed(photo=True)
    photo_key = repository.rows[request_id][
        "photo_derived_storage_key"
    ]
    app = help_app(repository=repository, storage=storage)

    response = await request_app(
        app,
        "DELETE",
        f"/internal/v1/admin/help-requests/{request_id}",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {"deleted": 1}
    assert request_id not in repository.rows
    assert photo_key in storage.deleted
    assert ("help_request_deleted", "success") in repository.audit

    missing = await request_app(
        app,
        "DELETE",
        f"/internal/v1/admin/help-requests/{uuid4()}",
        headers=ADMIN_HEADERS,
    )
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_admin_purge_empties_everything():
    repository = FakeHelpRequestRepository()
    storage = FakeStorage()
    repository.seed()
    repository.seed(expired=True)
    with_photo = repository.seed(photo=True)
    photo_key = repository.rows[with_photo]["photo_derived_storage_key"]
    app = help_app(repository=repository, storage=storage)

    response = await request_app(
        app,
        "DELETE",
        "/internal/v1/admin/help-requests",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {"deleted": 3}
    assert repository.rows == {}
    assert photo_key in storage.deleted
    assert ("help_requests_purged", "success") in repository.audit

    # El público ve la plataforma limpia.
    listing = await request_app(app, "GET", "/internal/v1/help-requests")
    assert listing.json()["total"] == 0


# CHG-139 — Reinicio absoluto de los datos de emergencia: exige rol
# super_admin, vacía el almacenamiento y deja el acto como primer
# evento de la auditoría nueva.
@pytest.mark.anyio
async def test_platform_reset_wipes_data_storage_and_audits():
    repository = FakeHelpRequestRepository()
    repository.seed()

    async def admin_reset_platform():
        repository.rows.clear()
        repository.attenders.clear()
        return 21

    repository.admin_reset_platform = admin_reset_platform
    storage = FakeStorage()
    app = help_app(repository=repository, storage=storage)

    response = await request_app(
        app,
        "POST",
        "/internal/v1/admin/platform-reset",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {"tablesCleared": 21}
    assert repository.rows == {}
    assert storage.wiped is True
    assert ("platform_reset", "success") in repository.audit


@pytest.mark.anyio
async def test_platform_reset_requires_super_admin():
    app = help_app()
    response = await request_app(
        app,
        "POST",
        "/internal/v1/admin/platform-reset",
        headers={**ADMIN_HEADERS, "X-Actor-Role": "moderator"},
    )
    assert response.status_code == 403


# CHG-148 — El super_admin ve los voluntarios anónimos con su PII
# descifrada; el conteo aparece en el listado admin.
@pytest.mark.anyio
async def test_admin_lists_volunteers_decrypted():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed()
    app = help_app(repository=repository)

    await post_volunteer(
        app,
        request_id,
        payload=volunteer_payload(name="Camilo Vega", phone="+57 301 000 0000"),
    )

    listing = await request_app(
        app,
        "GET",
        "/internal/v1/admin/help-requests",
        headers=ADMIN_HEADERS,
    )
    item = next(
        x for x in listing.json()["items"] if x["id"] == str(request_id)
    )
    assert item["volunteersCount"] == 1

    volunteers = await request_app(
        app,
        "GET",
        f"/internal/v1/admin/help-requests/{request_id}/volunteers",
        headers=ADMIN_HEADERS,
    )
    assert volunteers.status_code == 200
    body = volunteers.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Camilo Vega"
    assert body["items"][0]["phone"] == "+57 301 000 0000"


@pytest.mark.anyio
async def test_admin_volunteers_requires_super_admin():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed()
    app = help_app(repository=repository)

    response = await request_app(
        app,
        "GET",
        f"/internal/v1/admin/help-requests/{request_id}/volunteers",
        headers={**ADMIN_HEADERS, "X-Actor-Role": "moderator"},
    )
    assert response.status_code == 403


# CHG-180 — «Necesitamos ayuda» gana lo mismo que un Centro de Acopio
# Local: comentarios con estrellas, denuncias con sus umbrales y borrado
# administrativo de comentarios. Mismo contrato que CHG-176 dio a las
# ofertas de comida; solo cambia el objetivo.

COMMUNITY_REQUEST_ID = UUID("cccccccc-cccc-4ccc-8ccc-ccccccccc180")
COMMUNITY_COMMENT_ID = UUID("cccccccc-cccc-4ccc-8ccc-ccccccccc181")
COMMUNITY_ACTOR_ID = UUID(ACCOUNT_ID)
COMMUNITY_CREATED_AT = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
COMMUNITY_ADMIN_HEADERS = {
    "X-Actor-Role": "super_admin",
    "X-Actor-Account-Id": str(COMMUNITY_ACTOR_ID),
    "X-Actor-Display": "YWRtaW4=",
}
COMMUNITY_COMMENT_HEADERS = {
    "Idempotency-Key": "clave-comentario-ayuda-0180",
    "X-Actor-Kind": "anonymous",
}
COMMUNITY_REPORT_HEADERS = {
    "Idempotency-Key": "clave-denuncia-ayuda-0180",
    "X-Actor-Kind": "anonymous",
    "X-Denouncer-Key": "fp:abcdef0123456789",
}


class FakeHelpRequestCommunityRepository(FakeHelpRequestRepository):
    def __init__(self, *, missing=False, reporters=1, disabled=False):
        super().__init__()
        self.missing = missing
        self.reporters = reporters
        self.disabled = disabled
        self.community_calls: list[tuple[str, dict]] = []

    async def list_help_request_comments(self, **kwargs):
        self.community_calls.append(("list", kwargs))
        if self.missing:
            return None
        return {
            "items": [
                {
                    "id": COMMUNITY_COMMENT_ID,
                    "account_id": None,
                    "author_display_name": None,
                    "actor_kind": "anonymous",
                    "content": "Llegó ayuda al lugar, todo cierto.",
                    "rating": 5,
                    "created_at": COMMUNITY_CREATED_AT,
                }
            ],
            "total": 1,
            "rating_average": 4.5,
            "rating_count": 2,
        }

    async def create_help_request_comment(self, **kwargs):
        self.community_calls.append(("comment", kwargs))
        if self.missing:
            return None
        return {
            "id": COMMUNITY_COMMENT_ID,
            "account_id": None,
            "author_display_name": None,
            "actor_kind": "anonymous",
            "content": kwargs["content"],
            "rating": kwargs["rating"],
            "created_at": COMMUNITY_CREATED_AT,
        }

    async def create_help_request_report(self, **kwargs):
        self.community_calls.append(("report", kwargs))
        if self.missing:
            return None
        return {
            "reports_count": self.reporters,
            "under_observation": self.reporters >= 10 and not self.disabled,
            "disabled": self.disabled,
        }

    async def admin_delete_help_request_comment(self, **kwargs):
        self.community_calls.append(("delete_comment", kwargs))
        return 0 if self.missing else 1


def community_app(repository=None):
    return create_app(
        repository=repository or FakeHelpRequestCommunityRepository(),
        storage=FakeStorage(),
    )


@pytest.mark.anyio
async def test_help_request_comments_publish_the_average():
    response = await request_app(
        community_app(),
        "GET",
        f"/internal/v1/help-requests/{COMMUNITY_REQUEST_ID}/comments",
    )

    assert response.status_code == 200
    body = response.json()
    # El promedio lo calcula el servidor, igual que en los acopios.
    assert body["ratingAverage"] == 4.5
    assert body["ratingCount"] == 2
    assert body["items"][0]["rating"] == 5


@pytest.mark.anyio
async def test_help_request_comment_requires_a_star_rating():
    response = await request_app(
        community_app(),
        "POST",
        f"/internal/v1/help-requests/{COMMUNITY_REQUEST_ID}/comments",
        headers=COMMUNITY_COMMENT_HEADERS,
        json={"content": "Confirmo que la solicitud es real."},
    )
    # CHG-166 rige igual aquí: sin estrellas no hay comentario.
    assert response.status_code == 422


@pytest.mark.anyio
async def test_help_request_comment_is_published_anonymously():
    repository = FakeHelpRequestCommunityRepository()
    response = await request_app(
        community_app(repository),
        "POST",
        f"/internal/v1/help-requests/{COMMUNITY_REQUEST_ID}/comments",
        headers=COMMUNITY_COMMENT_HEADERS,
        json={
            "content": "Estuve allí y la ayuda hacía falta.",
            "rating": 4,
        },
    )

    assert response.status_code == 201
    assert response.json()["rating"] == 4
    call = dict(repository.community_calls[0][1])
    assert call["help_request_id"] == COMMUNITY_REQUEST_ID
    assert call["actor_kind"] == "anonymous"


@pytest.mark.anyio
async def test_help_request_report_needs_the_denouncer_key():
    response = await request_app(
        community_app(),
        "POST",
        f"/internal/v1/help-requests/{COMMUNITY_REQUEST_ID}/reports",
        headers={
            key: value
            for key, value in COMMUNITY_REPORT_HEADERS.items()
            if key != "X-Denouncer-Key"
        },
        json={"category": "informacion_falsa", "reason": "No existe."},
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_help_request_report_reports_the_threshold_state():
    repository = FakeHelpRequestCommunityRepository(
        reporters=20, disabled=True
    )
    response = await request_app(
        community_app(repository),
        "POST",
        f"/internal/v1/help-requests/{COMMUNITY_REQUEST_ID}/reports",
        headers=COMMUNITY_REPORT_HEADERS,
        json={
            "category": "informacion_falsa",
            "reason": "La dirección no corresponde con la realidad.",
        },
    )

    assert response.status_code == 202
    body = response.json()
    # Al alcanzar el umbral la solicitud deja de publicarse.
    assert body["disabled"] is True
    assert body["reportsCount"] == 20
    assert body["helpRequestId"] == str(COMMUNITY_REQUEST_ID)


@pytest.mark.anyio
async def test_admin_deletes_a_help_request_comment():
    repository = FakeHelpRequestCommunityRepository()
    response = await request_app(
        community_app(repository),
        "DELETE",
        f"/internal/v1/admin/help-requests/{COMMUNITY_REQUEST_ID}"
        f"/comments/{COMMUNITY_COMMENT_ID}",
        headers=COMMUNITY_ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["deleted"] == 1


@pytest.mark.anyio
async def test_help_request_comment_deletion_refuses_a_plain_account():
    response = await request_app(
        community_app(),
        "DELETE",
        f"/internal/v1/admin/help-requests/{COMMUNITY_REQUEST_ID}"
        f"/comments/{COMMUNITY_COMMENT_ID}",
        headers={
            "X-Actor-Role": "user",
            "X-Actor-Account-Id": str(COMMUNITY_ACTOR_ID),
        },
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_community_endpoints_404_on_a_missing_request():
    app = community_app(FakeHelpRequestCommunityRepository(missing=True))

    listing = await request_app(
        app,
        "GET",
        f"/internal/v1/help-requests/{COMMUNITY_REQUEST_ID}/comments",
    )
    assert listing.status_code == 404

    report = await request_app(
        app,
        "POST",
        f"/internal/v1/help-requests/{COMMUNITY_REQUEST_ID}/reports",
        headers=COMMUNITY_REPORT_HEADERS,
        json={"category": "informacion_falsa", "reason": "No existe."},
    )
    assert report.status_code == 404


# --- CHG-193: quién atiende MI solicitud ---


@pytest.mark.anyio
async def test_owner_sees_who_attends_with_and_without_consent():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed(reporter_account_id=UUID(ACCOUNT_ID))
    app = help_app(repository=repository)

    # Voluntaria que aceptó el aviso del formulario nuevo.
    await post_volunteer(
        app,
        request_id,
        payload={
            "name": "Ana Voluntaria",
            "phone": "3001234567",
            "email": "ana@example.com",
            "sharesContact": True,
        },
        key="voluntaria-que-consiente-01",
    )
    # Voluntario registrado bajo la promesa vieja: no comparte nada.
    await post_volunteer(
        app,
        request_id,
        payload={"name": "Bruno Antiguo", "phone": "3007654321"},
        key="voluntario-sin-consentimiento-1",
    )
    # Persona con cuenta que aceptó el aviso al atender.
    await request_app(
        app,
        "POST",
        f"/internal/v1/help-requests/{request_id}/attend",
        json={
            "sharesIdentity": True,
            "name": "Carla Con Cuenta",
            "phone": "3009998877",
        },
        headers={
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": OTHER_ACCOUNT_ID,
        },
    )

    response = await request_app(
        app,
        "GET",
        f"/internal/v1/help-requests/{request_id}/attenders",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    by_name = {item["name"]: item for item in body["items"]}
    assert by_name["Ana Voluntaria"]["phone"] == "3001234567"
    assert by_name["Ana Voluntaria"]["kind"] == "volunteer"
    assert by_name["Carla Con Cuenta"]["phone"] == "3009998877"
    assert by_name["Carla Con Cuenta"]["kind"] == "account"
    # El de la promesa vieja figura, pero sin un solo dato personal.
    sin_datos = [item for item in body["items"] if item["name"] is None]
    assert len(sin_datos) == 1
    assert sin_datos[0]["sharesContact"] is False
    assert sin_datos[0]["phone"] is None
    # El correo NUNCA sale por aquí, ni siquiera con consentimiento.
    assert all("email" not in item for item in body["items"])


@pytest.mark.anyio
async def test_attending_without_accepting_shares_nothing():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed(reporter_account_id=UUID(ACCOUNT_ID))
    app = help_app(repository=repository)

    await request_app(
        app,
        "POST",
        f"/internal/v1/help-requests/{request_id}/attend",
        headers={
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": OTHER_ACCOUNT_ID,
        },
    )

    body = (
        await request_app(
            app,
            "GET",
            f"/internal/v1/help-requests/{request_id}/attenders",
            headers=AUTH_HEADERS,
        )
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["sharesContact"] is False
    assert body["items"][0]["name"] is None


@pytest.mark.anyio
async def test_attenders_are_only_for_the_owner():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed(reporter_account_id=UUID(ACCOUNT_ID))
    app = help_app(repository=repository)

    ajena = await request_app(
        app,
        "GET",
        f"/internal/v1/help-requests/{request_id}/attenders",
        headers={
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": OTHER_ACCOUNT_ID,
        },
    )
    inexistente = await request_app(
        app,
        "GET",
        f"/internal/v1/help-requests/{uuid4()}/attenders",
        headers=AUTH_HEADERS,
    )
    sin_sesion = await request_app(
        app,
        "GET",
        f"/internal/v1/help-requests/{request_id}/attenders",
    )

    # Ajena e inexistente responden IGUAL: no se delata cuál existe.
    assert ajena.status_code == 404
    assert inexistente.status_code == 404
    assert ajena.json()["detail"] == inexistente.json()["detail"]
    assert sin_sesion.status_code == 401



# CHG-196 — La dueña elimina su propia solicitud.
@pytest.mark.anyio
async def test_owner_deletes_own_help_request():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed(reporter_account_id=UUID(ACCOUNT_ID))
    app = help_app(repository=repository)

    borrado = await request_app(
        app,
        "DELETE",
        f"/internal/v1/help-requests/{request_id}",
        headers=AUTH_HEADERS,
    )

    assert borrado.status_code == 204
    # La fila se fue de verdad: ya no se lista ni se puede volver a borrar.
    assert request_id not in repository.rows
    repetido = await request_app(
        app,
        "DELETE",
        f"/internal/v1/help-requests/{request_id}",
        headers=AUTH_HEADERS,
    )
    assert repetido.status_code == 404


@pytest.mark.anyio
async def test_deleting_a_help_request_is_only_for_its_owner():
    repository = FakeHelpRequestRepository()
    request_id = repository.seed(reporter_account_id=UUID(ACCOUNT_ID))
    app = help_app(repository=repository)

    ajena = await request_app(
        app,
        "DELETE",
        f"/internal/v1/help-requests/{request_id}",
        headers={
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": OTHER_ACCOUNT_ID,
        },
    )
    inexistente = await request_app(
        app,
        "DELETE",
        f"/internal/v1/help-requests/{uuid4()}",
        headers=AUTH_HEADERS,
    )
    sin_sesion = await request_app(
        app,
        "DELETE",
        f"/internal/v1/help-requests/{request_id}",
    )

    # Ajena e inexistente responden IGUAL, como en CHG-193.
    assert ajena.status_code == 404
    assert inexistente.status_code == 404
    assert ajena.json()["detail"] == inexistente.json()["detail"]
    assert sin_sesion.status_code == 401
    # Y la solicitud sigue viva: nadie ajeno la tocó.
    assert request_id in repository.rows
