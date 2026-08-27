"""CHG-066 — Presencia de visitantes con consentimiento explícito.

Solo usuarios REGISTRADOS reportan ubicación en vivo (el anónimo recibe
401) y la lectura es EXCLUSIVA de la consola super_admin.
"""

import base64
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.main import create_app

from test_missing_persons import request_app

PRESENCE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa9"
ACCOUNT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1"
ADMIN_HEADERS = {
    "X-Actor-Role": "super_admin",
    "X-Actor-Account-Id": ACCOUNT_ID,
    "X-Actor-Display": base64.b64encode(b"Superadmin").decode(),
}


class FakePresenceRepository:
    def __init__(self):
        self.upserts: list[dict] = []
        self.rows = [
            {
                "presence_id": UUID(PRESENCE_ID),
                "account_id": UUID(ACCOUNT_ID),
                "latitude": 7.13,
                "longitude": -73.13,
                "accuracy_meters": 12.5,
                "platform": "android",
                "first_seen_at": datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
                "updated_at": datetime(2026, 8, 15, 12, 5, tzinfo=UTC),
            }
        ]

    async def ping(self):
        return True

    async def upsert_visitor_presence(
        self,
        presence_id,
        account_id,
        latitude,
        longitude,
        accuracy_meters,
        platform,
        altitude_meters=None,
        altitude_accuracy_meters=None,
    ):
        self.upserts.append(
            {
                "presence_id": presence_id,
                "account_id": account_id,
                "latitude": latitude,
                "longitude": longitude,
                "accuracy_meters": accuracy_meters,
                "platform": platform,
                "altitude_meters": altitude_meters,
                "altitude_accuracy_meters": altitude_accuracy_meters,
            }
        )

    async def list_visitor_presence(self, window_minutes, limit):
        return self.rows, len(self.rows)


def presence_payload(**overrides):
    payload = {
        "presenceId": PRESENCE_ID,
        "latitude": 7.13,
        "longitude": -73.13,
        "accuracyMeters": 12.5,
        "platform": "android",
    }
    payload.update(overrides)
    return payload


@pytest.mark.anyio
async def test_presence_requires_registered_account():
    repository = FakePresenceRepository()
    app = create_app(repository=repository)

    anonymous = await request_app(
        app,
        "POST",
        "/internal/v1/presence",
        content=json.dumps(presence_payload()),
        headers={"Content-Type": "application/json"},
    )
    assert anonymous.status_code == 401
    assert repository.upserts == []

    authenticated = await request_app(
        app,
        "POST",
        "/internal/v1/presence",
        content=json.dumps(presence_payload()),
        headers={
            "Content-Type": "application/json",
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": ACCOUNT_ID,
        },
    )
    assert authenticated.status_code == 202
    assert repository.upserts[-1]["account_id"] == UUID(ACCOUNT_ID)
    assert repository.upserts[-1]["platform"] == "android"


@pytest.mark.anyio
async def test_presence_rejects_invalid_coordinates():
    app = create_app(repository=FakePresenceRepository())

    response = await request_app(
        app,
        "POST",
        "/internal/v1/presence",
        content=json.dumps(presence_payload(latitude=120)),
        headers={
            "Content-Type": "application/json",
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": ACCOUNT_ID,
        },
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_admin_presence_requires_super_admin():
    app = create_app(repository=FakePresenceRepository())

    without_role = await request_app(
        app, "GET", "/internal/v1/admin/visitor-presence"
    )
    assert without_role.status_code == 403

    moderator = await request_app(
        app,
        "GET",
        "/internal/v1/admin/visitor-presence",
        headers={**ADMIN_HEADERS, "X-Actor-Role": "moderator"},
    )
    assert moderator.status_code == 403


@pytest.mark.anyio
async def test_admin_presence_lists_recent_positions():
    app = create_app(repository=FakePresenceRepository())

    response = await request_app(
        app,
        "GET",
        "/internal/v1/admin/visitor-presence",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["windowMinutes"] == 30
    item = body["items"][0]
    assert item["presenceId"] == PRESENCE_ID
    assert item["platform"] == "android"
    assert item["authenticated"] is True
    # La cuenta jamás se expone: solo el hecho de estar autenticado.
    assert "accountId" not in item


# CHG-220 — La altitud del GPS se guarda con la presencia cuando llega.


@pytest.mark.anyio
async def test_la_presencia_guarda_la_altitud_cuando_llega():
    repository = FakePresenceRepository()
    app = create_app(repository=repository)
    response = await request_app(
        app,
        "POST",
        "/internal/v1/presence",
        content=json.dumps(
            presence_payload(altitudeMeters=959, altitudeAccuracyMeters=12)
        ),
        headers={
            "Content-Type": "application/json",
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": ACCOUNT_ID,
        },
    )
    assert response.status_code == 202
    assert repository.upserts[-1]["altitude_meters"] == 959
    assert repository.upserts[-1]["altitude_accuracy_meters"] == 12
