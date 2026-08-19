"""CHG-165 — Comentarios, denuncias con umbral de deshabilitación y
consola de verificación de Centros de Acopio Local (disaster-service).

El repositorio falso replica las reglas de negocio (orden DESC, actor
anónimo con account NULL, umbral 10 → observación / 20 → deshabilitado,
ciclo reiniciable al reactivar) para probar la capa de endpoints:
validación, actores, roles y recibos.
"""

import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.main import create_app
from app.models import (
    AID_LOCATION_DISABLE_THRESHOLD,
    AID_LOCATION_REPORT_THRESHOLD,
)

from test_missing_persons import FakeStorage, request_app

LOCATION_ID = UUID("88888888-8888-4888-8888-888888888801")
ACTOR_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
BASE_AT = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)

ADMIN_HEADERS = {
    "X-Actor-Role": "super_admin",
    "X-Actor-Account-Id": str(ACTOR_ID),
    "X-Actor-Display": base64.b64encode("Admin CUSOL".encode()).decode(),
}
MODERATOR_HEADERS = {**ADMIN_HEADERS, "X-Actor-Role": "moderator"}


def idempotency(suffix: str) -> dict:
    return {"Idempotency-Key": f"clave-idempotente-165-{suffix:>04}"}


def center_row(**overrides) -> dict:
    row = {
        "id": LOCATION_ID,
        "kind": "collection_center",
        "name": "Acopio La Feria",
        "location_label": "Calle 10 # 5-51",
        "municipality": "Bucaramanga",
        "department": "Santander",
        "latitude": 7.1,
        "longitude": -73.1,
        "description": "Recibe alimentos no perecederos.",
        "schedule": None,
        "contact": None,
        "created_at": BASE_AT,
        "created_by_account_id": None,
        "verification_status": "unverified",
        "operational_status": "open",
        "disabled_at": None,
        "verified_at": None,
    }
    row.update(overrides)
    return row


