"""CHG-044 — Ofertas comunitarias de comida y alojamiento.

Cubre el ingreso autenticado e idempotente (cifrado con clave EXCLUSIVA,
huella de reuso, campos cruzados), la gestión del propietario (versión,
transiciones, 404 ajeno), la expiración idempotente, el directorio de
proyecciones y el bloqueo de aceptación (DEC-020/DEC-021).
"""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest

from app import offers
from app.config import Settings
from app.main import build_fernet, create_app
from app.models import (
    AidOfferReceipt,
    CommunityMealOfferDirectoryCard,
)
from app.repository import AidOfferIdempotencyConflictError


ACCOUNT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
AUTH_HEADERS = {
    "Idempotency-Key": "clave-idempotente-0044",
    "X-Actor-Kind": "authenticated",
    "X-Account-Id": ACCOUNT_ID,
    "Content-Type": "application/json",
}
OFFER_ID = UUID("10000000-0000-4000-8000-000000000001")


async def request_app(app, method, path, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)


def owner_row(**overrides) -> dict:
    row = {
        "id": OFFER_ID,
        "tracking_code": "AID-2026-ABCD1234",
        "kind": "community_meal",
        "title_encrypted": None,
        "moderation_status": "under_review",
        "availability_status": "scheduled",
        "available_units": 40,
        "capacity_unit": "servings",
        "available_from": datetime(2030, 8, 16, 16, 0, tzinfo=UTC),
        "available_until": datetime(2030, 8, 16, 20, 0, tzinfo=UTC),
        "received_at": datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
        "version": 1,
    }
    row.update(overrides)
    return row


class FakeAidOfferRepository:
    def __init__(
        self,
        duplicate: bool = False,
        conflict: bool = False,
        fail: bool = False,
        update_outcome: str = "ok",
        update_error: Exception | None = None,
    ):
        self.duplicate = duplicate
        self.conflict = conflict
        self.fail = fail
        self.update_outcome = update_outcome
        self.update_error = update_error
        self.created_offer = None
        self.created_meal = None
        self.created_shelter = None
        self.list_arguments = None
        self.update_arguments = None
        self.expire_calls = 0
        self.search_arguments = None
        self.rows = [owner_row()]

    async def ping(self) -> bool:
        return True

    async def create_aid_offer(self, offer, meal, shelter):
        if self.conflict:
            raise AidOfferIdempotencyConflictError()
        if self.fail:
            raise asyncpg.PostgresError("fallo simulado")
        if self.duplicate:
            return (
                AidOfferReceipt(
                    id=OFFER_ID,
                    tracking_code="AID-2026-ORIGINAL",
                    kind=offer.kind,
                    moderation_status="under_review",
                    availability_status="scheduled",
                    received_at=datetime(
                        2026, 8, 15, 12, 0, tzinfo=UTC
                    ),
                    version=1,
                ),
                False,
            )
        self.created_offer = offer
        self.created_meal = meal
        self.created_shelter = shelter
        return (
            AidOfferReceipt(
                id=offer.id,
                tracking_code=offer.tracking_code,
                kind=offer.kind,
                moderation_status="under_review",
                availability_status="scheduled",
                received_at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
                version=1,
            ),
            True,
        )

    async def list_owner_aid_offers(
        self, account_id, kind, moderation_status, limit, offset
    ):
        self.list_arguments = (
            account_id, kind, moderation_status, limit, offset
        )
        return self.rows, len(self.rows)

    async def update_owner_aid_offer(self, *arguments):
        self.update_arguments = arguments
        if self.update_error is not None:
            raise self.update_error
        if self.update_outcome != "ok":
            return self.update_outcome, None
        return "ok", owner_row(
            availability_status="paused", version=2
        )

    async def expire_aid_offers(self, batch_size):
        self.expire_calls += 1
        return 3 if self.expire_calls == 1 else 0

    async def search_directory_aid_offers(
        self, kind, query, department, limit, offset
    ):
        self.search_arguments = (kind, query, department, limit, offset)
        card = CommunityMealOfferDirectoryCard(
            id=uuid4(),
            public_offer_code="OFR-2026-PUB1",
            title="Almuerzos comunitarios",
            description="Raciones preparadas para personas afectadas.",
            area_reference="Sector norte",
            municipality="Bucaramanga",
            department="Santander",
            availability_status="active",
            available_from=datetime(2030, 8, 16, 16, 0, tzinfo=UTC),
            available_until=datetime(2030, 8, 16, 20, 0, tzinfo=UTC),
            servings_available=40,
            distribution_mode="pickup",
            meal_description="Arroz, legumbres y proteína",
            allergen_information=None,
            verification_status="verified",
            source={
                "name": "Oferta comunitaria — plataforma CUSOL",
                "sourceType": "citizen",
                "url": None,
            },
            updated_at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
            data_classification="demonstrative",
        )
        return ([card] if kind == "community_meal" else []), 1

    # CHG-036 — mínimos para el flujo administrativo.
    async def admin_get_submission_summary(self, submission_id):
        return {
            "id": submission_id,
            "kind": "community_meal_offer",
            "tracking_code": "AID-2026-ABCD1234",
            "title": "Oferta de comida comunitaria",
            "location_label": "Bucaramanga, Santander",
            "source_label": "Oferta con cuenta",
            "domain_status": "under_review",
            "needs_information": False,
            "archived_at": None,
            "received_at": datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
            "updated_at": datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
            "version": 1,
            "evidence_count": 0,
            "admin_status": "under_review",
        }


