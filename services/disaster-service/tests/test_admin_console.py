"""CHG-036 — Consola de superadministración (disaster-service).

Cubre defensa en profundidad del rol, bandeja sin PII, detalle con
clasificación y allowlist, concurrencia 409, transiciones, evidencia
temporal ligada al actor y reglas puras compartidas.
"""

import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app import admin as admin_rules
from app.config import Settings
from app.main import build_fernet, create_app
from app.storage import StorageUnavailableError

from test_missing_persons import FakeStorage, request_app


FERNET = build_fernet(Settings.from_environment().report_encryption_key)
ACTOR_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
SUBMISSION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1")
EVIDENCE_ID = UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc1")
RECEIVED_AT = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)

ADMIN_HEADERS = {
    "X-Actor-Role": "super_admin",
    "X-Actor-Account-Id": str(ACTOR_ID),
    "X-Actor-Display": base64.b64encode("Admin CUSOL".encode()).decode(),
}


def summary_row(**overrides) -> dict:
    row = {
        "id": SUBMISSION_ID,
        "kind": "unverified_building_report",
        "tracking_code": "BR-2026-AAAA1111",
        "title": "Edificio sin verificar — Torre Norte",
        "location_label": "Bucaramanga, Santander",
        "source_label": "Reporte ciudadano",
        "domain_status": "under_review",
        "needs_information": False,
        "archived_at": None,
        "received_at": RECEIVED_AT,
        "updated_at": RECEIVED_AT,
        "version": 1,
        "evidence_count": 1,
        "admin_status": "under_review",
    }
    row.update(overrides)
    return row


def building_row(**overrides) -> dict:
    row = {
        "id": SUBMISSION_ID,
        "public_tracking_code": "BR-2026-AAAA1111",
        "building_reference": "Torre Norte",
        "building_type": "residential",
        "department": "Santander",
        "municipality": "Bucaramanga",
        "sector": "Barrio Colorados",
        "location_reference_protected": FERNET.encrypt(
            b"Frente al parque"
        ),
        "address_protected": FERNET.encrypt(b"Calle 45 # 12-34"),
        "latitude_protected": FERNET.encrypt(b"7.113256"),
        "longitude_protected": FERNET.encrypt(b"-73.119847"),
        "observed_date": "2026-08-13",
        "observed_time": "10:30",
        "search_status": "not_started",
        "occupancy_report": "unknown",
        "pending_reasons": ["access_blocked"],
        "observed_conditions": ["visible_debris"],
        "observation_description_protected": FERNET.encrypt(
            b"Observacion privada"
        ),
        "reporter_name_protected": FERNET.encrypt(b"Reportante Demo"),
        "reporter_role_protected": FERNET.encrypt(b"Vecino"),
        "reporter_organization_protected": None,
        "reporter_phone_protected": FERNET.encrypt(b"+57 300 000 0000"),
        "reporter_email_protected": None,
        "official_report_number_protected": None,
    }
    row.update(overrides)
    return row


def evidence_row(**overrides) -> dict:
    row = {
        "id": EVIDENCE_ID,
        "content_type": "image/jpeg",
        "size_bytes": 2048,
        "malware_scan": "clean",
        "created_at": RECEIVED_AT,
        "derived_key": "unverified-building-reports/x/derived/foto.jpg",
        "kind": "unverified_building_report",
    }
    row.update(overrides)
    return row