class FakeCommunityRepository:
    """Réplica en memoria de las reglas CHG-153/CHG-165."""

    def __init__(self, *, center=None, live_reports=0):
        self.center = center or center_row()
        self.comments: list[dict] = []
        self.reports: list[dict] = []
        self.audit: list[tuple[str, str]] = []
        self.admin_audit: list[str] = []
        for index in range(live_reports):
            self.reports.append(
                {
                    "denouncer_key": f"fp:previo-{index}",
                    "reason_category": "otro",
                    "archived_at": None,
                }
            )

    # --- comentarios ---

    async def list_aid_location_comments(self, *, location_id, limit):
        if location_id != self.center["id"]:
            return None
        ordered = sorted(
            self.comments, key=lambda c: c["created_at"], reverse=True
        )
        # CHG-166: promedio solo sobre quienes calificaron.
        rated = [c["rating"] for c in self.comments if c["rating"]]
        return {
            "items": ordered[:limit],
            "total": len(self.comments),
            "rating_average": (
                round(sum(rated) / len(rated), 1) if rated else None
            ),
            "rating_count": len(rated),
        }

    async def create_aid_location_comment(
        self,
        *,
        idempotency_key,
        location_id,
        actor_kind,
        account_id,
        author_display_name,
        content,
        rating,
    ):
        if location_id != self.center["id"]:
            return None
        for existing in self.comments:
            if existing["idempotency_key"] == idempotency_key:
                return existing
        row = {
            "id": uuid4(),
            "idempotency_key": idempotency_key,
            "account_id": account_id,
            "author_display_name": author_display_name,
            "actor_kind": actor_kind,
            "content": content,
            "rating": rating,
            "created_at": BASE_AT + timedelta(minutes=len(self.comments)),
        }
        self.comments.append(row)
        return row

    # CHG-167: borrado admin definitivo y auditado.
    async def admin_delete_aid_location_comment(
        self,
        *,
        location_id,
        comment_id,
        actor_account_id,
        actor_display_name,
    ):
        if location_id != self.center["id"]:
            return 0
        for comment in self.comments:
            if comment["id"] == comment_id:
                self.comments.remove(comment)
                self.admin_audit.append("aid_location_comment_deleted")
                return 1
        return 0

    # --- denuncias ---

    def _live_count(self) -> int:
        return sum(
            1 for r in self.reports if r["archived_at"] is None
        )

    async def create_aid_location_report(
        self,
        *,
        idempotency_key,
        location_id,
        actor_kind,
        account_id,
        denouncer_key,
        reason_category,
        reason_encrypted,
    ):
        if location_id != self.center["id"]:
            return None
        if not any(
            r["denouncer_key"] == denouncer_key
            and r["archived_at"] is None
            for r in self.reports
        ):
            self.reports.append(
                {
                    "denouncer_key": denouncer_key,
                    "reason_category": reason_category,
                    "archived_at": None,
                }
            )
            self.audit.append(
                ("aid_location_report_received", reason_category)
            )
        count = self._live_count()
        under_observation = (
            self.center["operational_status"] == "under_observation"
        )
        disabled = self.center["disabled_at"] is not None
        if (
            count >= AID_LOCATION_REPORT_THRESHOLD
            and self.center["operational_status"]
            not in ("under_observation", "inactive")
        ):
            self.center["operational_status"] = "under_observation"
            under_observation = True
        if (
            count >= AID_LOCATION_DISABLE_THRESHOLD
            and self.center["disabled_at"] is None
        ):
            self.center["operational_status"] = "inactive"
            self.center["disabled_at"] = BASE_AT
            disabled = True
            under_observation = False
            self.audit.append(
                ("aid_location_disabled_by_reports", str(count))
            )
        return {
            "id": location_id,
            "reports_count": count,
            "under_observation": under_observation,
            "disabled": disabled,
        }

    # --- consola super_admin ---

    def _summary(self) -> dict:
        return {
            **self.center,
            "active_reports_count": self._live_count(),
        }

    async def admin_list_aid_location_verifications(self):
        pending = (
            [self._summary()]
            if self.center["verification_status"] == "unverified"
            else []
        )
        disabled = (
            [self._summary()]
            if self.center["disabled_at"] is not None
            else []
        )
        return {"pending": pending, "disabled": disabled}

    async def admin_decide_aid_location_verification(
        self,
        *,
        location_id,
        decision,
        actor_account_id,
        actor_display_name,
        reason_encrypted,
    ):
        if location_id != self.center["id"]:
            return None
        self.center["verification_status"] = (
            "verified" if decision == "approve" else "rejected"
        )
        self.center["verified_at"] = BASE_AT
        self.admin_audit.append(
            "aid_location_verification_approved"
            if decision == "approve"
            else "aid_location_verification_rejected"
        )
        return self._summary()

    async def admin_reactivate_aid_location(
        self,
        *,
        location_id,
        actor_account_id,
        actor_display_name,
        reason_encrypted,
    ):
        if location_id != self.center["id"]:
            return None
        if self.center["disabled_at"] is None:
            return "not_disabled"
        for report in self.reports:
            if report["archived_at"] is None:
                report["archived_at"] = BASE_AT
        self.center["operational_status"] = "open"
        self.center["disabled_at"] = None
        self.admin_audit.append("aid_location_reactivated")
        return self._summary()


def community_app(repository=None):
    return create_app(
        repository=repository or FakeCommunityRepository(),
        storage=FakeStorage(),
    )


COMMENTS_PATH = f"/internal/v1/aid-locations/{LOCATION_ID}/comments"
REPORTS_PATH = f"/internal/v1/aid-locations/{LOCATION_ID}/reports"
VERIFICATIONS_PATH = "/internal/v1/admin/aid-locations/verifications"
VERIFICATION_PATH = (
    f"/internal/v1/admin/aid-locations/{LOCATION_ID}/verification"
)
REACTIVATE_PATH = (
    f"/internal/v1/admin/aid-locations/{LOCATION_ID}/reactivate"
)


