"""CHG-174 — Aceptación inicial de ruta Centro de Acopio Local ↔ Mulera.

Cubre la capa de endpoints con repositorio falso: quién puede actuar,
que validar el código NO acepta la ruta, y que cada error del dominio
llega con su código HTTP. La lógica transaccional (unicidad, un solo
uso, concurrencia) se verifica en vivo contra la base real.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.main import create_app

from test_missing_persons import FakeStorage, request_app


ACCOUNT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa7")
TRANSPORT_ID = UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd7")
REQUEST_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee7")
CENTER_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb7")
CREATED_AT = datetime(2026, 8, 19, 21, 0, tzinfo=UTC)

STEWARD_HEADERS = {
    "X-Actor-Kind": "authenticated",
    "X-Account-Id": str(ACCOUNT_ID),
}
SUPER_ADMIN_HEADERS = {**STEWARD_HEADERS, "X-Actor-Role": "super_admin"}
ANON_HEADERS = {"X-Actor-Kind": "anonymous"}


class FakeRouteRepository:
    def __init__(self, outcome=None):
        self.outcome = outcome
        self.calls: list[tuple[str, dict]] = []

    async def ping(self):
        return True

    async def list_center_transport_requests(self, **kwargs):
        self.calls.append(("list_requests", kwargs))
        return [
            {
                "id": REQUEST_ID,
                "transport_id": TRANSPORT_ID,
                "center_id": CENTER_ID,
                "center_role": "local",
                "status": "pending",
                "requested_at": CREATED_AT,
                "decided_at": None,
                "center_name": "Acopio La Feria",
                "center_municipality": "Bucaramanga",
                "transport_kind": "mule",
                "origin_center_name": "Acopio La Feria",
                "destination_center_name": "Receptor Mompós",
                "origin_municipality": "Bucaramanga",
                "destination_municipality": "Mompós",
                "supplies_summary": "Agua",
                "transport_created_at": CREATED_AT,
                "driver_full_name": "Pedro Antonio Rojas",
                "driver_document_type": "Cédula de ciudadanía",
                # Llegan cifrados desde la base; el endpoint descifra.
                "driver_document_number_encrypted": None,
                "driver_phone_encrypted": None,
                "tractor_plate": "ABC123",
                "trailer_plate": "R99881",
                "vessel_registration": None,
                "vessel_name": None,
                "vessel_type": None,
                "vehicle_visible_characteristics": "Blanco, franja azul",
            }
        ]

    async def decide_center_transport_request(self, **kwargs):
        self.calls.append(("decide", kwargs))
        if self.outcome:
            return self.outcome
        return {
            "id": REQUEST_ID,
            "transport_id": TRANSPORT_ID,
            "center_id": CENTER_ID,
            "status": "accepted" if kwargs["accept"] else "declined",
            "decided_at": CREATED_AT,
        }

    async def start_local_route_acceptance(self, **kwargs):
        self.calls.append(("start_route", kwargs))
        if self.outcome:
            return self.outcome
        return {
            "transport_id": TRANSPORT_ID,
            "confirmation_code": kwargs["generate_code"](),
            "status": "code_issued",
            "reused": False,
        }

    async def validate_route_code(self, **kwargs):
        self.calls.append(("validate", kwargs))
        if self.outcome:
            return self.outcome
        return {
            "transport_id": TRANSPORT_ID,
            "validated": True,
            "origin_center_name": "Acopio La Feria",
            "destination_center_name": "Receptor Mompós",
        }

    async def accept_route_by_mule(self, **kwargs):
        self.calls.append(("accept", kwargs))
        if self.outcome:
            return self.outcome
        return {
            "transport_id": TRANSPORT_ID,
            "status": "accepted",
            "mule_accepted_at": CREATED_AT,
        }


def route_app(repository=None):
    return create_app(
        repository=repository or FakeRouteRepository(),
        storage=FakeStorage(),
    )


@pytest.mark.anyio
async def test_center_requests_need_a_session():
    response = await request_app(
        route_app(),
        "GET",
        "/internal/v1/me/center-transport-requests",
        headers=ANON_HEADERS,
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_center_requests_carry_the_authorized_driver_view():
    repository = FakeRouteRepository()
    response = await request_app(
        route_app(repository),
        "GET",
        "/internal/v1/me/center-transport-requests",
        headers=STEWARD_HEADERS,
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    # §12: la vista autorizada nombra al conductor y su vehículo.
    assert item["driverFullName"] == "Pedro Antonio Rojas"
    assert item["tractorPlate"] == "ABC123"
    assert item["status"] == "pending"
    # El repositorio decide por centro; el endpoint solo pasa el actor.
    assert repository.calls[0][1]["account_id"] == ACCOUNT_ID
    assert repository.calls[0][1]["is_super_admin"] is False


@pytest.mark.anyio
async def test_super_admin_role_travels_to_the_repository():
    repository = FakeRouteRepository()
    await request_app(
        route_app(repository),
        "GET",
        "/internal/v1/me/center-transport-requests",
        headers=SUPER_ADMIN_HEADERS,
    )
    assert repository.calls[0][1]["is_super_admin"] is True


@pytest.mark.anyio
async def test_accept_and_decline_persist_the_decision():
    repository = FakeRouteRepository()
    app = route_app(repository)

    accepted = await request_app(
        app,
        "POST",
        f"/internal/v1/me/center-transport-requests/{REQUEST_ID}/decision",
        headers=STEWARD_HEADERS,
        json={"decision": "accept"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"

    declined = await request_app(
        app,
        "POST",
        f"/internal/v1/me/center-transport-requests/{REQUEST_ID}/decision",
        headers=STEWARD_HEADERS,
        json={"decision": "decline"},
    )
    assert declined.json()["status"] == "declined"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("not_found", 404),
        ("forbidden", 403),
        ("already_decided", 409),
    ],
)
async def test_decision_errors_map_to_http(outcome, expected):
    response = await request_app(
        route_app(FakeRouteRepository(outcome)),
        "POST",
        f"/internal/v1/me/center-transport-requests/{REQUEST_ID}/decision",
        headers=STEWARD_HEADERS,
        json={"decision": "accept"},
    )
    assert response.status_code == expected


@pytest.mark.anyio
async def test_route_code_is_generated_by_the_backend():
    repository = FakeRouteRepository()
    response = await request_app(
        route_app(repository),
        "POST",
        f"/internal/v1/me/humanitarian-transports/{TRANSPORT_ID}"
        "/route-acceptance",
        headers=STEWARD_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    # §28-§30: el código lo emite el backend con el patrón del repo.
    assert body["confirmationCode"].startswith("RT-")
    assert body["status"] == "code_issued"
    assert body["reused"] is False


@pytest.mark.anyio
async def test_route_code_refused_until_both_centers_accept():
    response = await request_app(
        route_app(FakeRouteRepository("not_ready")),
        "POST",
        f"/internal/v1/me/humanitarian-transports/{TRANSPORT_ID}"
        "/route-acceptance",
        headers=STEWARD_HEADERS,
    )
    # §56: la barrera es backend, no el botón deshabilitado.
    assert response.status_code == 409


@pytest.mark.anyio
async def test_validating_the_code_does_not_accept_the_route():
    repository = FakeRouteRepository()
    response = await request_app(
        route_app(repository),
        "POST",
        f"/internal/v1/me/humanitarian-transports/{TRANSPORT_ID}"
        "/route-code/validate",
        headers=STEWARD_HEADERS,
        json={"code": "RT-2026-ABCD1234"},
    )

    assert response.status_code == 200
    assert response.json()["validated"] is True
    # §41: validar habilita el botón; aceptar es otra acción.
    assert [name for name, _ in repository.calls] == ["validate"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("invalid_code", 422),
        ("code_used", 409),
        ("not_issued", 409),
        ("not_owner", 403),
    ],
)
async def test_code_errors_map_to_http(outcome, expected):
    response = await request_app(
        route_app(FakeRouteRepository(outcome)),
        "POST",
        f"/internal/v1/me/humanitarian-transports/{TRANSPORT_ID}"
        "/route-accept",
        headers=STEWARD_HEADERS,
        json={"code": "RT-2026-ABCD1234"},
    )
    assert response.status_code == expected


@pytest.mark.anyio
async def test_mule_confirmation_completes_local_route():
    response = await request_app(
        route_app(),
        "POST",
        f"/internal/v1/me/humanitarian-transports/{TRANSPORT_ID}"
        "/route-accept",
        headers=STEWARD_HEADERS,
        json={"code": "RT-2026-ABCD1234"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


# CHG-175 — Etapa 2: Mulera ↔ Centro de Acopio Receptor. Espejo de la
# etapa 1, pero con su propio código y su propia secuencia.
class FakeReceptionRepository(FakeRouteRepository):
    async def start_reception_route_acceptance(self, **kwargs):
        self.calls.append(("start_reception", kwargs))
        if self.outcome:
            return self.outcome
        return {
            "transport_id": TRANSPORT_ID,
            "confirmation_code": kwargs["generate_code"](),
            "status": "code_issued",
            "reused": False,
        }

    async def validate_reception_route_code(self, **kwargs):
        self.calls.append(("validate_reception", kwargs))
        if self.outcome:
            return self.outcome
        return {
            "transport_id": TRANSPORT_ID,
            "validated": True,
            "origin_center_name": "Acopio La Feria",
            "destination_center_name": "Receptor Mompós",
        }

    async def accept_reception_route_by_mule(self, **kwargs):
        self.calls.append(("accept_reception", kwargs))
        if self.outcome:
            return self.outcome
        return {
            "transport_id": TRANSPORT_ID,
            "status": "accepted",
            "mule_accepted_at": CREATED_AT,
            "route_accepted_at": CREATED_AT,
        }


def reception_app(repository=None):
    return create_app(
        repository=repository or FakeReceptionRepository(),
        storage=FakeStorage(),
    )


@pytest.mark.anyio
async def test_reception_code_has_its_own_prefix():
    repository = FakeReceptionRepository()
    response = await request_app(
        reception_app(repository),
        "POST",
        f"/internal/v1/me/humanitarian-transports/{TRANSPORT_ID}"
        "/reception-route-acceptance",
        headers=STEWARD_HEADERS,
    )

    assert response.status_code == 200
    # §25-§26: el código de la etapa 2 no se parece al de la etapa 1
    # («RT-»), de modo que confundirlos es imposible de un vistazo.
    assert response.json()["confirmationCode"].startswith("RR-")


@pytest.mark.anyio
async def test_reception_stage_refused_while_local_stage_pending():
    response = await request_app(
        reception_app(FakeReceptionRepository("local_stage_pending")),
        "POST",
        f"/internal/v1/me/humanitarian-transports/{TRANSPORT_ID}"
        "/reception-route-acceptance",
        headers=STEWARD_HEADERS,
    )
    # §18-§21: la secuencia la impone el backend, no la interfaz.
    assert response.status_code == 409


@pytest.mark.anyio
async def test_validating_reception_code_does_not_accept():
    repository = FakeReceptionRepository()
    response = await request_app(
        reception_app(repository),
        "POST",
        f"/internal/v1/me/humanitarian-transports/{TRANSPORT_ID}"
        "/reception-route-code/validate",
        headers=STEWARD_HEADERS,
        json={"code": "RR-2026-ABCD1234"},
    )

    assert response.status_code == 200
    assert [name for name, _ in repository.calls] == ["validate_reception"]


@pytest.mark.anyio
async def test_reception_acceptance_seals_the_whole_route():
    response = await request_app(
        reception_app(),
        "POST",
        f"/internal/v1/me/humanitarian-transports/{TRANSPORT_ID}"
        "/reception-route-accept",
        headers=STEWARD_HEADERS,
        json={"code": "RR-2026-ABCD1234"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    # §45-§46: con las dos etapas completas la ruta queda sellada.
    assert body["routeAcceptedAt"] is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("invalid_code", 422),
        ("code_used", 409),
        ("not_issued", 409),
        ("not_owner", 403),
    ],
)
async def test_reception_code_errors_map_to_http(outcome, expected):
    response = await request_app(
        reception_app(FakeReceptionRepository(outcome)),
        "POST",
        f"/internal/v1/me/humanitarian-transports/{TRANSPORT_ID}"
        "/reception-route-accept",
        headers=STEWARD_HEADERS,
        json={"code": "RT-2026-ABCD1234"},
    )
    assert response.status_code == expected