class FakeAdminRepository:
    def __init__(self):
        self.summary = summary_row()
        self.detail = ("unverified_building_report", building_row(), [
            evidence_row()
        ])
        self.evidence = evidence_row()
        self.mutations: list[dict] = []
        self.audits: list[dict] = []
        self.mutation_outcome = "ok"
        self.list_args = None
        self.audit_args = None

    async def ping(self):
        return True

    async def admin_get_submission_summary(self, submission_id):
        if self.summary is None:
            return None
        return dict(self.summary)

    async def admin_get_submission(self, submission_id):
        return self.detail

    async def admin_list_submissions(
        self, q, kind, status, received_from, received_to, limit, offset
    ):
        self.list_args = {
            "q": q,
            "kind": kind,
            "status": status,
            "received_from": received_from,
            "received_to": received_to,
            "limit": limit,
            "offset": offset,
        }
        return [dict(self.summary)], 1

    async def admin_submissions_overview(self):
        return {
            "counts": [
                {
                    "admin_status": "under_review",
                    "kind": "unverified_building_report",
                    "quantity": 3,
                    "oldest": RECEIVED_AT,
                },
                {
                    "admin_status": "archived",
                    "kind": "aid_location_rating",
                    "quantity": 1,
                    "oldest": RECEIVED_AT,
                },
            ],
            "accepted_today": 2,
            "recent_activity": [
                {
                    "id": uuid4(),
                    "action": "submission_accepted",
                    "resource_kind": "aid_location_rating",
                    "occurred_at": RECEIVED_AT,
                    "result": "success",
                }
            ],
        }

    async def admin_mutate_submission(
        self,
        kind,
        submission_id,
        expected_version,
        action,
        actor_account_id,
        actor_display_name,
        reason_encrypted,
        columns=None,
        correlation_id=None,
    ):
        self.mutations.append(
            {
                "kind": kind,
                "action": action,
                "expected_version": expected_version,
                "columns": columns,
                "actor": actor_account_id,
                "reason_encrypted": reason_encrypted,
            }
        )
        if self.mutation_outcome != "ok":
            return self.mutation_outcome, None, None
        self.summary["version"] += 1
        if action == "archive":
            self.summary["archived_at"] = RECEIVED_AT
            self.summary["admin_status"] = "archived"
        if action == "restore":
            self.summary["archived_at"] = None
            self.summary["admin_status"] = "under_review"
        if action == "accept":
            self.summary["domain_status"] = "accepted"
            self.summary["admin_status"] = "accepted"
        return "ok", uuid4(), self.summary["version"]

    async def admin_get_evidence(self, submission_id, evidence_id):
        if (
            submission_id != SUBMISSION_ID
            or evidence_id != EVIDENCE_ID
        ):
            return None
        return dict(self.evidence)

    async def admin_write_audit(self, *args, **kwargs):
        self.audits.append({"args": args, "kwargs": kwargs})
        return uuid4()

    async def admin_list_audit_events(
        self, q, action, result, limit, offset
    ):
        self.audit_args = {
            "q": q, "action": action, "result": result,
            "limit": limit, "offset": offset,
        }
        return [
            {
                "id": uuid4(),
                "occurred_at": RECEIVED_AT,
                "actor_account_id": ACTOR_ID,
                "actor_display_name": "Admin CUSOL",
                "action": "submission_accepted",
                "resource_kind": "aid_location_rating",
                "resource_id": SUBMISSION_ID,
                "result": "success",
                "reason_protected": FERNET.encrypt(b"Motivo valido."),
            }
        ], 1


def admin_app(repository=None, storage=None):
    return create_app(
        repository=repository or FakeAdminRepository(),
        storage=storage if storage is not None else FakeStorage(),
    )


# --- Defensa en profundidad ---


@pytest.mark.anyio
async def test_admin_routes_reject_missing_or_insufficient_role():
    app = admin_app()

    for path in (
        "/internal/v1/admin/submissions",
        "/internal/v1/admin/submissions-overview",
        f"/internal/v1/admin/submissions/{SUBMISSION_ID}",
        "/internal/v1/admin/audit-events",
    ):
        without = await request_app(app, "GET", path)
        as_user = await request_app(
            app, "GET", path,
            headers={**ADMIN_HEADERS, "X-Actor-Role": "user"},
        )
        as_moderator = await request_app(
            app, "GET", path,
            headers={**ADMIN_HEADERS, "X-Actor-Role": "moderator"},
        )
        assert without.status_code == 403
        assert as_user.status_code == 403
        assert as_moderator.status_code == 403


# --- Bandeja y resumen ---