@pytest.fixture
def aid_key_file(tmp_path):
    path = tmp_path / "aid_offer_key"
    path.write_text("clave-exclusiva-de-ofertas-0044")
    path.chmod(0o600)
    return path


def offer_settings(aid_key_file) -> Settings:
    return Settings(
        database_url="postgresql://test:test@localhost:5432/test",
        database_pool_min_size=1,
        database_pool_max_size=2,
        aid_offer_encryption_key_file=str(aid_key_file),
    )


def offer_app(repository=None, settings=None):
    return create_app(
        settings=settings,
        repository=repository or FakeAidOfferRepository(),
    )


def meal_payload(**overrides) -> dict:
    payload = {
        "kind": "community_meal",
        "title": "Almuerzos comunitarios",
        "description": (
            "Raciones preparadas para personas afectadas por la "
            "emergencia."
        ),
        "department": "Santander",
        "municipality": "Bucaramanga",
        "areaReference": "Sector norte, punto exacto por coordinación",
        "availableFrom": "2030-08-16T16:00:00Z",
        "availableUntil": "2030-08-16T20:00:00Z",
        "contactName": "Persona de prueba",
        "contactEmail": "oferta@example.test",
        "servingsAvailable": 40,
        "distributionMode": "pickup",
        "mealDescription": "Arroz, legumbres y proteína cocida",
        "allergenInformation": "Puede contener soya",
        "foodSafetyConfirmed": True,
        "truthConfirmed": True,
        "contactConsent": True,
        "reviewAcknowledged": True,
        "publicSummaryConsent": True,
    }
    payload.update(overrides)
    return payload


def shelter_payload(**overrides) -> dict:
    payload = {
        "kind": "temporary_shelter",
        "title": "Espacio temporal para una familia",
        "description": (
            "Habitación disponible durante la respuesta a la "
            "emergencia."
        ),
        "department": "Santander",
        "municipality": "Bucaramanga",
        "areaReference": "Zona occidental, ubicación por coordinación",
        "availableFrom": "2030-08-16T22:00:00Z",
        "availableUntil": "2030-08-20T12:00:00Z",
        "contactName": "Persona de prueba",
        "contactPhone": "+57 300 000 0000",
        "spacesAvailable": 3,
        "sharedSpace": False,
        "acceptsPets": True,
        "accessibilityNotes": "Acceso con un escalón",
        "shelterSafetyConfirmed": True,
        "truthConfirmed": True,
        "contactConsent": True,
        "reviewAcknowledged": True,
        "publicSummaryConsent": True,
    }
    payload.update(overrides)
    return payload


