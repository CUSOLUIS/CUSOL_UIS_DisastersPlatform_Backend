"""CHG-034 — Directorio humanitario y aportes con evidencia.

Cubre búsqueda unificada con filtros por tipo, aportes multipart con
idempotencia y privacidad, y las reglas puras de moderación/agregados
que la persistencia comparte.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest

from app import moderation
from app.config import Settings
from app.main import build_fernet, create_app
from app.models import (
    AidLocationDirectoryCard,
    MissingPersonDirectoryCard,
    SourceReference,
)
from app.repository import (
    DeceasedOutcomeFinalError,
    HealthVerifiedCaseError,
)
from app.storage import StorageUnavailableError

from test_missing_persons import (
    EICAR,
    FakeStorage,
    make_jpeg,
    request_app,
)


PERSON_ID = UUID("55555555-5555-4555-8555-555555555501")
LOCATION_ID = UUID("44444444-4444-4444-8444-444444444403")
FERNET = build_fernet(Settings.from_environment().report_encryption_key)

PERSON_CARD = MissingPersonDirectoryCard(
    id=PERSON_ID,
    public_case_code="MP-2026-DEMO01",
    display_name="Camila Rueda (caso demo)",
    status="missing",
    approximate_age=34,
    last_seen_at=datetime(2026, 8, 10, 18, 30, tzinfo=UTC),
    last_seen_area="Sector Café Madrid",
    municipality="Bucaramanga",
    department="Santander",
    public_photo_url=None,
    source=SourceReference(
        name="Reporte ciudadano — plataforma CUSOL",
        source_type="citizen",
        url=None,
    ),
    updated_at=datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
    data_classification="demonstrative",
)

LOCATION_CARD = AidLocationDirectoryCard(
    kind="collection_center",
    id=LOCATION_ID,
    name="Centro de acopio — Coliseo Bicentenario",
    location_label="Coliseo Bicentenario, Bucaramanga",
    municipality="Bucaramanga",
    department="Santander",
    verification_status="verified",
    availability_status="active",
    open_now=True,
    accepted_supplies=["water", "food"],
    average_rating=4.5,
    ratings_count=2,
    source=SourceReference(
        name="UNGRD", source_type="official", url=None
    ),
    updated_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
    data_classification="demonstrative",
)


class FakeDirectoryRepository:
    def __init__(
        self,
        duplicate: bool = False,
        fail: bool = False,
        publishable_persons: set[UUID] | None = None,
        publishable_locations: set[UUID] | None = None,
        health_verified: bool = False,
        health_verified_on_create: bool = False,
        public_status: str = "missing",
        deceased_on_create: bool = False,
    ):
        self.duplicate = duplicate
        self.fail = fail
        # CHG-120: caso con novedad efectiva del sector salud; la
        # variante `on_create` simula la carrera que solo detecta la
        # comprobación transaccional.
        self.health_verified = health_verified
        self.health_verified_on_create = health_verified_on_create
        # CHG-122: estado público del caso para la máquina de estados;
        # `deceased_on_create` simula el fallecimiento registrado en
        # plena carrera.
        self.public_status = public_status
        self.deceased_on_create = deceased_on_create
        self.publishable_persons = (
            publishable_persons
            if publishable_persons is not None
            else {PERSON_ID}
        )
        self.publishable_locations = (
            publishable_locations
            if publishable_locations is not None
            else {LOCATION_ID}
        )
        self.last_person_search = None
        self.last_location_search = None
        self.created_status_report = None
        self.created_status_photos = None
        self.created_rating = None
        self.created_rating_photos = None

    async def ping(self) -> bool:
        return True

    async def search_directory_missing_persons(
        self, query, person_status, department, limit, offset
    ):
        self.last_person_search = {
            "query": query,
            "person_status": person_status,
            "department": department,
            "limit": limit,
            "offset": offset,
        }
        return [PERSON_CARD], 1

    async def search_directory_aid_locations(
        self,
        kind,
        query,
        verification_status,
        availability_status,
        open_now,
        department,
        min_rating,
        limit,
        offset,
    ):
        self.last_location_search = {
            "kind": kind,
            "query": query,
            "verification_status": verification_status,
            "availability_status": availability_status,
            "open_now": open_now,
            "department": department,
            "min_rating": min_rating,
            "limit": limit,
            "offset": offset,
        }
        return [LOCATION_CARD], 1

    # CHG-120: bloqueo por novedad efectiva del sector salud.
    async def person_has_effective_health_report(self, person_id):
        if self.fail:
            raise asyncpg.PostgresError("fallo simulado")
        return self.health_verified

    # CHG-122: estado público para la máquina de estados.
    async def person_public_status(self, person_id):
        if self.fail:
            raise asyncpg.PostgresError("fallo simulado")
        if person_id not in self.publishable_persons:
            return None
        return self.public_status

    # CHG-077: novedades visibles de una persona publicada.
    async def list_person_status_reports(self, person_id, limit):
        if self.fail:
            raise asyncpg.PostgresError("fallo simulado")
        if person_id not in self.publishable_persons:
            return None
        rows = [
            {
                "id": UUID("77777777-7777-4777-8777-777777777701"),
                "claimed_outcome": "found",
                "evidence_description_encrypted": FERNET.encrypt(
                    b"La vi en el albergue del colegio."
                ),
                "location_description_encrypted": FERNET.encrypt(
                    b"Albergue del colegio central"
                ),
                "occurred_at": datetime(
                    2026, 8, 14, 10, 0, tzinfo=UTC
                ),
                "received_at": datetime(
                    2026, 8, 14, 11, 0, tzinfo=UTC
                ),
                "actor_kind": "authenticated",
                "reporter_health_sector": True,
                "moderation_status": "under_review",
            },
            {
                "id": UUID("77777777-7777-4777-8777-777777777702"),
                "claimed_outcome": "found",
                "evidence_description_encrypted": FERNET.encrypt(
                    b"Coincide con la persona del punto de encuentro."
                ),
                "location_description_encrypted": None,
                "occurred_at": None,
                "received_at": datetime(
                    2026, 8, 14, 9, 30, tzinfo=UTC
                ),
                "actor_kind": "anonymous",
                "reporter_health_sector": False,
                "moderation_status": "accepted",
            },
        ][:limit]
        return "found", rows

    async def aid_location_is_publishable(self, location_id):
        return location_id in self.publishable_locations

    async def create_person_status_report(self, report, photos):
        from app.models import CommunityContributionReceipt

        if self.fail:
            raise asyncpg.PostgresError("fallo simulado")
        # CHG-120: la comprobación transaccional cierra la carrera.
        if (
            self.health_verified_on_create
            and not report.reporter_health_sector
        ):
            raise HealthVerifiedCaseError
        # CHG-122: fallecimiento registrado en plena carrera.
        if self.deceased_on_create and report.claimed_outcome == "found":
            raise DeceasedOutcomeFinalError
        if self.duplicate:
            return (
                CommunityContributionReceipt(
                    id=UUID("99999999-9999-4999-8999-999999999901"),
                    status="under_review",
                    actor_kind="anonymous",
                    received_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
                ),
                False,
            )
        self.created_status_report = report
        self.created_status_photos = photos
        return (
            CommunityContributionReceipt(
                id=report.id,
                status="under_review",
                actor_kind=report.actor_kind,
                received_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
            ),
            True,
        )

    async def create_aid_location_rating(self, rating, photos):
        from app.models import CommunityContributionReceipt

        if self.fail:
            raise asyncpg.PostgresError("fallo simulado")
        if self.duplicate:
            return (
                CommunityContributionReceipt(
                    id=UUID("99999999-9999-4999-8999-999999999902"),
                    status="under_review",
                    actor_kind="anonymous",
                    received_at=datetime(2026, 8, 14, 9, 5, tzinfo=UTC),
                ),
                False,
            )
        self.created_rating = rating
        self.created_rating_photos = photos
        return (
            CommunityContributionReceipt(
                id=rating.id,
                status="under_review",
                actor_kind=rating.actor_kind,
                received_at=datetime(2026, 8, 14, 9, 5, tzinfo=UTC),
            ),
            True,
        )


def directory_app(repository=None, storage=None):
    return create_app(
        repository=repository or FakeDirectoryRepository(),
        storage=storage if storage is not None else FakeStorage(),
    )


def status_payload(**overrides) -> dict:
    payload = {
        "claimedOutcome": "found",
        "evidenceDescription": (
            "La persona fue vista con vida en el albergue municipal "
            "durante la jornada de ayer."
        ),
        "truthConfirmed": True,
        "reviewAcknowledged": True,
    }
    payload.update(overrides)
    return payload


def rating_payload(**overrides) -> dict:
    payload = {
        "rating": 5,
        "evidenceDescription": (
            "Atendieron rápido, con orden y buena señalización."
        ),
        "truthConfirmed": True,
        "reviewAcknowledged": True,
    }
    payload.update(overrides)
    return payload


def photos_form(count: int, photo: bytes | None = None):
    content = photo if photo is not None else make_jpeg()
    return [
        ("photos", (f"foto-{index}.jpg", content, "image/jpeg"))
        for index in range(count)
    ]


IDEMPOTENCY = {"Idempotency-Key": "clave-idempotente-0034"}
STATUS_PATH = f"/internal/v1/missing-persons/{PERSON_ID}/status-reports"
RATING_PATH = f"/internal/v1/aid-locations/{LOCATION_ID}/ratings"


# --- Búsqueda unificada ---


@pytest.mark.anyio
async def test_directory_search_missing_person_filters_and_shape():
    repository = FakeDirectoryRepository()
    app = directory_app(repository=repository)

    response = await request_app(
        app,
        "GET",
        "/internal/v1/humanitarian-directory/search"
        "?kind=missing_person&q=Camila&personStatus=missing"
        "&department=Santander&limit=5&offset=10",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "missing_person"
    assert body["query"] == "Camila"
    assert body["total"] == 1
    assert body["limit"] == 5
    assert body["offset"] == 10
    assert repository.last_person_search == {
        "query": "Camila",
        "person_status": "missing",
        "department": "Santander",
        "limit": 5,
        "offset": 10,
    }
    item = body["items"][0]
    assert item["kind"] == "missing_person"
    assert set(item.keys()) == {
        "kind", "id", "publicCaseCode", "displayName", "status",
        "approximateAge", "lastSeenAt", "lastSeenArea", "municipality",
        "department", "publicPhotoUrl", "source", "updatedAt",
        "dataClassification",
    }


@pytest.mark.anyio
async def test_directory_search_aid_location_filters():
    repository = FakeDirectoryRepository()
    app = directory_app(repository=repository)

    response = await request_app(
        app,
        "GET",
        "/internal/v1/humanitarian-directory/search"
        "?kind=collection_center&q=acopio&verificationStatus=verified"
        "&availabilityStatus=active&openNow=true&department=Santander"
        "&minRating=4",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 20
    assert repository.last_location_search == {
        "kind": "collection_center",
        "query": "acopio",
        "verification_status": "verified",
        "availability_status": "active",
        "open_now": True,
        "department": "Santander",
        "min_rating": 4.0,
        "limit": 20,
        "offset": 0,
    }
    item = body["items"][0]
    assert item["kind"] == "collection_center"
    assert item["averageRating"] == 4.5
    assert item["ratingsCount"] == 2


@pytest.mark.anyio
async def test_directory_search_rejects_cross_kind_filters():
    app = directory_app()

    person_with_aid_filter = await request_app(
        app,
        "GET",
        "/internal/v1/humanitarian-directory/search"
        "?kind=missing_person&q=Camila&minRating=3",
    )
    location_with_person_filter = await request_app(
        app,
        "GET",
        "/internal/v1/humanitarian-directory/search"
        "?kind=collection_point&q=parque&personStatus=found",
    )

    assert person_with_aid_filter.status_code == 422
    assert location_with_person_filter.status_code == 422


@pytest.mark.anyio
async def test_directory_search_rejects_blank_query():
    app = directory_app()

    response = await request_app(
        app,
        "GET",
        "/internal/v1/humanitarian-directory/search"
        "?kind=missing_person&q=%20a",
    )

    assert response.status_code == 422


# --- Novedades de personas ---


@pytest.mark.anyio
async def test_status_report_anonymous_receipt_and_privacy():
    repository = FakeDirectoryRepository()
    storage = FakeStorage()
    app = directory_app(repository=repository, storage=storage)

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(status_payload())},
        files=photos_form(2),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "under_review"
    assert body["actorKind"] == "anonymous"
    assert set(body.keys()) == {"id", "status", "actorKind", "receivedAt"}

    stored = repository.created_status_report
    assert stored.person_id == PERSON_ID
    assert stored.claimed_outcome == "found"
    assert stored.actor_kind == "anonymous"
    assert stored.account_id is None
    # La descripción viaja cifrada y nunca en claro.
    plaintext = status_payload()["evidenceDescription"].encode()
    assert stored.evidence_description_encrypted != plaintext
    assert (
        FERNET.decrypt(stored.evidence_description_encrypted)
        == plaintext
    )
    # Original + derivado por fotografía, con claves opacas.
    assert len(repository.created_status_photos) == 2
    assert len(storage.objects) == 4
    assert all(
        key.startswith("person-status-reports/")
        for key in storage.objects
    )


@pytest.mark.anyio
async def test_status_report_authenticated_actor():
    repository = FakeDirectoryRepository()
    app = directory_app(repository=repository)
    account_id = uuid4()

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers={
            **IDEMPOTENCY,
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": str(account_id),
        },
        data={"payload": json.dumps(status_payload())},
        files=photos_form(1),
    )

    assert response.status_code == 202
    assert response.json()["actorKind"] == "authenticated"
    assert repository.created_status_report.account_id == account_id


@pytest.mark.anyio
async def test_status_report_authenticated_requires_account():
    app = directory_app()

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers={**IDEMPOTENCY, "X-Actor-Kind": "authenticated"},
        data={"payload": json.dumps(status_payload())},
        files=photos_form(1),
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_status_report_unpublishable_person_is_404():
    repository = FakeDirectoryRepository(publishable_persons=set())
    storage = FakeStorage()
    app = directory_app(repository=repository, storage=storage)

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(status_payload())},
        files=photos_form(1),
    )

    assert response.status_code == 404
    assert storage.objects == {}
    assert repository.created_status_report is None


@pytest.mark.anyio
async def test_status_report_requires_one_to_five_photos():
    app = directory_app()

    without_photos = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(status_payload())},
    )
    too_many = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(status_payload())},
        files=photos_form(6),
    )

    assert without_photos.status_code == 422
    assert too_many.status_code == 422


@pytest.mark.anyio
async def test_status_report_rejects_malware_and_fake_images():
    app = directory_app()

    eicar = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(status_payload())},
        files=photos_form(1, photo=make_jpeg() + EICAR),
    )
    fake = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(status_payload())},
        files=[("photos", ("nota.txt", b"no soy una imagen" * 4,
                           "image/jpeg"))],
    )

    assert eicar.status_code == 415
    assert fake.status_code == 415


@pytest.mark.anyio
async def test_status_report_rejects_future_occurred_at():
    app = directory_app()
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={
            "payload": json.dumps(status_payload(occurredAt=future))
        },
        files=photos_form(1),
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_status_report_rejects_extra_or_invalid_fields():
    app = directory_app()

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={
            "payload": json.dumps(
                status_payload(claimedOutcome="alive", extra="x")
            )
        },
        files=photos_form(1),
    )

    assert response.status_code == 422
    cuerpo = response.json()
    # CHG-114: etiqueta en español en el texto, clave en `fields`.
    assert "Resultado alegado" in cuerpo["detail"]
    assert "claimedOutcome" in cuerpo["fields"]
    assert "alive" not in cuerpo["detail"]  # nunca se devuelven valores


@pytest.mark.anyio
async def test_status_report_idempotent_retry_cleans_files():
    repository = FakeDirectoryRepository(duplicate=True)
    storage = FakeStorage()
    app = directory_app(repository=repository, storage=storage)

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(status_payload())},
        files=photos_form(2),
    )

    assert response.status_code == 202
    assert response.json()["id"] == (
        "99999999-9999-4999-8999-999999999901"
    )
    assert storage.objects == {}
    assert len(storage.deleted) == 4


@pytest.mark.anyio
async def test_status_report_requires_idempotency_key():
    app = directory_app()

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers={"Idempotency-Key": "corta"},
        data={"payload": json.dumps(status_payload())},
        files=photos_form(1),
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_status_report_storage_failure_registers_nothing():
    repository = FakeDirectoryRepository()
    app = directory_app(
        repository=repository, storage=FakeStorage(fail=True)
    )

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(status_payload())},
        files=photos_form(1),
    )

    assert response.status_code == 503
    assert repository.created_status_report is None


@pytest.mark.anyio
async def test_status_report_database_failure_cleans_files():
    storage = FakeStorage()
    app = directory_app(
        repository=FakeDirectoryRepository(fail=True), storage=storage
    )

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(status_payload())},
        files=photos_form(1),
    )

    assert response.status_code == 503
    assert storage.objects == {}


# --- Valoraciones de lugares ---


@pytest.mark.anyio
async def test_rating_without_photos_is_accepted():
    repository = FakeDirectoryRepository()
    app = directory_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        RATING_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(rating_payload())},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "under_review"
    stored = repository.created_rating
    assert stored.location_id == LOCATION_ID
    assert stored.rating == 5
    plaintext = rating_payload()["evidenceDescription"].encode()
    assert FERNET.decrypt(
        stored.evidence_description_encrypted
    ) == plaintext
    assert repository.created_rating_photos == []


@pytest.mark.anyio
async def test_rating_accepts_up_to_three_photos():
    repository = FakeDirectoryRepository()
    storage = FakeStorage()
    app = directory_app(repository=repository, storage=storage)

    accepted = await request_app(
        app,
        "POST",
        RATING_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(rating_payload())},
        files=photos_form(3),
    )
    rejected = await request_app(
        app,
        "POST",
        RATING_PATH,
        headers={"Idempotency-Key": "clave-idempotente-0035"},
        data={"payload": json.dumps(rating_payload())},
        files=photos_form(4),
    )

    assert accepted.status_code == 202
    assert len(repository.created_rating_photos) == 3
    assert all(
        key.startswith("aid-location-ratings/")
        for key in storage.objects
    )
    assert rejected.status_code == 422


@pytest.mark.anyio
async def test_rating_rejects_out_of_range_stars():
    app = directory_app()

    response = await request_app(
        app,
        "POST",
        RATING_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(rating_payload(rating=6))},
    )

    assert response.status_code == 422
    # CHG-114: el texto nombra el campo como lo ve quien reporta; la
    # clave interna viaja aparte, para que el cliente pueda resaltarlo.
    cuerpo = response.json()
    assert cuerpo["detail"] == "Revisa los campos: Estrellas."
    assert cuerpo["fields"] == ["rating"]


@pytest.mark.anyio
async def test_rating_unpublishable_location_is_404():
    repository = FakeDirectoryRepository(publishable_locations=set())
    app = directory_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        RATING_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(rating_payload())},
    )

    assert response.status_code == 404
    assert repository.created_rating is None


# --- Reglas de moderación y agregados ---


def test_moderation_requires_authorized_role():
    with pytest.raises(moderation.ModerationNotAllowedError):
        moderation.ensure_moderator_role("user")
    with pytest.raises(moderation.ModerationNotAllowedError):
        moderation.ensure_moderator_role("")
    with pytest.raises(moderation.ModerationNotAllowedError):
        moderation.ensure_moderator_role(None)
    moderation.ensure_moderator_role("moderator")


def test_moderation_transitions():
    assert moderation.can_decide("under_review", "accepted")
    assert moderation.can_decide("under_review", "rejected")
    assert not moderation.can_decide("under_review", "withdrawn")
    assert moderation.can_decide("accepted", "withdrawn")
    assert not moderation.can_decide("accepted", "accepted")
    assert not moderation.can_decide("rejected", "accepted")
    assert not moderation.can_decide("withdrawn", "accepted")
    with pytest.raises(moderation.InvalidModerationTransitionError):
        moderation.ensure_transition("rejected", "accepted")


def test_public_status_only_changes_with_accepted_reports():
    # Crear novedades (ninguna aceptada) no cambia el estado público.
    assert moderation.public_status_from_accepted_outcomes([]) == (
        "missing"
    )
    # Aceptar una novedad sí lo cambia; la última aceptada define.
    assert moderation.public_status_from_accepted_outcomes(
        ["found"]
    ) == "found"
    assert moderation.public_status_from_accepted_outcomes(
        ["found", "deceased"]
    ) == "deceased"


def test_rating_aggregate_uses_only_accepted_ratings():
    # Sin aceptadas: sin promedio y conteo cero, aunque existan
    # valoraciones under_review o rechazadas (no llegan a la lista).
    assert moderation.recompute_rating_aggregate([]) == (None, 0)
    assert moderation.recompute_rating_aggregate([5, 4]) == (4.5, 2)
    assert moderation.recompute_rating_aggregate([5, 4, 4]) == (4.33, 3)


# --- CHG-077: verificación comunitaria del estado ---


@pytest.mark.anyio
async def test_status_report_stores_health_sector_flag():
    repository = FakeDirectoryRepository()
    app = directory_app(repository=repository)
    account_id = uuid4()

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers={
            **IDEMPOTENCY,
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": str(account_id),
            "X-Actor-Health": "true",
        },
        data={"payload": json.dumps(status_payload())},
        files=photos_form(1),
    )

    assert response.status_code == 202
    assert repository.created_status_report.reporter_health_sector is True


@pytest.mark.anyio
async def test_status_report_health_flag_ignored_for_anonymous():
    # La bandera solo vale para actores autenticados declarados por el
    # gateway; un cliente anónimo no puede reclamarla.
    repository = FakeDirectoryRepository()
    app = directory_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers={**IDEMPOTENCY, "X-Actor-Health": "true"},
        data={"payload": json.dumps(status_payload())},
        files=photos_form(1),
    )

    assert response.status_code == 202
    assert repository.created_status_report.reporter_health_sector is False


# --- CHG-120: un reporte del sector salud cierra las novedades ---


@pytest.mark.anyio
async def test_status_report_blocked_after_health_verification():
    # Caso con novedad efectiva del sector salud: un envío anónimo
    # recibe 409 y no deja rastro (ni fila ni archivos).
    repository = FakeDirectoryRepository(health_verified=True)
    storage = FakeStorage()
    app = directory_app(repository=repository, storage=storage)

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(status_payload())},
        files=photos_form(1),
    )

    assert response.status_code == 409
    body = response.json()
    assert body["title"] == "Caso verificado por el sector salud"
    assert repository.created_status_report is None
    assert storage.objects == {}


@pytest.mark.anyio
async def test_status_report_blocked_for_plain_account_too():
    # La restricción es de rol, no de sesión: una cuenta autenticada
    # sin rol de salud también queda bloqueada.
    repository = FakeDirectoryRepository(health_verified=True)
    app = directory_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers={
            **IDEMPOTENCY,
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": str(uuid4()),
        },
        data={"payload": json.dumps(status_payload())},
        files=photos_form(1),
    )

    assert response.status_code == 409
    assert repository.created_status_report is None


@pytest.mark.anyio
async def test_status_report_health_sector_can_still_report():
    # El sector salud no se bloquea a sí mismo: otra persona del
    # sector puede corregir el desenlace (CHG-077: su novedad más
    # reciente manda).
    repository = FakeDirectoryRepository(health_verified=True)
    app = directory_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers={
            **IDEMPOTENCY,
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": str(uuid4()),
            "X-Actor-Health": "true",
        },
        data={"payload": json.dumps(status_payload())},
        files=photos_form(1),
    )

    assert response.status_code == 202
    assert repository.created_status_report.reporter_health_sector is True


@pytest.mark.anyio
async def test_status_report_health_race_cleans_files():
    # La verificación llegó entre el pre-chequeo y la inserción: la
    # comprobación transaccional responde 409 y los archivos de este
    # intento se limpian.
    repository = FakeDirectoryRepository(health_verified_on_create=True)
    storage = FakeStorage()
    app = directory_app(repository=repository, storage=storage)

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={"payload": json.dumps(status_payload())},
        files=photos_form(2),
    )

    assert response.status_code == 409
    assert storage.objects == {}
    assert len(storage.deleted) == 4


# --- CHG-122: el desenlace fallecido es definitivo ---


@pytest.mark.anyio
async def test_deceased_case_rejects_found_even_from_health_sector():
    # El escenario de las capturas: salud reportó fallecida y otra
    # cuenta de salud intenta "encontrada". La máquina de estados lo
    # rechaza para todos los roles.
    repository = FakeDirectoryRepository(
        health_verified=True, public_status="deceased"
    )
    storage = FakeStorage()
    app = directory_app(repository=repository, storage=storage)

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers={
            **IDEMPOTENCY,
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": str(uuid4()),
            "X-Actor-Health": "true",
        },
        data={
            "payload": json.dumps(
                status_payload(claimedOutcome="found")
            )
        },
        files=photos_form(1),
    )

    assert response.status_code == 409
    assert response.json()["title"] == (
        "El desenlace fallecido es definitivo"
    )
    assert repository.created_status_report is None
    assert storage.objects == {}


@pytest.mark.anyio
async def test_deceased_case_rejects_found_from_anonymous():
    # Caso fallecido por umbral comunitario (sin novedad de salud que
    # active CHG-120): un anónimo tampoco puede volverlo "encontrada".
    repository = FakeDirectoryRepository(public_status="deceased")
    app = directory_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={
            "payload": json.dumps(
                status_payload(claimedOutcome="found")
            )
        },
        files=photos_form(1),
    )

    assert response.status_code == 409
    assert repository.created_status_report is None


@pytest.mark.anyio
async def test_deceased_case_accepts_deceased_confirmation():
    # Reportar `deceased` sobre un caso ya fallecido confirma, no
    # contradice: sigue permitido.
    repository = FakeDirectoryRepository(public_status="deceased")
    app = directory_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={
            "payload": json.dumps(
                status_payload(claimedOutcome="deceased")
            )
        },
        files=photos_form(1),
    )

    assert response.status_code == 202
    assert repository.created_status_report.claimed_outcome == "deceased"


@pytest.mark.anyio
async def test_found_case_still_accepts_deceased():
    # found → deceased es una transición válida de la máquina.
    repository = FakeDirectoryRepository(public_status="found")
    app = directory_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={
            "payload": json.dumps(
                status_payload(claimedOutcome="deceased")
            )
        },
        files=photos_form(1),
    )

    assert response.status_code == 202


@pytest.mark.anyio
async def test_deceased_race_cleans_files():
    # El fallecimiento se registró entre el pre-chequeo y la
    # inserción: la comprobación transaccional responde 409 y los
    # archivos de este intento se limpian.
    repository = FakeDirectoryRepository(deceased_on_create=True)
    storage = FakeStorage()
    app = directory_app(repository=repository, storage=storage)

    response = await request_app(
        app,
        "POST",
        STATUS_PATH,
        headers=IDEMPOTENCY,
        data={
            "payload": json.dumps(
                status_payload(claimedOutcome="found")
            )
        },
        files=photos_form(2),
    )

    assert response.status_code == 409
    assert storage.objects == {}
    assert len(storage.deleted) == 4


@pytest.mark.anyio
async def test_list_person_status_reports_public_projection():
    repository = FakeDirectoryRepository()
    app = directory_app(repository=repository)

    response = await request_app(app, "GET", STATUS_PATH)

    assert response.status_code == 200
    body = response.json()
    assert body["publicStatus"] == "found"
    assert body["total"] == 2
    first, second = body["items"]
    # La evidencia se muestra descifrada; el sector salud se distingue
    # y jamás viaja identidad del reportante ni fotografías.
    assert first["reporterKind"] == "health_sector"
    assert first["evidenceDescription"] == (
        "La vi en el albergue del colegio."
    )
    assert first["locationDescription"] == (
        "Albergue del colegio central"
    )
    assert second["reporterKind"] == "anonymous"
    assert second["moderationStatus"] == "accepted"
    assert set(first.keys()) == {
        "id", "claimedOutcome", "evidenceDescription",
        "locationDescription", "occurredAt", "receivedAt",
        "reporterKind", "moderationStatus",
    }


@pytest.mark.anyio
async def test_list_person_status_reports_unpublishable_is_404():
    repository = FakeDirectoryRepository(publishable_persons=set())
    app = directory_app(repository=repository)

    response = await request_app(app, "GET", STATUS_PATH)

    assert response.status_code == 404
