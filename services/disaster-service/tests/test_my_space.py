"""CHG-069 — "Mi espacio": reportes propios con novedades de terceros
y alertas ciudadanas de voluntariado. Todo exige cuenta autenticada."""

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.main import create_app

from test_missing_persons import request_app

ACCOUNT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2"
REPORT_ID = "cccccccc-cccc-4ccc-8ccc-ccccccccccc1"
ALERT_ID = "dddddddd-dddd-4ddd-8ddd-ddddddddddd1"
AUTH_HEADERS = {
    "X-Actor-Kind": "authenticated",
    "X-Account-Id": ACCOUNT_ID,
}
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


class FakeMySpaceRepository:
    def __init__(self):
        self.alerts: list[dict] = []
        self.resolved: list[UUID] = []

    async def ping(self):
        return True

    async def list_my_reports(self, account_id):
        assert account_id == UUID(ACCOUNT_ID)
        return [
            {
                "id": UUID(REPORT_ID),
                "kind": "missing_person_report",
                "reference_code": "MP-2026-ABCD1234",
                "title": "Persona De Prueba",
                "status": "under_review",
                "received_at": NOW,
            }
        ]

    async def list_report_novelties(self, account_id, report_ids):
        assert report_ids == [UUID(REPORT_ID)]
        return {
            UUID(REPORT_ID): [
                {
                    "claimed_outcome": "found",
                    "moderation_status": "under_review",
                    "received_at": NOW,
                }
            ]
        }

    async def create_volunteer_alert(
        self, account_id, description, address, latitude, longitude
    ):
        row = {
            "id": UUID(ALERT_ID),
            "description": description,
            "address": address,
            "latitude": latitude,
            "longitude": longitude,
            "status": "active",
            "created_at": NOW,
            "updated_at": NOW,
        }
        self.alerts.append(row)
        return row

    async def list_my_volunteer_alerts(self, account_id):
        return list(self.alerts)

    async def resolve_volunteer_alert(self, account_id, alert_id):
        self.resolved.append(alert_id)
        for row in self.alerts:
            if row["id"] == alert_id and row["status"] == "active":
                return {**row, "status": "resolved"}
        return None


def alert_payload(**overrides):
    payload = {
        "description": "Se necesita gente para remover escombros.",
        "address": "Calle 45 #27-08, Bucaramanga",
        "latitude": 7.1193,
        "longitude": -73.1227,
    }
    payload.update(overrides)
    return payload


@pytest.mark.anyio
async def test_my_space_requires_account():
    app = create_app(repository=FakeMySpaceRepository())
    for method, path in [
        ("GET", "/internal/v1/me/reports"),
        ("GET", "/internal/v1/me/volunteer-alerts"),
        ("POST", "/internal/v1/me/volunteer-alerts"),
    ]:
        response = await request_app(
            app,
            method,
            path,
            content=json.dumps(alert_payload()) if method == "POST" else None,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 401, path


@pytest.mark.anyio
async def test_my_reports_include_novelties_from_others():
    app = create_app(repository=FakeMySpaceRepository())

    response = await request_app(
        app, "GET", "/internal/v1/me/reports", headers=AUTH_HEADERS
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["referenceCode"] == "MP-2026-ABCD1234"
    assert item["status"] == "under_review"
    assert item["novelties"] == [
        {
            "claimedOutcome": "found",
            "moderationStatus": "under_review",
            "receivedAt": "2026-08-15T12:00:00Z",
        }
    ]


@pytest.mark.anyio
async def test_volunteer_alert_lifecycle():
    repository = FakeMySpaceRepository()
    app = create_app(repository=repository)

    created = await request_app(
        app,
        "POST",
        "/internal/v1/me/volunteer-alerts",
        content=json.dumps(alert_payload()),
        headers={**AUTH_HEADERS, "Content-Type": "application/json"},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "active"

    listed = await request_app(
        app, "GET", "/internal/v1/me/volunteer-alerts", headers=AUTH_HEADERS
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    resolved = await request_app(
        app,
        "POST",
        f"/internal/v1/me/volunteer-alerts/{ALERT_ID}/resolve",
        headers=AUTH_HEADERS,
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"

    missing = await request_app(
        app,
        "POST",
        "/internal/v1/me/volunteer-alerts/"
        "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1/resolve",
        headers=AUTH_HEADERS,
    )
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_volunteer_alert_rejects_short_description():
    app = create_app(repository=FakeMySpaceRepository())

    response = await request_app(
        app,
        "POST",
        "/internal/v1/me/volunteer-alerts",
        content=json.dumps(alert_payload(description="corta")),
        headers={**AUTH_HEADERS, "Content-Type": "application/json"},
    )

    assert response.status_code == 422