async def post_offer(app, payload, headers=None):
    return await request_app(
        app,
        "POST",
        "/internal/v1/aid-offers",
        content=json.dumps(payload),
        headers=headers or AUTH_HEADERS,
    )


# --- Ingreso autenticado e idempotente ---


@pytest.mark.anyio
async def test_create_meal_offer_encrypts_and_returns_receipt(
    aid_key_file,
):
    repository = FakeAidOfferRepository()
    app = offer_app(repository, settings=offer_settings(aid_key_file))

    response = await post_offer(app, meal_payload())

    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "community_meal"
    assert body["moderationStatus"] == "under_review"
    assert body["availabilityStatus"] == "scheduled"
    assert body["version"] == 1
    assert body["trackingCode"].startswith("AID-")

    stored = repository.created_offer
    assert stored.account_id == UUID(ACCOUNT_ID)
    # La llave jamás en claro; huella del cuerpo presente.
    assert stored.idempotency_key_hash != AUTH_HEADERS["Idempotency-Key"]
    assert len(stored.request_fingerprint) == 64
    # Cifrado en reposo con la clave EXCLUSIVA de ofertas.
    fernet = build_fernet("clave-exclusiva-de-ofertas-0044")
    assert stored.title_encrypted != b"Almuerzos comunitarios"
    assert (
        fernet.decrypt(stored.title_encrypted).decode()
        == "Almuerzos comunitarios"
    )
    assert (
        fernet.decrypt(stored.contact_email_encrypted).decode()
        == "oferta@example.test"
    )
    assert repository.created_meal is not None
    assert repository.created_shelter is None
    assert repository.created_meal.servings_available == 40


@pytest.mark.anyio
async def test_create_shelter_offer_uses_shelter_detail(aid_key_file):
    repository = FakeAidOfferRepository()
    app = offer_app(repository, settings=offer_settings(aid_key_file))

    response = await post_offer(app, shelter_payload())

    assert response.status_code == 202
    assert repository.created_shelter is not None
    assert repository.created_meal is None
    assert repository.created_shelter.spaces_available == 3
    assert repository.created_shelter.shared_space is False