# --- comentarios (§40 tests 1-3) -------------------------------------


@pytest.mark.anyio
async def test_authenticated_comment_keeps_author_and_date():
    repository = FakeCommunityRepository()
    app = community_app(repository)

    response = await request_app(
        app,
        "POST",
        COMMENTS_PATH,
        headers={
            **idempotency("c1"),
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": str(ACTOR_ID),
            "X-Actor-Display": base64.b64encode(
                "María Gómez".encode()
            ).decode(),
        },
        json={"content": "Hay disponibilidad para recibir ropa.", "rating": 5},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["authorDisplayName"] == "María Gómez"
    assert body["actorKind"] == "authenticated"
    assert body["createdAt"]
    # CHG-166: la calificación queda persistida con el comentario.
    assert body["rating"] == 5
    assert repository.comments[0]["account_id"] == ACTOR_ID
    assert repository.comments[0]["rating"] == 5


@pytest.mark.anyio
async def test_anonymous_comment_stores_null_account():
    repository = FakeCommunityRepository()
    app = community_app(repository)

    response = await request_app(
        app,
        "POST",
        COMMENTS_PATH,
        headers={**idempotency("c2"), "X-Actor-Kind": "anonymous"},
        json={
            "content": "Acabo de entregar varias cajas en este punto.",
            "rating": 4,
        },
    )

    assert response.status_code == 201
    body = response.json()
    # §7: relación nula, jamás el texto "anonymous" como usuario.
    assert body["authorDisplayName"] is None
    assert body["actorKind"] == "anonymous"
    assert repository.comments[0]["account_id"] is None


@pytest.mark.anyio
async def test_comments_come_back_newest_first():
    repository = FakeCommunityRepository()
    app = community_app(repository)

    for index, (text, stars) in enumerate(
        [
            ("Primer comentario del punto de acopio.", 5),
            ("Segundo comentario del punto de acopio.", 4),
            ("Tercer comentario del punto de acopio.", 3),
        ]
    ):
        created = await request_app(
            app,
            "POST",
            COMMENTS_PATH,
            headers={
                **idempotency(f"o{index}"),
                "X-Actor-Kind": "anonymous",
            },
            json={"content": text, "rating": stars},
        )
        assert created.status_code == 201

    listing = await request_app(app, "GET", COMMENTS_PATH)
    assert listing.status_code == 200
    contents = [item["content"] for item in listing.json()["items"]]
    assert contents == [
        "Tercer comentario del punto de acopio.",
        "Segundo comentario del punto de acopio.",
        "Primer comentario del punto de acopio.",
    ]
    assert listing.json()["total"] == 3
    # CHG-166: promedio server-side (5+4+3)/3 = 4.0 sobre 3 calificados.
    assert listing.json()["ratingAverage"] == 4.0
    assert listing.json()["ratingCount"] == 3


@pytest.mark.anyio
async def test_comment_without_rating_is_invalid():
    # CHG-166: los comentarios nuevos exigen la calificación 1-5.
    app = community_app()

    response = await request_app(
        app,
        "POST",
        COMMENTS_PATH,
        headers={**idempotency("nr"), "X-Actor-Kind": "anonymous"},
        json={"content": "Comentario sin estrellas del punto."},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_comment_rejects_gibberish_and_missing_location():
    app = community_app()

    gibberish = await request_app(
        app,
        "POST",
        COMMENTS_PATH,
        headers={**idempotency("g1"), "X-Actor-Kind": "anonymous"},
        json={"content": "aaaaaaaaaaaaaaaa"},
    )
    assert gibberish.status_code == 422

    missing = await request_app(
        app,
        "GET",
        f"/internal/v1/aid-locations/{uuid4()}/comments",
    )
    assert missing.status_code == 404


# --- denuncias (§40 tests 4-5) ---------------------------------------


@pytest.mark.anyio
async def test_report_persists_category_and_description():
    repository = FakeCommunityRepository()
    app = community_app(repository)

    response = await request_app(
        app,
        "POST",
        REPORTS_PATH,
        headers={
            **idempotency("r1"),
            "X-Actor-Kind": "anonymous",
            "X-Denouncer-Key": "fp:huella-1",
        },
        json={
            "category": "no_existe",
            "reason": "Fui al lugar y no existe tal centro.",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["reportsCount"] == 1
    assert body["underObservation"] is False
    assert body["disabled"] is False
    assert repository.reports[-1]["reason_category"] == "no_existe"
    assert repository.audit[-1][0] == "aid_location_report_received"


@pytest.mark.anyio
async def test_report_without_category_is_invalid():
    app = community_app()

    response = await request_app(
        app,
        "POST",
        REPORTS_PATH,
        headers={
            **idempotency("r2"),
            "X-Actor-Kind": "anonymous",
            "X-Denouncer-Key": "fp:huella-2",
        },
        json={"reason": "Denuncia sin motivo del selector."},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_twentieth_report_disables_the_center():
    repository = FakeCommunityRepository(
        center=center_row(operational_status="under_observation"),
        live_reports=AID_LOCATION_DISABLE_THRESHOLD - 1,
    )
    app = community_app(repository)

    response = await request_app(
        app,
        "POST",
        REPORTS_PATH,
        headers={
            **idempotency("r3"),
            "X-Actor-Kind": "anonymous",
            "X-Denouncer-Key": "fp:huella-20",
        },
        json={
            "category": "funcionamiento_irregular",
            "reason": "El punto lleva días sin atender a nadie.",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["reportsCount"] == AID_LOCATION_DISABLE_THRESHOLD
    assert body["disabled"] is True
    assert repository.center["operational_status"] == "inactive"
    assert repository.center["disabled_at"] is not None
    assert (
        "aid_location_disabled_by_reports"
        in [event for event, _ in repository.audit]
    )


# --- consola super_admin (§40 tests 6-10) ----------------------------


@pytest.mark.anyio
async def test_admin_routes_reject_missing_or_insufficient_role():
    app = community_app()

    without_role = await request_app(app, "GET", VERIFICATIONS_PATH)
    as_moderator = await request_app(
        app, "GET", VERIFICATIONS_PATH, headers=MODERATOR_HEADERS
    )
    reactivate_as_moderator = await request_app(
        app, "POST", REACTIVATE_PATH, headers=MODERATOR_HEADERS
    )
    decide_as_moderator = await request_app(
        app,
        "POST",
        VERIFICATION_PATH,
        headers=MODERATOR_HEADERS,
        json={"decision": "approve"},
    )

    assert without_role.status_code == 403
    assert as_moderator.status_code == 403
    assert reactivate_as_moderator.status_code == 403
    assert decide_as_moderator.status_code == 403


@pytest.mark.anyio
async def test_verifications_listing_shows_pending_and_disabled():
    repository = FakeCommunityRepository(
        center=center_row(
            operational_status="inactive", disabled_at=BASE_AT
        ),
        live_reports=AID_LOCATION_DISABLE_THRESHOLD,
    )
    app = community_app(repository)

    response = await request_app(
        app, "GET", VERIFICATIONS_PATH, headers=ADMIN_HEADERS
    )

    assert response.status_code == 200
    body = response.json()
    assert body["pending"][0]["name"] == "Acopio La Feria"
    assert body["pending"][0]["verificationStatus"] == "unverified"
    assert body["disabled"][0]["activeReportsCount"] == (
        AID_LOCATION_DISABLE_THRESHOLD
    )


@pytest.mark.anyio
async def test_approving_verification_marks_verified_and_audits():
    repository = FakeCommunityRepository()
    app = community_app(repository)

    response = await request_app(
        app,
        "POST",
        VERIFICATION_PATH,
        headers=ADMIN_HEADERS,
        json={"decision": "approve"},
    )

    assert response.status_code == 200
    assert response.json()["verificationStatus"] == "verified"
    assert repository.admin_audit == [
        "aid_location_verification_approved"
    ]


@pytest.mark.anyio
async def test_rejecting_verification_marks_rejected():
    repository = FakeCommunityRepository()
    app = community_app(repository)

    response = await request_app(
        app,
        "POST",
        VERIFICATION_PATH,
        headers=ADMIN_HEADERS,
        json={"decision": "reject", "reason": "Dirección inexistente."},
    )

    assert response.status_code == 200
    assert response.json()["verificationStatus"] == "rejected"
    # §25: rechazar la verificación no toca el estado operativo.
    assert response.json()["operationalStatus"] == "open"


@pytest.mark.anyio
async def test_reactivation_resets_cycle_and_keeps_history():
    repository = FakeCommunityRepository(
        center=center_row(
            operational_status="inactive", disabled_at=BASE_AT
        ),
        live_reports=AID_LOCATION_DISABLE_THRESHOLD,
    )
    app = community_app(repository)

    response = await request_app(
        app, "POST", REACTIVATE_PATH, headers=ADMIN_HEADERS
    )

    assert response.status_code == 200
    body = response.json()
    assert body["operationalStatus"] == "open"
    assert body["disabledAt"] is None
    # Ciclo 2 empieza en 0 sin borrar el histórico (§16).
    assert body["activeReportsCount"] == 0
    assert len(repository.reports) == AID_LOCATION_DISABLE_THRESHOLD
    assert all(
        report["archived_at"] is not None
        for report in repository.reports
    )
    assert repository.admin_audit == ["aid_location_reactivated"]


@pytest.mark.anyio
async def test_reactivating_a_healthy_center_conflicts():
    app = community_app()

    response = await request_app(
        app, "POST", REACTIVATE_PATH, headers=ADMIN_HEADERS
    )

    assert response.status_code == 409


# --- CHG-167: borrado admin de comentarios ---------------------------


def comment_delete_path(comment_id) -> str:
    return (
        f"/internal/v1/admin/aid-locations/{LOCATION_ID}"
        f"/comments/{comment_id}"
    )


@pytest.mark.anyio
async def test_admin_deletes_a_comment_and_audits():
    repository = FakeCommunityRepository()
    app = community_app(repository)
    created = await request_app(
        app,
        "POST",
        COMMENTS_PATH,
        headers={**idempotency("d1"), "X-Actor-Kind": "anonymous"},
        json={"content": "Este punto dejó de atender.", "rating": 2},
    )
    comment_id = created.json()["id"]

    response = await request_app(
        app,
        "DELETE",
        comment_delete_path(comment_id),
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {"deleted": 1}
    assert repository.comments == []
    assert repository.admin_audit == ["aid_location_comment_deleted"]


@pytest.mark.anyio
async def test_deleting_unknown_comment_is_404():
    app = community_app()

    response = await request_app(
        app,
        "DELETE",
        comment_delete_path(uuid4()),
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_deleting_comment_requires_admin_role():
    repository = FakeCommunityRepository()
    app = community_app(repository)
    created = await request_app(
        app,
        "POST",
        COMMENTS_PATH,
        headers={**idempotency("d2"), "X-Actor-Kind": "anonymous"},
        json={"content": "Comentario que nadie más puede borrar.", "rating": 3},
    )
    comment_id = created.json()["id"]

    without_role = await request_app(
        app, "DELETE", comment_delete_path(comment_id)
    )
    as_moderator = await request_app(
        app,
        "DELETE",
        comment_delete_path(comment_id),
        headers=MODERATOR_HEADERS,
    )

    assert without_role.status_code == 403
    assert as_moderator.status_code == 403
    assert len(repository.comments) == 1
