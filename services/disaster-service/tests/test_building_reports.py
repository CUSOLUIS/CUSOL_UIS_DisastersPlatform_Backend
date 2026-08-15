"""CHG-035 — Reporte ciudadano de edificio sin verificar.

Cubre el expediente privado multipart (validación, cifrado, cuarentena,
idempotencia hasheada, rollback compensable) y las reglas puras de la
proyección `building_pending`, que permanece desactivada (DEC-012–014).
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest

from app import moderation
from app.config import Settings
from app.main import (
    BUILDING_REPORT_LEGAL_TEXT_VERSION,
    build_fernet,
    create_app,
)
from app.models import (
    DisasterEventSuggestion,
    UnverifiedBuildingReportReceipt,
)

from test_missing_persons import (
    EICAR,
    FakeStorage,
    make_jpeg,
    request_app,
)


FERNET = build_fernet(Settings.from_environment().report_encryption_key)
PATH = "/internal/v1/unverified-building-reports"
IDEMPOTENCY = {"Idempotency-Key": "clave-idempotente-0035"}


class FakeBuildingRepository:
    def __init__(self, duplicate: bool = False, fail: bool = False,
                 fk_violation: bool = False):
        self.duplicate = duplicate
        self.fail = fail
        self.fk_violation = fk_violation
        self.created_report = None
        self.created_files = None
        self.map_writes = 0

    async def ping(self) -> bool:
        return True

    async def create_unverified_building_report(
        self, report, files, related_event_name=None
    ):
        self.last_related_event_name = related_event_name
        if self.fk_violation:
            raise asyncpg.ForeignKeyViolationError("fk simulada")
        if self.fail:
            raise asyncpg.PostgresError("fallo simulado")
        if self.duplicate:
            return (
                UnverifiedBuildingReportReceipt(
                    id=UUID("99999999-9999-4999-8999-999999999903"),
                    public_tracking_code="BR-2026-ORIGINAL",
                    status="under_review",
                    received_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
                ),
                False,
            )
        self.created_report = report
        self.created_files = files
        return (
            UnverifiedBuildingReportReceipt(
                id=report.id,
                public_tracking_code=report.public_tracking_code,
                status="under_review",
                received_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
            ),
            True,
        )


def building_app(repository=None, storage=None):
    return create_app(
        repository=repository or FakeBuildingRepository(),
        storage=storage if storage is not None else FakeStorage(),
    )


def building_payload(**overrides) -> dict:
    payload = {
        "buildingReference": "Edificio Torre Norte",
        "buildingType": "residential",
        "department": "Santander",
        "municipality": "Bucaramanga",
        "sector": "Barrio Colorados",
        "locationReference": "Frente al parque principal, costado norte",
        "observedDate": "2026-08-13",
        "searchStatus": "not_started",
        "occupancyReport": "unknown",
        "pendingReasons": ["access_blocked", "debris"],
        "observedConditions": ["visible_debris"],
        "observationDescription": (
            "Acceso bloqueado por escombros; no se observó ingreso de "
            "cuadrillas desde la inundación."
        ),
        "reporterName": "Reportante Demo",
        "reporterRole": "Vecino del sector",
        "reporterPhone": "+57 300 000 0000",
        "truthConfirmed": True,
        "photoAuthorizationConfirmed": True,
        "reviewAcknowledged": True,
    }
    payload.update(overrides)
    return payload


def photos_form(count: int, photo: bytes | None = None):
    content = photo if photo is not None else make_jpeg()
    return [
        ("photos", (f"fachada-{index}.jpg", content, "image/jpeg"))
        for index in range(count)
    ]


async def post_report(app, payload=None, photos=1, headers=None,
                      files=None):
    return await request_app(
        app,
        "POST",
        PATH,
        headers=headers if headers is not None else IDEMPOTENCY,
        data={
            "payload": json.dumps(
                payload if payload is not None else building_payload()
            )
        },
        files=files if files is not None else photos_form(photos),
    )


# --- Recepción y privacidad ---


@pytest.mark.anyio
async def test_report_creates_private_case_with_receipt():
    repository = FakeBuildingRepository()
    storage = FakeStorage()
    app = building_app(repository=repository, storage=storage)

    response = await post_report(app, photos=2)

    assert response.status_code == 201
    body = response.json()
    # La respuesta expone únicamente el comprobante.
    assert set(body.keys()) == {
        "id", "publicTrackingCode", "status", "receivedAt"
    }
    assert body["status"] == "under_review"
    assert body["publicTrackingCode"].startswith("BR-")

    stored = repository.created_report
    # Aleatorio, no secuencial, y la llave solo hasheada.
    assert stored.public_tracking_code.startswith("BR-2026-")
    assert stored.idempotency_key_hash == hashlib.sha256(
        IDEMPOTENCY["Idempotency-Key"].encode()
    ).hexdigest()
    assert stored.legal_text_version == (
        BUILDING_REPORT_LEGAL_TEXT_VERSION
    )
    assert stored.actor_account_id is None
    assert stored.truth_confirmed_at is not None
    # Campos protegidos cifrados con descifrado íntegro.
    assert FERNET.decrypt(
        stored.location_reference_protected
    ).decode() == building_payload()["locationReference"]
    assert FERNET.decrypt(
        stored.reporter_phone_protected
    ).decode() == building_payload()["reporterPhone"]
    assert stored.reporter_email_protected is None
    assert stored.address_protected is None
    assert stored.latitude_protected is None
    # Evidencia: original + derivado por foto, claves opacas sin
    # nombre original.
    assert len(repository.created_files) == 2
    assert len(storage.objects) == 4
    assert all(
        key.startswith("unverified-building-reports/")
        and "fachada" not in key
        for key in storage.objects
    )
    # El mapa operativo jamás se toca al crear.
    assert repository.map_writes == 0


@pytest.mark.anyio
async def test_report_encrypts_exact_coordinates():
    repository = FakeBuildingRepository()
    app = building_app(repository=repository)

    response = await post_report(
        app,
        payload=building_payload(
            latitude=7.113256, longitude=-73.119847,
            address="Calle 45 # 12-34, apto 301",
        ),
    )

    assert response.status_code == 201
    stored = repository.created_report
    assert FERNET.decrypt(
        stored.latitude_protected
    ).decode() == repr(7.113256)
    assert FERNET.decrypt(
        stored.longitude_protected
    ).decode() == repr(-73.119847)
    assert FERNET.decrypt(stored.address_protected).decode() == (
        "Calle 45 # 12-34, apto 301"
    )


@pytest.mark.anyio
async def test_report_response_never_echoes_private_fields():
    app = building_app()

    response = await post_report(
        app,
        payload=building_payload(
            address="Calle 45 # 12-34", latitude=7.11, longitude=-73.12
        ),
    )

    text = response.text
    for private in (
        "Calle 45", "7.11", "-73.12", "Reportante Demo",
        "+57 300", "Frente al parque",
    ):
        assert private not in text


# --- Validaciones ---


@pytest.mark.anyio
async def test_report_requires_contact():
    app = building_app()

    response = await post_report(
        app, payload=building_payload(reporterPhone=None)
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_report_accepts_email_as_only_contact():
    app = building_app()

    response = await post_report(
        app,
        payload=building_payload(
            reporterPhone=None, reporterEmail="demo@cusol.local"
        ),
    )

    assert response.status_code == 201


@pytest.mark.anyio
async def test_report_requires_coordinates_in_pairs():
    app = building_app()

    response = await post_report(
        app, payload=building_payload(latitude=7.11)
    )

    assert response.status_code == 422
    assert "7.11" not in response.json()["detail"]


@pytest.mark.anyio
async def test_report_rejects_future_date_and_bad_time():
    app = building_app()
    future = (
        datetime.now(UTC).date() + timedelta(days=1)
    ).isoformat()

    future_date = await post_report(
        app, payload=building_payload(observedDate=future)
    )
    bad_time = await post_report(
        app, payload=building_payload(observedTime="25:99")
    )

    assert future_date.status_code == 422
    assert bad_time.status_code == 422


@pytest.mark.anyio
async def test_report_rejects_empty_or_duplicated_reasons():
    app = building_app()

    empty = await post_report(
        app, payload=building_payload(pendingReasons=[])
    )
    duplicated = await post_report(
        app,
        payload=building_payload(
            pendingReasons=["debris", "debris"]
        ),
    )
    unknown = await post_report(
        app, payload=building_payload(pendingReasons=["invented"])
    )

    assert empty.status_code == 422
    assert duplicated.status_code == 422
    assert unknown.status_code == 422


@pytest.mark.anyio
async def test_report_requires_all_consents():
    app = building_app()

    response = await post_report(
        app, payload=building_payload(truthConfirmed=False)
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_report_requires_one_to_three_photos():
    # CHG-071: el máximo bajó a 3 fotografías por reporte.
    app = building_app()

    without_photos = await post_report(app, files=[])
    too_many = await post_report(app, files=photos_form(4))
    three = await post_report(app, files=photos_form(3))

    assert without_photos.status_code == 422
    assert too_many.status_code == 422
    assert three.status_code == 201


@pytest.mark.anyio
async def test_report_rejects_malware_and_fake_images():
    app = building_app()

    eicar = await post_report(
        app, files=photos_form(1, photo=make_jpeg() + EICAR)
    )
    fake = await post_report(
        app,
        files=[
            ("photos", ("doc.txt", b"no soy una imagen" * 4,
                        "image/jpeg"))
        ],
    )

    assert eicar.status_code == 415
    assert fake.status_code == 415


@pytest.mark.anyio
async def test_report_requires_idempotency_key():
    app = building_app()

    response = await post_report(
        app, headers={"Idempotency-Key": "corta"}
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_report_rejects_unknown_related_disaster():
    storage = FakeStorage()
    app = building_app(
        repository=FakeBuildingRepository(fk_violation=True),
        storage=storage,
    )

    response = await post_report(
        app, payload=building_payload(relatedDisasterId=str(uuid4()))
    )

    assert response.status_code == 422
    assert "relatedDisasterId" in response.json()["detail"]
    assert storage.objects == {}


# --- Idempotencia y rollback compensable ---


@pytest.mark.anyio
async def test_report_idempotent_retry_returns_same_receipt():
    storage = FakeStorage()
    app = building_app(
        repository=FakeBuildingRepository(duplicate=True),
        storage=storage,
    )

    response = await post_report(app, photos=2)

    assert response.status_code == 201
    assert response.json()["publicTrackingCode"] == "BR-2026-ORIGINAL"
    # Los objetos del reintento se compensan.
    assert storage.objects == {}
    assert len(storage.deleted) == 4


@pytest.mark.anyio
async def test_report_database_failure_compensates_objects():
    storage = FakeStorage()
    app = building_app(
        repository=FakeBuildingRepository(fail=True), storage=storage
    )

    response = await post_report(app, photos=3)

    assert response.status_code == 503
    assert storage.objects == {}
    assert len(storage.deleted) == 6


@pytest.mark.anyio
async def test_report_storage_failure_registers_nothing():
    repository = FakeBuildingRepository()
    app = building_app(
        repository=repository, storage=FakeStorage(fail=True)
    )

    response = await post_report(app)

    assert response.status_code == 503
    assert repository.created_report is None


# --- Proyección building_pending (reglas puras; publicación gated) ---


def test_projection_degrades_coordinates():
    assert moderation.degrade_coordinates(7.113256, -73.119847) == (
        7.11, -73.12
    )


def test_projection_labels_are_sanitized():
    title, label = moderation.build_building_projection_labels(
        " Barrio Colorados ", "Bucaramanga", "Santander"
    )
    assert title == (
        "Edificio sin inspección registrada — Barrio Colorados"
    )
    assert label == "Bucaramanga, Santander"
    # Nunca dirección, contacto ni texto libre.
    for value in (title, label):
        assert "Calle" not in value
        assert "@" not in value


def test_building_moderation_reuses_shared_transitions():
    assert moderation.can_decide("under_review", "accepted")
    assert moderation.can_decide("under_review", "rejected")
    assert moderation.can_decide("accepted", "withdrawn")
    assert not moderation.can_decide("rejected", "accepted")
    with pytest.raises(moderation.ModerationNotAllowedError):
        moderation.ensure_moderator_role("user")


# --- CHG-092: "Evento relacionado" creable ---


@pytest.mark.anyio
async def test_related_event_name_reaches_repository():
    repository = FakeBuildingRepository()
    app = building_app(repository=repository)

    response = await post_report(
        app,
        payload=building_payload(relatedEventName="Sismo en el Centro"),
    )

    assert response.status_code == 201
    assert repository.last_related_event_name == "Sismo en el Centro"
    # El expediente no lleva id: lo resuelve la transacción del repo.
    assert repository.created_report.related_disaster_id is None


@pytest.mark.anyio
async def test_related_event_name_and_id_are_exclusive():
    app = building_app()

    response = await post_report(
        app,
        payload=building_payload(
            relatedEventName="Sismo en el Centro",
            relatedDisasterId="55555555-5555-4555-8555-555555555501",
        ),
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_event_autocomplete_returns_suggestions():
    class EventRepository(FakeBuildingRepository):
        async def autocomplete_disaster_events(self, query, limit):
            self.last_event_autocomplete = {
                "query": query,
                "limit": limit,
            }
            return [
                DisasterEventSuggestion(
                    id=UUID("77777777-7777-4777-8777-777777777701"),
                    title="Sismo en el Centro",
                    disaster_type="earthquake",
                    verification_status="verified",
                    occurred_at=datetime(2026, 8, 10, 6, 0, tzinfo=UTC),
                    similarity=0.9,
                )
            ]

    repository = EventRepository()
    app = building_app(repository=repository)

    response = await request_app(
        app,
        "GET",
        "/internal/v1/disaster-events/autocomplete?q=sismo&limit=5",
    )

    assert response.status_code == 200
    body = response.json()
    assert repository.last_event_autocomplete == {
        "query": "sismo",
        "limit": 5,
    }
    item = body["items"][0]
    assert item["title"] == "Sismo en el Centro"
    assert item["similarity"] == 0.9
    assert item["verificationStatus"] == "verified"