@pytest.mark.anyio
async def test_cross_kind_fields_are_rejected(aid_key_file):
    app = offer_app(settings=offer_settings(aid_key_file))

    response = await post_offer(
        app, meal_payload(spacesAvailable=3)
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_contact_and_window_validation(aid_key_file):
    app = offer_app(settings=offer_settings(aid_key_file))

    without_contact = meal_payload()
    del without_contact["contactEmail"]
    response = await post_offer(app, without_contact)
    assert response.status_code == 422

    inverted = meal_payload(
        availableFrom="2030-08-16T20:00:00Z",
        availableUntil="2030-08-16T16:00:00Z",
    )
    response = await post_offer(app, inverted)
    assert response.status_code == 422

    past = meal_payload(
        availableFrom="2020-08-16T16:00:00Z",
        availableUntil="2020-08-16T20:00:00Z",
    )
    response = await post_offer(app, past)
    assert response.status_code == 422
    assert "futuro" in response.json()["detail"]


@pytest.mark.anyio
async def test_coordinates_must_arrive_in_pairs(aid_key_file):
    app = offer_app(settings=offer_settings(aid_key_file))

    response = await post_offer(app, meal_payload(latitude=7.12))

    assert response.status_code == 422


@pytest.mark.anyio
async def test_anonymous_actor_is_rejected(aid_key_file):
    app = offer_app(settings=offer_settings(aid_key_file))

    headers = dict(AUTH_HEADERS)
    headers["X-Actor-Kind"] = "anonymous"
    del headers["X-Account-Id"]
    response = await post_offer(app, meal_payload(), headers=headers)

    assert response.status_code == 401


@pytest.mark.anyio
async def test_idempotency_key_is_required(aid_key_file):
    app = offer_app(settings=offer_settings(aid_key_file))

    headers = dict(AUTH_HEADERS)
    del headers["Idempotency-Key"]
    response = await post_offer(app, meal_payload(), headers=headers)

    assert response.status_code == 422


@pytest.mark.anyio
async def test_duplicate_retry_returns_original_receipt(aid_key_file):
    app = offer_app(
        FakeAidOfferRepository(duplicate=True),
        settings=offer_settings(aid_key_file),
    )

    response = await post_offer(app, meal_payload())

    assert response.status_code == 202
    assert response.json()["trackingCode"] == "AID-2026-ORIGINAL"


@pytest.mark.anyio
async def test_same_key_with_different_body_conflicts(aid_key_file):
    app = offer_app(
        FakeAidOfferRepository(conflict=True),
        settings=offer_settings(aid_key_file),
    )

    response = await post_offer(app, meal_payload())

    assert response.status_code == 409


@pytest.mark.anyio
async def test_missing_key_file_fails_readiness_and_writes(tmp_path):
    settings = Settings(
        database_url="postgresql://test:test@localhost:5432/test",
        database_pool_min_size=1,
        database_pool_max_size=2,
        aid_offer_encryption_key_file=str(tmp_path / "no-existe"),
    )
    app = offer_app(settings=settings)

    ready = await request_app(app, "GET", "/health/ready")
    assert ready.status_code == 503

    response = await post_offer(app, meal_payload())
    assert response.status_code == 503


@pytest.mark.anyio
async def test_insecure_key_permissions_fail(tmp_path):
    path = tmp_path / "aid_offer_key"
    path.write_text("clave-exclusiva-de-ofertas-0044")
    path.chmod(0o644)

    with pytest.raises(offers.AidOfferKeyError):
        offers.load_aid_offer_key(str(path))


# --- Gestión del propietario ---


@pytest.mark.anyio
async def test_owner_list_decrypts_title(aid_key_file):
    fernet = build_fernet("clave-exclusiva-de-ofertas-0044")
    repository = FakeAidOfferRepository()
    repository.rows = [
        owner_row(
            title_encrypted=fernet.encrypt(b"Almuerzos comunitarios")
        )
    ]
    app = offer_app(repository, settings=offer_settings(aid_key_file))

    response = await request_app(
        app,
        "GET",
        "/internal/v1/aid-offers?kind=community_meal&limit=10",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Almuerzos comunitarios"
    assert body["items"][0]["capacityUnit"] == "servings"
    assert repository.list_arguments[0] == UUID(ACCOUNT_ID)
    assert repository.list_arguments[1] == "community_meal"


@pytest.mark.anyio
async def test_owner_update_outcomes(aid_key_file):
    settings_value = offer_settings(aid_key_file)

    app = offer_app(
        FakeAidOfferRepository(update_outcome="not_found"),
        settings=settings_value,
    )
    response = await request_app(
        app,
        "PATCH",
        f"/internal/v1/aid-offers/{OFFER_ID}",
        content=json.dumps(
            {"version": 1, "availabilityStatus": "paused"}
        ),
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 404

    app = offer_app(
        FakeAidOfferRepository(update_outcome="version_conflict"),
        settings=settings_value,
    )
    response = await request_app(
        app,
        "PATCH",
        f"/internal/v1/aid-offers/{OFFER_ID}",
        content=json.dumps(
            {"version": 1, "availabilityStatus": "paused"}
        ),
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 409

    app = offer_app(
        FakeAidOfferRepository(
            update_error=offers.OwnerTransitionError("terminal")
        ),
        settings=settings_value,
    )
    response = await request_app(
        app,
        "PATCH",
        f"/internal/v1/aid-offers/{OFFER_ID}",
        content=json.dumps(
            {"version": 1, "availabilityStatus": "active"}
        ),
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 409

    repository = FakeAidOfferRepository()
    app = offer_app(repository, settings=settings_value)
    response = await request_app(
        app,
        "PATCH",
        f"/internal/v1/aid-offers/{OFFER_ID}",
        content=json.dumps(
            {"version": 1, "availabilityStatus": "paused"}
        ),
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["availabilityStatus"] == "paused"
    assert response.json()["version"] == 2
    assert repository.update_arguments[0] == UUID(ACCOUNT_ID)


@pytest.mark.anyio
async def test_owner_update_requires_some_field(aid_key_file):
    app = offer_app(settings=offer_settings(aid_key_file))

    response = await request_app(
        app,
        "PATCH",
        f"/internal/v1/aid-offers/{OFFER_ID}",
        content=json.dumps({"version": 1}),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 422


# --- Reglas puras de transición (FR-008) ---


def test_zero_units_close_as_fulfilled():
    availability, units = offers.resolve_owner_update(
        "community_meal", "active", None, 0
    )
    assert availability == "fulfilled"
    assert units == 0


def test_zero_units_with_other_status_is_invalid():
    with pytest.raises(offers.OwnerUpdateInvalidError):
        offers.resolve_owner_update(
            "community_meal", "active", "active", 0
        )


def test_terminal_states_reject_updates():
    for terminal in ("fulfilled", "withdrawn", "expired"):
        with pytest.raises(offers.OwnerTransitionError):
            offers.resolve_owner_update(
                "community_meal", terminal, "active", None
            )


def test_active_cannot_return_to_scheduled():
    with pytest.raises(offers.OwnerTransitionError):
        offers.resolve_owner_update(
            "temporary_shelter", "active", "scheduled", None
        )


def test_shelter_units_capped_at_1000():
    with pytest.raises(offers.OwnerUpdateInvalidError):
        offers.resolve_owner_update(
            "temporary_shelter", "active", None, 5_000
        )


# --- Expiración idempotente ---


@pytest.mark.anyio
async def test_expiration_endpoint_is_idempotent(aid_key_file):
    repository = FakeAidOfferRepository()
    app = offer_app(repository, settings=offer_settings(aid_key_file))

    first = await request_app(
        app, "POST", "/internal/v1/aid-offers/expirations"
    )
    second = await request_app(
        app, "POST", "/internal/v1/aid-offers/expirations"
    )

    assert first.status_code == 200
    assert first.json() == {"expired": 3}
    assert second.json() == {"expired": 0}


# --- Directorio humanitario ---


@pytest.mark.anyio
async def test_directory_search_returns_offer_cards(aid_key_file):
    repository = FakeAidOfferRepository()
    app = offer_app(repository, settings=offer_settings(aid_key_file))

    response = await request_app(
        app,
        "GET",
        "/internal/v1/humanitarian-directory/search"
        "?kind=community_meal&q=almuerzo",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "community_meal"
    item = body["items"][0]
    assert item["publicOfferCode"] == "OFR-2026-PUB1"
    # Jamás datos privados en la tarjeta pública.
    assert "contactName" not in item
    assert "exactAddress" not in item
    assert "accountId" not in item
    assert repository.search_arguments[0] == "community_meal"


@pytest.mark.anyio
async def test_directory_offer_kind_rejects_foreign_filters(
    aid_key_file,
):
    app = offer_app(settings=offer_settings(aid_key_file))

    response = await request_app(
        app,
        "GET",
        "/internal/v1/humanitarian-directory/search"
        "?kind=temporary_shelter&q=espacio&minRating=3",
    )

    assert response.status_code == 422


# --- Aceptación bloqueada (DEC-020/DEC-021) ---


@pytest.mark.anyio
async def test_admin_accept_offer_is_blocked(aid_key_file):
    app = offer_app(settings=offer_settings(aid_key_file))

    response = await request_app(
        app,
        "POST",
        f"/internal/v1/admin/submissions/{OFFER_ID}/decisions",
        content=json.dumps(
            {
                "expectedVersion": 1,
                "action": "accept",
                "reason": "verificación completada",
            }
        ),
        headers={
            "Content-Type": "application/json",
            "X-Actor-Role": "super_admin",
            "X-Actor-Account-Id": ACCOUNT_ID,
            "X-Actor-Display": "U3VwZXJhZG1pbg==",
        },
    )

    assert response.status_code == 409
    assert "DEC-020" in response.json()["detail"]