@pytest.mark.anyio
async def test_admin_list_passes_filters_and_hides_pii():
    repository = FakeAdminRepository()
    app = admin_app(repository=repository)

    response = await request_app(
        app,
        "GET",
        "/internal/v1/admin/submissions"
        "?q=torre&kind=unverified_building_report&status=under_review"
        "&limit=10&offset=20",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert repository.list_args["q"] == "torre"
    assert repository.list_args["kind"] == "unverified_building_report"
    assert repository.list_args["status"] == "under_review"
    assert repository.list_args["limit"] == 10
    assert repository.list_args["offset"] == 20
    item = body["items"][0]
    assert set(item.keys()) == {
        "id", "kind", "trackingCode", "title", "locationLabel",
        "status", "sourceLabel", "evidenceCount", "receivedAt",
        "updatedAt", "version",
    }
    # Cero PII en el resumen.
    text = response.text
    for private in ("Reportante", "Calle 45", "+57 300", "7.113256"):
        assert private not in text


@pytest.mark.anyio
async def test_admin_list_rejects_invalid_page_size():
    app = admin_app()

    response = await request_app(
        app,
        "GET",
        "/internal/v1/admin/submissions?limit=13",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_admin_overview_aggregates_counts():
    app = admin_app()

    response = await request_app(
        app,
        "GET",
        "/internal/v1/admin/submissions-overview",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["underReview"] == 3
    assert body["archived"] == 1
    assert body["acceptedToday"] == 2
    assert body["byKind"] == [
        {"kind": "unverified_building_report", "count": 3}
    ]
    assert len(body["recentActivity"]) == 1


# --- Detalle clasificado ---


@pytest.mark.anyio
async def test_admin_detail_classifies_and_decrypts_for_console():
    app = admin_app()

    response = await request_app(
        app,
        "GET",
        f"/internal/v1/admin/submissions/{SUBMISSION_ID}",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    fields = {field["key"]: field for field in body["fields"]}
    assert fields["address"]["classification"] == "protected"
    assert fields["address"]["displayValue"] == "Calle 45 # 12-34"
    assert fields["address"]["editable"] is False
    assert fields["sector"]["editable"] is True
    assert fields["sector"]["editValue"] == "Barrio Colorados"
    assert fields["reporterPhone"]["displayValue"] == "+57 300 000 0000"
    assert body["availableActions"] == [
        "accept", "reject", "request_changes", "archive"
    ]
    assert body["evidence"][0]["scanStatus"] == "safe"
    # La evidencia jamás expone claves de almacenamiento.
    assert "derived" not in response.text


# --- Edición con versión y allowlist ---


@pytest.mark.anyio
async def test_admin_edit_applies_allowlisted_change():
    repository = FakeAdminRepository()
    app = admin_app(repository=repository)

    response = await request_app(
        app,
        "PATCH",
        f"/internal/v1/admin/submissions/{SUBMISSION_ID}",
        headers=ADMIN_HEADERS,
        json={
            "expectedVersion": 1,
            "reason": "Corrección de sector reportado.",
            "changes": [{"field": "sector", "value": "Centro"}],
        },
    )

    assert response.status_code == 200
    mutation = repository.mutations[0]
    assert mutation["action"] == "edit"
    assert mutation["columns"] == {"sector": "Centro"}
    # El motivo viaja cifrado a la auditoría.
    assert FERNET.decrypt(mutation["reason_encrypted"]).decode() == (
        "Corrección de sector reportado."
    )


@pytest.mark.anyio
async def test_admin_edit_rejects_fields_outside_allowlist():
    repository = FakeAdminRepository()
    app = admin_app(repository=repository)

    protected = await request_app(
        app,
        "PATCH",
        f"/internal/v1/admin/submissions/{SUBMISSION_ID}",
        headers=ADMIN_HEADERS,
        json={
            "expectedVersion": 1,
            "reason": "Intento de editar campo protegido.",
            "changes": [{"field": "address", "value": "Nueva 1-23"}],
        },
    )

    assert protected.status_code == 422
    assert repository.mutations == []


@pytest.mark.anyio
async def test_admin_edit_version_conflict_is_409():
    repository = FakeAdminRepository()
    repository.mutation_outcome = "conflict"
    app = admin_app(repository=repository)

    response = await request_app(
        app,
        "PATCH",
        f"/internal/v1/admin/submissions/{SUBMISSION_ID}",
        headers=ADMIN_HEADERS,
        json={
            "expectedVersion": 1,
            "reason": "Edición con versión vieja.",
            "changes": [{"field": "sector", "value": "Centro"}],
        },
    )

    assert response.status_code == 409


# --- Decisiones y transiciones ---


@pytest.mark.anyio
async def test_admin_decision_accept_and_receipt():
    repository = FakeAdminRepository()
    app = admin_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        f"/internal/v1/admin/submissions/{SUBMISSION_ID}/decisions",
        headers=ADMIN_HEADERS,
        json={
            "expectedVersion": 1,
            "action": "accept",
            "reason": "Evidencia revisada y consistente.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["version"] == 2
    assert repository.mutations[0]["action"] == "accept"


@pytest.mark.anyio
async def test_admin_decision_rejects_illegal_transition():
    repository = FakeAdminRepository()
    repository.summary = summary_row(
        domain_status="accepted", admin_status="accepted"
    )
    app = admin_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        f"/internal/v1/admin/submissions/{SUBMISSION_ID}/decisions",
        headers=ADMIN_HEADERS,
        json={
            "expectedVersion": 2,
            "action": "accept",
            "reason": "Reintento sobre aceptado.",
        },
    )

    assert response.status_code == 409
    assert repository.mutations == []


@pytest.mark.anyio
async def test_admin_archive_then_restore_flow():
    repository = FakeAdminRepository()
    app = admin_app(repository=repository)

    archived = await request_app(
        app,
        "DELETE",
        f"/internal/v1/admin/submissions/{SUBMISSION_ID}",
        headers=ADMIN_HEADERS,
        json={
            "expectedVersion": 1,
            "reason": "Duplicado de otro expediente.",
        },
    )
    edit_archived = await request_app(
        app,
        "PATCH",
        f"/internal/v1/admin/submissions/{SUBMISSION_ID}",
        headers=ADMIN_HEADERS,
        json={
            "expectedVersion": 2,
            "reason": "Editar estando archivado.",
            "changes": [{"field": "sector", "value": "Centro"}],
        },
    )
    restored = await request_app(
        app,
        "POST",
        f"/internal/v1/admin/submissions/{SUBMISSION_ID}/restore",
        headers=ADMIN_HEADERS,
        json={
            "expectedVersion": 2,
            "reason": "Archivo fue un error.",
        },
    )

    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert edit_archived.status_code == 409
    assert restored.status_code == 200
    assert restored.json()["status"] == "under_review"


@pytest.mark.anyio
async def test_admin_restore_requires_archived_state():
    app = admin_app()

    response = await request_app(
        app,
        "POST",
        f"/internal/v1/admin/submissions/{SUBMISSION_ID}/restore",
        headers=ADMIN_HEADERS,
        json={
            "expectedVersion": 1,
            "reason": "Restaurar sin archivo previo.",
        },
    )

    assert response.status_code == 409


# --- Evidencia temporal ---


@pytest.mark.anyio
async def test_evidence_grant_expires_within_five_minutes():
    repository = FakeAdminRepository()
    app = admin_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        f"/internal/v1/admin/submissions/{SUBMISSION_ID}"
        f"/evidence/{EVIDENCE_ID}/access-grants",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    expires_at = datetime.fromisoformat(body["expiresAt"])
    assert expires_at <= datetime.now(UTC) + timedelta(seconds=300)
    assert body["url"].startswith("/api/v1/admin/evidence-access/")
    # Sin nombre original ni clave de almacenamiento en la URL.
    assert "foto" not in body["url"]
    assert any(
        audit["args"][2] == "evidence_access_granted"
        for audit in repository.audits
    )


@pytest.mark.anyio
async def test_evidence_serving_is_bound_to_actor_and_expiry():
    repository = FakeAdminRepository()
    storage = FakeStorage()
    storage.objects[
        "unverified-building-reports/x/derived/foto.jpg"
    ] = b"derivado-sin-exif"
    app = admin_app(repository=repository, storage=storage)

    grant = await request_app(
        app,
        "POST",
        f"/internal/v1/admin/submissions/{SUBMISSION_ID}"
        f"/evidence/{EVIDENCE_ID}/access-grants",
        headers=ADMIN_HEADERS,
    )
    url = grant.json()["url"].replace(
        "/api/v1/admin", "/internal/v1/admin"
    )

    served = await request_app(app, "GET", url, headers=ADMIN_HEADERS)
    other_actor = await request_app(
        app,
        "GET",
        url,
        headers={
            **ADMIN_HEADERS,
            "X-Actor-Account-Id": str(uuid4()),
        },
    )

    assert served.status_code == 200
    assert served.content == b"derivado-sin-exif"
    assert served.headers["content-type"] == "image/jpeg"
    assert served.headers["cache-control"] == "no-store, private"
    assert other_actor.status_code == 404


def test_evidence_token_rejects_expiry_and_tampering():
    secret = "clave-de-prueba"
    expires = datetime.now(UTC) + timedelta(seconds=60)
    token = admin_rules.make_evidence_grant_token(
        secret, SUBMISSION_ID, EVIDENCE_ID, ACTOR_ID, expires
    )

    valid = admin_rules.parse_evidence_grant_token(secret, token)
    assert valid is not None
    assert valid.actor_account_id == ACTOR_ID

    expired = admin_rules.make_evidence_grant_token(
        secret,
        SUBMISSION_ID,
        EVIDENCE_ID,
        ACTOR_ID,
        datetime.now(UTC) - timedelta(seconds=1),
    )
    assert admin_rules.parse_evidence_grant_token(secret, expired) is None
    assert admin_rules.parse_evidence_grant_token(
        secret, token[:-4] + "AAAA"
    ) is None
    assert admin_rules.parse_evidence_grant_token(
        "otra-clave", token
    ) is None


@pytest.mark.anyio
async def test_evidence_with_failed_scan_is_never_granted():
    repository = FakeAdminRepository()
    repository.evidence = evidence_row(malware_scan="infected")
    app = admin_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        f"/internal/v1/admin/submissions/{SUBMISSION_ID}"
        f"/evidence/{EVIDENCE_ID}/access-grants",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 404


# --- Auditoría ---


@pytest.mark.anyio
async def test_admin_audit_list_decrypts_reason_summary():
    repository = FakeAdminRepository()
    app = admin_app(repository=repository)

    response = await request_app(
        app,
        "GET",
        "/internal/v1/admin/audit-events?action=submission_accepted"
        "&result=success&limit=25",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert repository.audit_args["action"] == "submission_accepted"
    assert body["items"][0]["reasonSummary"] == "Motivo valido."
    assert body["items"][0]["result"] == "success"


# --- Reglas puras compartidas ---


def test_unified_status_mapping():
    assert admin_rules.unified_status("under_review", False, None) == (
        "under_review"
    )
    assert admin_rules.unified_status("unverified", False, None) == (
        "under_review"
    )
    assert admin_rules.unified_status("under_review", True, None) == (
        "needs_information"
    )
    assert admin_rules.unified_status("verified", False, None) == (
        "accepted"
    )
    assert admin_rules.unified_status("accepted", False, None) == (
        "accepted"
    )
    assert admin_rules.unified_status("rejected", False, None) == (
        "rejected"
    )
    assert admin_rules.unified_status("withdrawn", False, None) == (
        "archived"
    )
    assert admin_rules.unified_status(
        "under_review", False, RECEIVED_AT
    ) == "archived"


def test_available_actions_by_state():
    assert admin_rules.available_actions("under_review", False, None) == [
        "accept", "reject", "request_changes", "archive"
    ]
    assert admin_rules.available_actions("under_review", True, None) == [
        "accept", "reject", "archive"
    ]
    assert admin_rules.available_actions("accepted", False, None) == [
        "archive"
    ]
    assert admin_rules.available_actions(
        "accepted", False, RECEIVED_AT
    ) == ["restore"]
    assert admin_rules.available_actions("withdrawn", False, None) == [
        "archive"
    ]


def test_validate_changes_enforces_allowlist_and_lengths():
    resolved = admin_rules.validate_changes(
        "missing_person_report",
        {"lastSeenArea": "  Sector nuevo  "},
    )
    assert resolved == {"last_seen_area": "Sector nuevo"}

    with pytest.raises(admin_rules.AdminEditError):
        admin_rules.validate_changes(
            "missing_person_report", {"reporterName": "x"}
        )
    with pytest.raises(admin_rules.AdminEditError):
        admin_rules.validate_changes(
            "aid_location_rating", {"rating": "5"}
        )
    with pytest.raises(admin_rules.AdminEditError):
        admin_rules.validate_changes(
            "unverified_building_report", {"sector": "x" * 200}
        )
