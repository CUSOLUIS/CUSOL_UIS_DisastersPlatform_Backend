"""CHG-208 — Monitoreo sísmico y red privada de emergencia.

Cubre las reglas puras (idempotencia, persistencia por magnitud,
matching seguro), el ciclo de polling con proveedor falso (revisiones
sin duplicar, fallos sin propagar), y la privacidad de punta a punta:
el público ve triángulos anónimos con coordenada redondeada; SOLO el
contacto aceptado identifica y abre el panel; cada acceso queda
auditado; la revocación surte efecto inmediato; el documento jamás
viaja en el panel. Todo con repositorio falso (sin base de datos).
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest

from app import seismic, seismic_ingest
from app.config import Settings
from app.main import create_app

from test_missing_persons import FakeStorage, request_app


OWNER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa01")
CONTACT = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa02")
STRANGER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa03")
ADMIN = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa99")


def headers_for(account_id: UUID | None, role: str | None = None):
    if account_id is None:
        return {"X-Actor-Kind": "anonymous"}
    headers = {
        "X-Actor-Kind": "authenticated",
        "X-Account-Id": str(account_id),
    }
    if role is not None:
        headers["X-Actor-Role"] = role
    return headers


def point_in_ring(lon: float, lat: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (
            (xj - xi) * (lat - yi) / (yj - yi) + xi
        ):
            inside = not inside
        j = i
    return inside


class FakeSeismicRepository:
    """Espejo en memoria de la semántica del repositorio Postgres."""

    def __init__(self):
        self.events: dict[UUID, dict] = {}
        self.revisions: list[dict] = []
        self.zones: list[dict] = []
        self.settings: dict[UUID, dict] = {}
        self.candidates: dict[UUID, dict] = {}
        self.contacts: dict[UUID, dict] = {}
        self.alerts: dict[UUID, dict] = {}
        self.notifications: list[dict] = []
        self.access_log: list[dict] = []
        self.presence: dict[UUID, dict] = {}
        self.checkpoints: dict[str, dict] = {}

    async def ping(self):
        return True

    # --- checkpoint ---

    async def get_seismic_checkpoint(self, source):
        return self.checkpoints.get(source)

    async def update_seismic_checkpoint(
        self, source, *, last_event_time, success
    ):
        entry = self.checkpoints.setdefault(
            source, {"consecutive_failures": 0, "last_event_time": None}
        )
        if success:
            entry["consecutive_failures"] = 0
            if last_event_time is not None:
                previous = entry.get("last_event_time")
                entry["last_event_time"] = (
                    max(previous, last_event_time)
                    if previous
                    else last_event_time
                )
        else:
            entry["consecutive_failures"] += 1

    # --- eventos ---

    async def get_seismic_event_by_source(self, source, source_event_id):
        for event in self.events.values():
            if (
                event["source"] == source
                and event["source_event_id"] == source_event_id
            ):
                return dict(event)
        return None

    async def insert_seismic_event(self, **fields):
        event = {
            "id": uuid4(),
            "source": fields["source"],
            "source_event_id": fields["source_event_id"],
            "source_location_solution_id": fields.get(
                "source_location_solution_id"
            ),
            "source_magnitude_solution_id": fields.get(
                "source_magnitude_solution_id"
            ),
            "origin_time_utc": fields["origin_time_utc"],
            "magnitude": fields["magnitude"],
            "depth_km": fields.get("depth_km"),
            "latitude": fields["latitude"],
            "longitude": fields["longitude"],
            "municipality_code": fields.get("municipality_code"),
            "department_code": fields.get("department_code"),
            "magnitude_source": fields.get("magnitude_source"),
            "location_source": fields.get("location_source"),
            "description": fields.get("description"),
            "first_detected_at": datetime.now(UTC),
            "last_updated_at": datetime.now(UTC),
            "is_simulated": fields.get("is_simulated", False),
            "notify_real_users": fields.get("notify_real_users", True),
            "processing_status": "SEISMIC_DATA_PRELIMINARY",
            "deactivated_at": None,
        }
        self.events[event["id"]] = event
        return dict(event)

    async def apply_seismic_revision(self, event_id, previous, **fields):
        self.revisions.append(
            {"seismic_event_id": event_id, "previous": dict(previous)}
        )
        event = self.events[event_id]
        event.update(
            source_location_solution_id=fields.get(
                "source_location_solution_id"
            ),
            source_magnitude_solution_id=fields.get(
                "source_magnitude_solution_id"
            ),
            origin_time_utc=fields["origin_time_utc"],
            magnitude=fields["magnitude"],
            depth_km=fields.get("depth_km"),
            latitude=fields["latitude"],
            longitude=fields["longitude"],
            last_updated_at=datetime.now(UTC),
        )
        return dict(event)

    async def list_public_seismic_events(self, visibility_hours, limit):
        cutoff = datetime.now(UTC) - timedelta(hours=visibility_hours)
        now = datetime.now(UTC)

        def alive_by_alert(event_id):
            # Espejo del OR EXISTS del SQL real: una alerta ACTIVE sin
            # vencer mantiene el evento vivo más allá de la ventana.
            return any(
                a["seismic_event_id"] == event_id
                and a["status"] == "ACTIVE"
                and (a["expires_at"] is None or a["expires_at"] > now)
                for a in self.alerts.values()
            )

        rows = [
            dict(event)
            for event in self.events.values()
            if event["deactivated_at"] is None
            and (
                event["origin_time_utc"] > cutoff
                or alive_by_alert(event["id"])
            )
        ]
        rows.sort(key=lambda row: row["origin_time_utc"], reverse=True)
        return rows[:limit]

    async def get_seismic_event(self, event_id):
        event = self.events.get(event_id)
        return dict(event) if event else None

    async def list_seismic_history(self, limit, offset, include_simulated):
        rows = [
            dict(e)
            for e in self.events.values()
            if e["deactivated_at"] is None
            and (include_simulated or not e["is_simulated"])
        ]
        rows.sort(key=lambda r: r["origin_time_utc"], reverse=True)
        return rows[offset:offset + limit], len(rows)

    # --- zonas ---

    async def list_intensity_zones_for_events(self, event_ids):
        return [
            dict(zone)
            for zone in self.zones
            if zone["seismic_event_id"] in event_ids
            and zone["superseded_at"] is None
        ]

    async def replace_intensity_zones(self, event_id, zones, supersede):
        if supersede:
            for zone in self.zones:
                if (
                    zone["seismic_event_id"] == event_id
                    and zone["superseded_at"] is None
                ):
                    zone["superseded_at"] = datetime.now(UTC)
        stored = []
        for zone in zones:
            row = {
                "id": uuid4(),
                "seismic_event_id": event_id,
                "source": zone["source"],
                "severity_level": zone["severity_level"],
                "intensity_min": zone.get("intensity_min"),
                "intensity_max": zone.get("intensity_max"),
                "generated_at": datetime.now(UTC),
                "geometry_geojson": zone["geometry_geojson"],
                "superseded_at": None,
            }
            self.zones.append(row)
            stored.append(dict(row))
        return stored

    # --- ajustes ---

    async def get_seismic_settings(self, account_id):
        row = self.settings.get(account_id)
        return dict(row) if row else None

    async def upsert_seismic_settings(
        self, account_id, enabled, display_name, *, is_test_account=None
    ):
        row = self.settings.setdefault(
            account_id,
            {
                "account_id": account_id,
                "enabled": False,
                "display_name": None,
                "is_test_account": False,
            },
        )
        row["enabled"] = enabled
        if display_name is not None:
            row["display_name"] = display_name
        if is_test_account is not None:
            row["is_test_account"] = is_test_account
        return dict(row)

    # --- candidatos y contactos ---

    async def create_emergency_candidate(self, **fields):
        row = {
            "id": uuid4(),
            "status": "UNREGISTERED",
            "matched_account_id": None,
            "created_at": datetime.now(UTC),
            **fields,
        }
        self.candidates[row["id"]] = row
        return dict(row)

    async def create_emergency_contact(
        self,
        owner_account_id,
        contact_account_id,
        candidate_id,
        direct_display_name=None,
    ):
        live = [
            c
            for c in self.contacts.values()
            if c["owner_account_id"] == owner_account_id
            and c["status"] in ("PENDING", "ACCEPTED")
        ]
        if len(live) >= seismic.MAX_EMERGENCY_CONTACTS:
            return "limit"
        if contact_account_id is not None and any(
            c["contact_account_id"] == contact_account_id
            for c in live
        ):
            return "duplicate"
        # CHG-215: espejo del CHECK emergency_contact_not_self.
        if contact_account_id == owner_account_id:
            return "self"
        row = {
            "id": uuid4(),
            "owner_account_id": owner_account_id,
            "contact_account_id": contact_account_id,
            "candidate_id": candidate_id,
            "direct_display_name": direct_display_name,
            "status": "PENDING",
            "created_at": datetime.now(UTC),
        }
        self.contacts[row["id"]] = row
        return dict(row)

    async def list_emergency_contacts_of_owner(self, owner_account_id):
        rows = []
        for contact in self.contacts.values():
            if contact["owner_account_id"] != owner_account_id:
                continue
            if contact["status"] not in ("PENDING", "ACCEPTED"):
                continue
            candidate = self.candidates.get(contact["candidate_id"])
            settings = self.settings.get(contact["contact_account_id"])
            rows.append(
                {
                    **contact,
                    "candidate_first_names": (
                        candidate["first_names"] if candidate else None
                    ),
                    "candidate_last_names": (
                        candidate["last_names"] if candidate else None
                    ),
                    "contact_display_name": (
                        settings["display_name"] if settings else None
                    ),
                    "accepted_at": None,
                }
            )
        rows.sort(key=lambda row: row["created_at"])
        return rows

    async def list_emergency_invitations_for(self, contact_account_id):
        rows = []
        for contact in self.contacts.values():
            if (
                contact["contact_account_id"] == contact_account_id
                and contact["status"] == "PENDING"
            ):
                owner_settings = self.settings.get(
                    contact["owner_account_id"]
                )
                rows.append(
                    {
                        **contact,
                        "owner_display_name": (
                            owner_settings["display_name"]
                            if owner_settings
                            else None
                        ),
                    }
                )
        return rows

    async def match_unregistered_candidates(
        self, document_hash, phone_normalized
    ):
        rows = []
        for candidate in self.candidates.values():
            if candidate["status"] != "UNREGISTERED":
                continue
            if (
                document_hash is not None
                and candidate["document_hash"] == document_hash
            ) or (
                phone_normalized is not None
                and candidate["phone_normalized"] == phone_normalized
            ):
                rows.append(dict(candidate))
        return rows

    async def link_candidate_to_account(self, candidate_id, account_id):
        candidate = self.candidates.get(candidate_id)
        if candidate is None or candidate["status"] != "UNREGISTERED":
            return None
        candidate["status"] = "MATCHED"
        candidate["matched_account_id"] = account_id
        for contact in self.contacts.values():
            if (
                contact["candidate_id"] == candidate_id
                and contact["contact_account_id"] is None
                and contact["status"] == "PENDING"
                and contact["owner_account_id"] != account_id
            ):
                contact["contact_account_id"] = account_id
        return dict(candidate)

    async def respond_emergency_contact(
        self, contact_id, contact_account_id, accept
    ):
        contact = self.contacts.get(contact_id)
        if (
            contact is None
            or contact["contact_account_id"] != contact_account_id
            or contact["status"] != "PENDING"
        ):
            return None
        contact["status"] = "ACCEPTED" if accept else "REJECTED"
        return dict(contact)

    async def revoke_emergency_contact(self, contact_id, owner_account_id):
        contact = self.contacts.get(contact_id)
        if (
            contact is None
            or contact["owner_account_id"] != owner_account_id
            or contact["status"] not in ("PENDING", "ACCEPTED")
        ):
            return False
        contact["status"] = "REVOKED"
        return True

    # --- alertas ---

    async def compute_affected_accounts(self, event_id):
        event = self.events[event_id]
        rows = []
        for account_id, location in self.presence.items():
            settings = self.settings.get(account_id)
            if not (settings and settings["enabled"]):
                continue
            if not (
                event["notify_real_users"]
                or settings["is_test_account"]
            ):
                continue
            best = None
            for zone in self.zones:
                if (
                    zone["seismic_event_id"] != event_id
                    or zone["superseded_at"] is not None
                ):
                    continue
                geometry = json.loads(zone["geometry_geojson"])
                for polygon in geometry["coordinates"]:
                    if point_in_ring(
                        location["longitude"],
                        location["latitude"],
                        polygon[0],
                    ):
                        order = seismic.SEVERITY_ORDER.index(
                            zone["severity_level"]
                        )
                        if best is None or order < best[0]:
                            best = (order, zone)
            if best is not None:
                rows.append(
                    {
                        "account_id": account_id,
                        "latitude": location["latitude"],
                        "longitude": location["longitude"],
                        "accuracy_meters": location.get(
                            "accuracy_meters"
                        ),
                        "altitude_meters": location.get("altitude_meters"),
                        "altitude_accuracy_meters": location.get(
                            "altitude_accuracy_meters"
                        ),
                        "located_at": location.get("updated_at"),
                        "zone_id": best[1]["id"],
                        "severity_level": best[1]["severity_level"],
                        "display_name": settings.get("display_name"),
                    }
                )
        return rows

    async def create_seismic_alerts(self, event_id, alerts):
        created = []
        for alert in alerts:
            if any(
                a["seismic_event_id"] == event_id
                and a["account_id"] == alert["account_id"]
                for a in self.alerts.values()
            ):
                continue
            row = {
                "id": uuid4(),
                "seismic_event_id": event_id,
                "status": "ACTIVE",
                "safe_confirmed_at": None,
                "resolved_address": None,
                "created_at": datetime.now(UTC),
                **alert,
            }
            self.alerts[row["id"]] = row
            created.append(dict(row))
        return created

    def _expire(self):
        now = datetime.now(UTC)
        for alert in self.alerts.values():
            if (
                alert["status"] == "ACTIVE"
                and alert.get("expires_at") is not None
                and alert["expires_at"] <= now
            ):
                alert["status"] = "EXPIRED"

    async def list_alerts_for_event(self, event_id):
        self._expire()
        return [
            dict(alert)
            for alert in self.alerts.values()
            if alert["seismic_event_id"] == event_id
            and alert["status"] in ("ACTIVE", "SAFE_CONFIRMED")
        ]

    async def accepted_owner_alerts_for_viewer(
        self, event_id, viewer_account_id
    ):
        rows = []
        for alert in self.alerts.values():
            if alert["seismic_event_id"] != event_id:
                continue
            if alert["status"] not in ("ACTIVE", "SAFE_CONFIRMED"):
                continue
            settings = self.settings.get(alert["account_id"])
            if not (settings and settings["enabled"]):
                continue
            if any(
                c["owner_account_id"] == alert["account_id"]
                and c["contact_account_id"] == viewer_account_id
                and c["status"] == "ACCEPTED"
                for c in self.contacts.values()
            ):
                rows.append(
                    {
                        "id": alert["id"],
                        "account_id": alert["account_id"],
                        "status": alert["status"],
                        "severity_level": alert["severity_level"],
                        "display_name": settings["display_name"],
                    }
                )
        return rows

    async def get_alert_for_authorized_viewer(
        self, alert_id, viewer_account_id
    ):
        alert = self.alerts.get(alert_id)
        if alert is None:
            return None
        settings = self.settings.get(alert["account_id"])
        if not (settings and settings["enabled"]):
            return None
        authorized = any(
            c["owner_account_id"] == alert["account_id"]
            and c["contact_account_id"] == viewer_account_id
            and c["status"] == "ACCEPTED"
            for c in self.contacts.values()
        )
        if not authorized:
            return None
        event = self.events[alert["seismic_event_id"]]
        return {
            **alert,
            "display_name": settings["display_name"],
            "magnitude": event["magnitude"],
            "origin_time_utc": event["origin_time_utc"],
            "is_simulated": event["is_simulated"],
        }

    async def list_my_seismic_alerts(self, account_id):
        self._expire()
        rows = []
        for alert in self.alerts.values():
            if alert["account_id"] != account_id:
                continue
            if alert["status"] not in ("ACTIVE", "SAFE_CONFIRMED"):
                continue
            event = self.events[alert["seismic_event_id"]]
            if event["deactivated_at"] is not None:
                continue
            rows.append(
                {
                    **alert,
                    "magnitude": event["magnitude"],
                    "origin_time_utc": event["origin_time_utc"],
                    "is_simulated": event["is_simulated"],
                }
            )
        return rows

    async def confirm_safe(self, account_id, event_id):
        confirmed = []
        for alert in self.alerts.values():
            if alert["account_id"] != account_id:
                continue
            if alert["status"] != "ACTIVE":
                continue
            if event_id is not None and (
                alert["seismic_event_id"] != event_id
            ):
                continue
            alert["status"] = "SAFE_CONFIRMED"
            alert["safe_confirmed_at"] = datetime.now(UTC)
            confirmed.append(dict(alert))
        return confirmed

    async def list_accepted_contact_recipients(self, owner_account_id):
        return [
            {"contact_account_id": c["contact_account_id"]}
            for c in self.contacts.values()
            if c["owner_account_id"] == owner_account_id
            and c["status"] == "ACCEPTED"
            and c["contact_account_id"] is not None
        ]

    async def record_seismic_notifications(self, rows):
        self.notifications.extend(rows)

    async def log_emergency_access(
        self,
        viewer_account_id,
        affected_account_id,
        seismic_event_id,
        alert_id,
        access_type,
    ):
        self.access_log.append(
            {
                "viewer": viewer_account_id,
                "affected": affected_account_id,
                "event": seismic_event_id,
                "alert": alert_id,
                "type": access_type,
            }
        )

    async def deactivate_simulation(self, event_id):
        event = self.events.get(event_id)
        if (
            event is None
            or not event["is_simulated"]
            or event["deactivated_at"] is not None
        ):
            return False
        event["deactivated_at"] = datetime.now(UTC)
        for alert in self.alerts.values():
            if (
                alert["seismic_event_id"] == event_id
                and alert["status"] == "ACTIVE"
            ):
                alert["status"] = "EXPIRED"
        return True

    async def expire_my_active_alerts(self, account_id):
        count = 0
        for alert in self.alerts.values():
            if (
                alert["account_id"] == account_id
                and alert["status"] == "ACTIVE"
            ):
                alert["status"] = "EXPIRED"
                count += 1
        return count

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
        if account_id is not None:
            self.presence[account_id] = {
                "latitude": latitude,
                "longitude": longitude,
                "accuracy_meters": accuracy_meters,
                "altitude_meters": altitude_meters,
                "altitude_accuracy_meters": altitude_accuracy_meters,
                "updated_at": datetime.now(UTC),
            }


def seismic_app(repository, settings=None, sgc_provider=None):
    return create_app(
        settings=settings,
        repository=repository,
        storage=FakeStorage(),
        sgc_provider=sgc_provider,
    )


def seed_network(repo: FakeSeismicRepository):
    """OWNER activó el servicio; CONTACT es su contacto ACEPTADO;
    STRANGER activó el servicio pero no es contacto de nadie."""
    repo.settings[OWNER] = {
        "account_id": OWNER,
        "enabled": True,
        "display_name": "Julián Villamizar",
        "is_test_account": True,
    }
    repo.settings[CONTACT] = {
        "account_id": CONTACT,
        "enabled": True,
        "display_name": "Laura Gómez",
        "is_test_account": True,
    }
    repo.settings[STRANGER] = {
        "account_id": STRANGER,
        "enabled": True,
        "display_name": "Alguien Más",
        "is_test_account": True,
    }
    contact_id = uuid4()
    repo.contacts[contact_id] = {
        "id": contact_id,
        "owner_account_id": OWNER,
        "contact_account_id": CONTACT,
        "candidate_id": None,
        "status": "ACCEPTED",
        "created_at": datetime.now(UTC),
    }
    repo.presence[OWNER] = {
        "latitude": 7.119349,
        "longitude": -73.122742,
        "accuracy_meters": 14.0,
        "updated_at": datetime.now(UTC),
    }
    return contact_id


def seed_event(repo: FakeSeismicRepository, magnitude=5.2):
    event_id = uuid4()
    repo.events[event_id] = {
        "id": event_id,
        "source": "SIMULATED",
        "source_event_id": "SIM-2026-TESTSEED",
        "source_location_solution_id": None,
        "source_magnitude_solution_id": None,
        "origin_time_utc": datetime.now(UTC),
        "magnitude": magnitude,
        "depth_km": 20.0,
        "latitude": 7.12,
        "longitude": -73.12,
        "description": None,
        "is_simulated": True,
        "notify_real_users": False,
        "processing_status": "SEISMIC_DATA_PRELIMINARY",
        "deactivated_at": None,
        "first_detected_at": datetime.now(UTC),
        "last_updated_at": datetime.now(UTC),
    }
    zone_id = uuid4()
    repo.zones.append(
        {
            "id": zone_id,
            "seismic_event_id": event_id,
            "source": "SIMULATED",
            "severity_level": "STRONG",
            "intensity_min": None,
            "intensity_max": None,
            "generated_at": datetime.now(UTC),
            "geometry_geojson": json.dumps(
                {
                    "type": "MultiPolygon",
                    "coordinates": [
                        [
                            [
                                [-73.3, 6.9],
                                [-72.9, 6.9],
                                [-72.9, 7.3],
                                [-73.3, 7.3],
                                [-73.3, 6.9],
                            ]
                        ]
                    ],
                }
            ),
            "superseded_at": None,
        }
    )
    return event_id, zone_id


def seed_alert(repo, event_id, zone_id, account_id=OWNER, expires_at=None):
    alert_id = uuid4()
    repo.alerts[alert_id] = {
        "id": alert_id,
        "seismic_event_id": event_id,
        "account_id": account_id,
        "zone_id": zone_id,
        "severity_level": "STRONG",
        "status": "ACTIVE",
        "event_latitude": 7.119349,
        "event_longitude": -73.122742,
        "event_location_accuracy": 14.0,
        "event_location_timestamp": datetime.now(UTC),
        "resolved_address": None,
        "expires_at": expires_at,
        "safe_confirmed_at": None,
        "created_at": datetime.now(UTC),
    }
    return alert_id


# --- Reglas puras ---


def test_persistencia_del_marcador_por_magnitud():
    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    assert seismic.alert_expiry(2.9, now) == now + timedelta(minutes=3)
    assert seismic.alert_expiry(3.0, now) == now + timedelta(minutes=10)
    assert seismic.alert_expiry(4.4, now) == now + timedelta(minutes=10)
    # Spec §53: 4.5 exacto pertenece al tramo «hasta confirmación».
    assert seismic.alert_expiry(4.5, now) is None
    assert seismic.alert_expiry(6.8, now) is None
    assert seismic.requires_confirmation(4.5) is True
    assert seismic.requires_confirmation(4.49) is False


def test_normalizacion_de_identidad():
    assert (
        seismic.normalize_name("  Julián   VILLAMIZAR ")
        == "julian villamizar"
    )
    assert seismic.normalize_phone("+57 (300) 123-4567") == "3001234567"
    assert seismic.normalize_phone("3001234567") == "3001234567"
    assert seismic.normalize_document("cc-1.098.765-432") == (
        "CC1098765432"
    )
    assert seismic.document_hash("1098765432") == seismic.document_hash(
        " 1.098.765.432 "
    )


def test_matching_jamas_por_nombre_solo():
    candidate_hash = seismic.document_hash("1098765432")
    kwargs = dict(
        candidate_document_hash=candidate_hash,
        candidate_phone_normalized="3001234567",
        candidate_name_normalized="ana martinez",
    )
    # Documento idéntico: coincide.
    assert (
        seismic.match_strength(
            **kwargs,
            claimed_document="1.098.765.432",
            claimed_phone=None,
            claimed_full_name="Otra Persona",
        )
        == "document"
    )
    # Teléfono idéntico + nombre coherente: coincide.
    assert (
        seismic.match_strength(
            **kwargs,
            claimed_document=None,
            claimed_phone="+57 300 123 4567",
            claimed_full_name="Ana María Martínez",
        )
        == "phone"
    )
    # Teléfono idéntico pero nombre ajeno: NO coincide.
    assert (
        seismic.match_strength(
            **kwargs,
            claimed_document=None,
            claimed_phone="3001234567",
            claimed_full_name="Juan Pérez",
        )
        is None
    )
    # Solo nombre: NO coincide (spec §31).
    assert (
        seismic.match_strength(
            **kwargs,
            claimed_document=None,
            claimed_phone=None,
            claimed_full_name="Ana Martínez",
        )
        is None
    )


def test_idempotencia_ante_revisiones():
    stored = seismic.StoredEventSolution(
        location_solution_id="L1",
        magnitude_solution_id="M1",
        magnitude=5.1,
        depth_km=18.0,
        latitude=7.12,
        longitude=-73.12,
    )
    same = seismic.NormalizedSgcEvent(
        source_event_id="SGC-1",
        magnitude=5.1,
        depth_km=18.0,
        latitude=7.12,
        longitude=-73.12,
        origin_time_utc=datetime.now(UTC),
        location_solution_id="L1",
        magnitude_solution_id="M1",
        municipality_code=None,
        department_code=None,
        magnitude_source=None,
        location_source=None,
        payload={},
    )
    assert seismic.is_revision(stored, same) is False
    revised = seismic.NormalizedSgcEvent(
        **{
            **same.__dict__,
            "magnitude": 4.9,
            "magnitude_solution_id": "M2",
        }
    )
    assert seismic.is_revision(stored, revised) is True


def test_zonas_provisionales_y_geometria_manual():
    radii = seismic.provisional_zone_radii_km(5.2, 20.0)
    assert set(radii) == {"STRONG", "MODERATE", "LIGHT"}
    assert radii["LIGHT"] > radii["MODERATE"] > radii["STRONG"] > 0
    assert seismic.provisional_zone_radii_km(2.0, 10.0) == {}
    # Un anillo abierto se rechaza.
    open_ring = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[-73.3, 6.9], [-72.9, 6.9], [-72.9, 7.3], [-73.3, 7.3]]]
        ],
    }
    assert (
        seismic.validate_manual_zone_geometry(open_ring) is not None
    )
    assert (
        seismic.validate_manual_zone_geometry(
            {
                "type": "MultiPolygon",
                "coordinates": [
                    [
                        [
                            [-73.3, 6.9],
                            [-72.9, 6.9],
                            [-72.9, 7.3],
                            [-73.3, 6.9],
                        ]
                    ]
                ],
            }
        )
        is None
    )


def test_coordenada_anonima_redondeada():
    lat, lon = seismic.anonymize_coordinate(7.119349, -73.122742)
    assert (lat, lon) == (7.12, -73.12)


# --- Poller SGC ---


class FakeSgcProvider:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = 0

    async def fetch_recent(self, since):
        self.calls += 1
        if not self.batches:
            return []
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch


def sgc_feature(event_id="SGC-1", magnitude=5.1, mag_solution="M1"):
    return {
        "ESP_ID_EVENTO_TXT": event_id,
        "ESP_ID_SOL_MAGNITUD": mag_solution,
        "ESP_MAGNITUD": magnitude,
        "ESP_ID_SOL_LOCALIZACION": "L1",
        "ESP_PROFUNDIDAD": 18,
        "ESP_FECHA": int(datetime.now(UTC).timestamp() * 1000),
        "ESP_LATITUD": 7.119,
        "ESP_LONGITUD": -73.122,
        "MUN_CODIGO": "68001",
        "DEPT_CODIGO": "68",
    }


@pytest.mark.anyio
async def test_poller_crea_revisa_y_no_duplica():
    repo = FakeSeismicRepository()
    seed_network(repo)
    provider = FakeSgcProvider(
        [
            [sgc_feature(), {"ESP_ID_EVENTO_TXT": "incompleta"}],
            [sgc_feature()],
            [sgc_feature(magnitude=4.9, mag_solution="M2")],
        ]
    )
    first = await seismic_ingest.run_sgc_poll_cycle(
        repo, provider, None
    )
    assert first == {
        "created": 1,
        "revised": 0,
        "unchanged": 0,
        "failed": False,
    }
    assert len(repo.events) == 1
    # La cuenta dentro de la zona provisional quedó alertada.
    assert len(repo.alerts) == 1
    second = await seismic_ingest.run_sgc_poll_cycle(
        repo, provider, None
    )
    assert second["unchanged"] == 1 and second["created"] == 0
    third = await seismic_ingest.run_sgc_poll_cycle(
        repo, provider, None
    )
    assert third["revised"] == 1
    # La revisión quedó en el histórico, no como evento nuevo.
    assert len(repo.events) == 1
    assert len(repo.revisions) == 1
    assert repo.revisions[0]["previous"]["magnitude"] == 5.1
    # Y la alerta no se duplicó.
    assert len(repo.alerts) == 1


@pytest.mark.anyio
async def test_poller_no_propaga_fallos_del_sgc():
    repo = FakeSeismicRepository()
    provider = FakeSgcProvider([RuntimeError("SGC caído")])
    summary = await seismic_ingest.run_sgc_poll_cycle(
        repo, provider, None
    )
    assert summary["failed"] is True
    assert repo.checkpoints["SGC"]["consecutive_failures"] == 1


@pytest.mark.anyio
async def test_servicio_responde_con_poller_roto():
    """Spec §4/§81: el subsistema sísmico no interrumpe la plataforma."""
    repo = FakeSeismicRepository()
    settings = Settings(
        database_url="postgresql://unused",
        database_pool_min_size=1,
        database_pool_max_size=2,
        sgc_poll_enabled=True,
        sgc_poll_interval_seconds=5,
    )
    app = seismic_app(
        repo,
        settings=settings,
        sgc_provider=FakeSgcProvider([RuntimeError("SGC caído")] * 50),
    )
    response = await request_app(app, "GET", "/health/live")
    assert response.status_code == 200


# --- Privacidad del mapa y del panel ---


@pytest.mark.anyio
async def test_publico_ve_triangulos_anonimos():
    repo = FakeSeismicRepository()
    seed_network(repo)
    event_id, zone_id = seed_event(repo)
    seed_alert(repo, event_id, zone_id)
    app = seismic_app(repo)
    response = await request_app(
        app,
        "GET",
        f"/internal/v1/seismic/events/{event_id}/affected",
        headers=headers_for(None),
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["markers"]) == 1
    marker = body["markers"][0]
    assert marker["identified"] is False
    assert marker["displayName"] is None
    assert marker["alertId"] is None
    # Coordenada redondeada, jamás la exacta.
    assert marker["latitude"] == 7.12
    assert marker["longitude"] == -73.12
    assert "7.119349" not in response.text


@pytest.mark.anyio
async def test_contacto_aceptado_identifica_y_extranio_no():
    repo = FakeSeismicRepository()
    seed_network(repo)
    event_id, zone_id = seed_event(repo)
    alert_id = seed_alert(repo, event_id, zone_id)
    app = seismic_app(repo)
    # El contacto aceptado ve nombre y alertId (marcador rojo/morado).
    response = await request_app(
        app,
        "GET",
        f"/internal/v1/seismic/events/{event_id}/affected",
        headers=headers_for(CONTACT),
    )
    marker = response.json()["markers"][0]
    assert marker["identified"] is True
    assert marker["displayName"] == "Julián Villamizar"
    assert marker["alertId"] == str(alert_id)
    # Y el acceso quedó auditado.
    assert any(
        log["type"] == "MARKER" and log["viewer"] == CONTACT
        for log in repo.access_log
    )
    # Un autenticado cualquiera sigue viendo el triángulo anónimo.
    response = await request_app(
        app,
        "GET",
        f"/internal/v1/seismic/events/{event_id}/affected",
        headers=headers_for(STRANGER),
    )
    marker = response.json()["markers"][0]
    assert marker["identified"] is False
    assert marker["displayName"] is None


@pytest.mark.anyio
async def test_panel_solo_para_contacto_y_sin_documento():
    repo = FakeSeismicRepository()
    seed_network(repo)
    event_id, zone_id = seed_event(repo)
    alert_id = seed_alert(repo, event_id, zone_id)
    app = seismic_app(repo)
    response = await request_app(
        app,
        "GET",
        f"/internal/v1/seismic/alerts/{alert_id}",
        headers=headers_for(CONTACT),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["displayName"] == "Julián Villamizar"
    assert body["latitude"] == 7.119349
    assert body["zoneTitle"] == "Sacudida fuerte estimada"
    # Spec §49: el documento no viaja en el panel.
    assert "document" not in response.text.lower()
    assert any(
        log["type"] == "PANEL" and log["viewer"] == CONTACT
        for log in repo.access_log
    )
    # Un extraño recibe lo mismo que ante una alerta inexistente.
    response = await request_app(
        app,
        "GET",
        f"/internal/v1/seismic/alerts/{alert_id}",
        headers=headers_for(STRANGER),
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_revocacion_con_efecto_inmediato():
    repo = FakeSeismicRepository()
    contact_id = seed_network(repo)
    event_id, zone_id = seed_event(repo)
    alert_id = seed_alert(repo, event_id, zone_id)
    app = seismic_app(repo)
    response = await request_app(
        app,
        "DELETE",
        f"/internal/v1/seismic/contacts/{contact_id}",
        headers=headers_for(OWNER),
    )
    assert response.status_code == 204
    # Desde ya: triángulo anónimo y panel cerrado (spec §85).
    response = await request_app(
        app,
        "GET",
        f"/internal/v1/seismic/events/{event_id}/affected",
        headers=headers_for(CONTACT),
    )
    assert response.json()["markers"][0]["identified"] is False
    response = await request_app(
        app,
        "GET",
        f"/internal/v1/seismic/alerts/{alert_id}",
        headers=headers_for(CONTACT),
    )
    assert response.status_code == 404


# --- Ajustes y contactos ---


@pytest.mark.anyio
async def test_opt_in_apagado_por_defecto_y_contactos_exigen_activarlo():
    repo = FakeSeismicRepository()
    app = seismic_app(repo)
    response = await request_app(
        app,
        "GET",
        "/internal/v1/seismic/settings",
        headers=headers_for(OWNER),
    )
    assert response.json()["enabled"] is False
    contacto = {
        "firstNames": "Laura",
        "lastNames": "Gómez",
        "documentType": "Cédula de ciudadanía",
        "documentNumber": "1098765432",
        "phone": "+57 300 123 4567",
    }
    response = await request_app(
        app,
        "POST",
        "/internal/v1/seismic/contacts",
        json=contacto,
        headers=headers_for(OWNER),
    )
    assert response.status_code == 409


@pytest.mark.anyio
async def test_maximo_tres_contactos():
    repo = FakeSeismicRepository()
    app = seismic_app(repo)
    await request_app(
        app,
        "PUT",
        "/internal/v1/seismic/settings",
        json={"enabled": True, "displayName": "Julián Villamizar"},
        headers=headers_for(OWNER),
    )
    for index in range(3):
        response = await request_app(
            app,
            "POST",
            "/internal/v1/seismic/contacts",
            json={
                "firstNames": f"Contacto{index}",
                "lastNames": "De Prueba",
                "documentType": "Cédula de ciudadanía",
                "documentNumber": f"10000000{index}",
                "phone": f"+57 30012345{index:02d}",
            },
            headers=headers_for(OWNER),
        )
        assert response.status_code == 201
    response = await request_app(
        app,
        "POST",
        "/internal/v1/seismic/contacts",
        json={
            "firstNames": "Cuarto",
            "lastNames": "Sobrante",
            "documentType": "Cédula de ciudadanía",
            "documentNumber": "999999999",
            "phone": "+57 3009999999",
        },
        headers=headers_for(OWNER),
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_apagar_el_servicio_detiene_las_alertas():
    repo = FakeSeismicRepository()
    seed_network(repo)
    event_id, zone_id = seed_event(repo)
    seed_alert(repo, event_id, zone_id)
    app = seismic_app(repo)
    response = await request_app(
        app,
        "PUT",
        "/internal/v1/seismic/settings",
        json={"enabled": False},
        headers=headers_for(OWNER),
    )
    assert response.status_code == 200
    assert all(
        alert["status"] != "ACTIVE"
        for alert in repo.alerts.values()
        if alert["account_id"] == OWNER
    )


@pytest.mark.anyio
async def test_matching_de_invitacion_y_aceptacion():
    repo = FakeSeismicRepository()
    seed_network(repo)
    app = seismic_app(repo)
    # OWNER registra a Ana, que aún no tiene cuenta (spec §27 caso B).
    response = await request_app(
        app,
        "POST",
        "/internal/v1/seismic/contacts",
        json={
            "firstNames": "Ana",
            "lastNames": "Martínez",
            "documentType": "Cédula de ciudadanía",
            "documentNumber": "1098765432",
            "phone": "+57 301 555 6677",
        },
        headers=headers_for(OWNER),
    )
    assert response.status_code == 201
    ana = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa07")
    # Ana llega después; con solo el nombre no hay coincidencia.
    response = await request_app(
        app,
        "POST",
        "/internal/v1/seismic/invitations/match",
        json={"displayName": "Ana Martínez"},
        headers=headers_for(ana),
    )
    assert response.json()["invitations"] == []
    # Con su documento la coincidencia es fuerte y aparece.
    response = await request_app(
        app,
        "POST",
        "/internal/v1/seismic/invitations/match",
        json={
            "displayName": "Ana Martínez",
            "documentNumber": "1.098.765.432",
        },
        headers=headers_for(ana),
    )
    invitations = response.json()["invitations"]
    assert len(invitations) == 1
    assert invitations[0]["matchStrength"] == "document"
    assert (
        invitations[0]["ownerDisplayName"] == "Julián Villamizar"
    )
    # Acepta y el vínculo queda activo (spec §33).
    response = await request_app(
        app,
        "POST",
        (
            "/internal/v1/seismic/invitations/"
            f"{invitations[0]['id']}/respond"
        ),
        json={"accept": True, "displayName": "Ana Martínez"},
        headers=headers_for(ana),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ACCEPTED"


# --- «ESTOY BIEN» ---


@pytest.mark.anyio
async def test_estoy_bien_confirma_y_notifica_contactos():
    repo = FakeSeismicRepository()
    seed_network(repo)
    event_id, zone_id = seed_event(repo)
    alert_id = seed_alert(repo, event_id, zone_id)
    app = seismic_app(repo)
    response = await request_app(
        app,
        "POST",
        "/internal/v1/seismic/alerts/mine/confirm-safe",
        json={},
        headers=headers_for(OWNER),
    )
    assert response.status_code == 200
    assert response.json()["confirmed"] == 1
    assert repo.alerts[alert_id]["status"] == "SAFE_CONFIRMED"
    kinds = [n["kind"] for n in repo.notifications]
    assert "SAFE_CONFIRMED" in kinds
    recipients = {
        n["recipient_account_id"]
        for n in repo.notifications
        if n["kind"] == "SAFE_CONFIRMED"
    }
    assert recipients == {CONTACT}
    body = next(
        n["body"]
        for n in repo.notifications
        if n["kind"] == "SAFE_CONFIRMED"
    )
    assert "confirmó que se encuentra bien" in body


# --- Simulador del Super Admin ---


SIMULATION_BODY = {
    "latitude": 7.12,
    "longitude": -73.12,
    "magnitude": 5.2,
    "depthKm": 20,
    "description": "Simulacro de prueba",
}


@pytest.mark.anyio
async def test_simulacro_exige_super_admin():
    repo = FakeSeismicRepository()
    app = seismic_app(repo)
    response = await request_app(
        app,
        "POST",
        "/internal/v1/admin/seismic/simulations",
        json=SIMULATION_BODY,
        headers=headers_for(OWNER),
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_simulacro_solo_alerta_cuentas_de_prueba():
    repo = FakeSeismicRepository()
    seed_network(repo)
    # Una cuenta REAL (no de prueba) también está dentro de la zona.
    real = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa08")
    repo.settings[real] = {
        "account_id": real,
        "enabled": True,
        "display_name": "Cuenta Real",
        "is_test_account": False,
    }
    repo.presence[real] = {
        "latitude": 7.121,
        "longitude": -73.121,
        "accuracy_meters": 8.0,
        "updated_at": datetime.now(UTC),
    }
    app = seismic_app(repo)
    response = await request_app(
        app,
        "POST",
        "/internal/v1/admin/seismic/simulations",
        json=SIMULATION_BODY,
        headers=headers_for(ADMIN, role="super_admin"),
    )
    assert response.status_code == 201
    receipt = response.json()
    assert receipt["sourceEventId"].startswith("SIM-")
    assert receipt["banner"] == seismic.SIMULATED_BANNER
    assert receipt["zonesCreated"] == 3
    # Solo la cuenta de prueba dentro de zona quedó alertada.
    assert receipt["alertsActivated"] == 1
    alerted = {a["account_id"] for a in repo.alerts.values()}
    assert alerted == {OWNER}


@pytest.mark.anyio
async def test_simulacro_con_poligono_manual_irregular():
    repo = FakeSeismicRepository()
    seed_network(repo)
    zona_manual = {
        "severityLevel": "STRONG",
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [
                [
                    [
                        [-73.3, 6.9],
                        [-72.9, 6.95],
                        [-72.85, 7.3],
                        [-73.2, 7.25],
                        [-73.3, 6.9],
                    ]
                ]
            ],
        },
    }
    app = seismic_app(repo)
    response = await request_app(
        app,
        "POST",
        "/internal/v1/admin/seismic/simulations",
        json={**SIMULATION_BODY, "zones": [zona_manual]},
        headers=headers_for(ADMIN, role="super_admin"),
    )
    assert response.status_code == 201
    assert response.json()["zonesCreated"] == 1
    assert response.json()["alertsActivated"] == 1
    # Una zona mal cerrada se rechaza.
    zona_abierta = {
        "severityLevel": "STRONG",
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [
                [[[-73.3, 6.9], [-72.9, 6.95], [-72.85, 7.3]]]
            ],
        },
    }
    response = await request_app(
        app,
        "POST",
        "/internal/v1/admin/seismic/simulations",
        json={**SIMULATION_BODY, "zones": [zona_abierta]},
        headers=headers_for(ADMIN, role="super_admin"),
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_retirar_simulacro():
    repo = FakeSeismicRepository()
    seed_network(repo)
    event_id, zone_id = seed_event(repo)
    seed_alert(repo, event_id, zone_id)
    app = seismic_app(repo)
    response = await request_app(
        app,
        "DELETE",
        f"/internal/v1/admin/seismic/simulations/{event_id}",
        headers=headers_for(ADMIN, role="super_admin"),
    )
    assert response.status_code == 204
    assert repo.events[event_id]["deactivated_at"] is not None
    response = await request_app(
        app,
        "GET",
        f"/internal/v1/seismic/events/{event_id}/affected",
        headers=headers_for(None),
    )
    assert response.status_code == 404


# --- Listado público de eventos ---


@pytest.mark.anyio
async def test_eventos_con_zonas_banda_y_aviso_provisional():
    repo = FakeSeismicRepository()
    seed_network(repo)
    event_id, _zone_id = seed_event(repo)
    app = seismic_app(repo)
    response = await request_app(
        app, "GET", "/internal/v1/seismic/events"
    )
    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1
    event = events[0]
    assert event["id"] == str(event_id)
    assert event["simulatedBanner"] == seismic.SIMULATED_BANNER
    zone = event["zones"][0]
    assert zone["title"] == "Sacudida fuerte estimada"
    assert zone["source"] == "SIMULATED"
    assert zone["geometry"]["type"] == "MultiPolygon"


@pytest.mark.anyio
async def test_cuentas_de_prueba_configurables_por_admin():
    repo = FakeSeismicRepository()
    app = seismic_app(repo)
    user01 = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11")
    user02 = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12")
    response = await request_app(
        app,
        "POST",
        "/internal/v1/admin/seismic/test-accounts",
        json={
            "accounts": [
                {
                    "accountId": str(user01),
                    "displayName": "TEST USER 01",
                    "latitude": 7.12,
                    "longitude": -73.12,
                },
                {
                    "accountId": str(user02),
                    "displayName": "TEST USER 02",
                    "latitude": 7.13,
                    "longitude": -73.13,
                },
            ],
            "relations": [
                {
                    "ownerAccountId": str(user01),
                    "contactAccountId": str(user02),
                }
            ],
        },
        headers=headers_for(ADMIN, role="super_admin"),
    )
    assert response.status_code == 200
    assert response.json()["accountsConfigured"] == 2
    assert response.json()["relationsCreated"] == 1
    assert repo.settings[user01]["is_test_account"] is True
    assert repo.settings[user01]["enabled"] is True
    assert user01 in repo.presence
    assert any(
        c["owner_account_id"] == user01
        and c["contact_account_id"] == user02
        and c["status"] == "ACCEPTED"
        for c in repo.contacts.values()
    )


# CHG-215 — Vínculo directo por ID compartible (sin candidato).


@pytest.mark.anyio
async def test_vinculo_directo_crea_invitacion_pendiente():
    repo = FakeSeismicRepository()
    app = seismic_app(repo)
    await request_app(
        app,
        "PUT",
        "/internal/v1/seismic/settings",
        json={"enabled": True, "displayName": "Julián Villamizar"},
        headers=headers_for(OWNER),
    )
    response = await request_app(
        app,
        "POST",
        "/internal/v1/seismic/contacts/direct",
        json={
            "contactAccountId": str(CONTACT),
            "displayName": "María Paz Rueda",
        },
        headers=headers_for(OWNER),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["displayName"] == "María Paz Rueda"
    assert body["linked"] is True
    # El dueño la ve en su lista con el nombre fijado por el gateway.
    listado = await request_app(
        app,
        "GET",
        "/internal/v1/seismic/contacts",
        headers=headers_for(OWNER),
    )
    nombres = [c["displayName"] for c in listado.json()["contacts"]]
    assert "María Paz Rueda" in nombres
    # Y a la persona le aparece la invitación sin teclear documento.
    invitaciones = await request_app(
        app,
        "POST",
        "/internal/v1/seismic/invitations/match",
        json={"displayName": "María Paz Rueda"},
        headers=headers_for(CONTACT),
    )
    assert len(invitaciones.json()["invitations"]) == 1


@pytest.mark.anyio
async def test_vinculo_directo_rechaza_el_id_propio():
    repo = FakeSeismicRepository()
    app = seismic_app(repo)
    await request_app(
        app,
        "PUT",
        "/internal/v1/seismic/settings",
        json={"enabled": True, "displayName": "Julián Villamizar"},
        headers=headers_for(OWNER),
    )
    response = await request_app(
        app,
        "POST",
        "/internal/v1/seismic/contacts/direct",
        json={
            "contactAccountId": str(OWNER),
            "displayName": "Julián Villamizar",
        },
        headers=headers_for(OWNER),
    )
    assert response.status_code == 422
    assert "propio" in response.json()["detail"]


@pytest.mark.anyio
async def test_vinculo_directo_duplicado_y_limite():
    repo = FakeSeismicRepository()
    app = seismic_app(repo)
    await request_app(
        app,
        "PUT",
        "/internal/v1/seismic/settings",
        json={"enabled": True, "displayName": "Julián Villamizar"},
        headers=headers_for(OWNER),
    )
    primero = await request_app(
        app,
        "POST",
        "/internal/v1/seismic/contacts/direct",
        json={
            "contactAccountId": str(CONTACT),
            "displayName": "María Paz Rueda",
        },
        headers=headers_for(OWNER),
    )
    assert primero.status_code == 201
    repetido = await request_app(
        app,
        "POST",
        "/internal/v1/seismic/contacts/direct",
        json={
            "contactAccountId": str(CONTACT),
            "displayName": "María Paz Rueda",
        },
        headers=headers_for(OWNER),
    )
    assert repetido.status_code == 409
    for index in range(seismic.MAX_EMERGENCY_CONTACTS - 1):
        extra = await request_app(
            app,
            "POST",
            "/internal/v1/seismic/contacts/direct",
            json={
                "contactAccountId": str(uuid4()),
                "displayName": f"Contacto Directo {index}",
            },
            headers=headers_for(OWNER),
        )
        assert extra.status_code == 201
    desbordado = await request_app(
        app,
        "POST",
        "/internal/v1/seismic/contacts/direct",
        json={
            "contactAccountId": str(uuid4()),
            "displayName": "Contacto Sobrante",
        },
        headers=headers_for(OWNER),
    )
    assert desbordado.status_code == 422



# CHG-218 — A las 24 h se retiran los círculos; los triángulos siguen.


@pytest.mark.anyio
async def test_las_zonas_se_retiran_a_las_24_horas_pero_el_evento_sigue():
    repo = FakeSeismicRepository()
    viejo, zona_vieja = seed_event(repo, magnitude=6.5)
    repo.events[viejo]["origin_time_utc"] = datetime.now(UTC) - timedelta(
        hours=25
    )
    # Alerta M ≥ 4.5 sin confirmar: mantiene vivo el evento (regla de
    # intensidad intacta) aunque ya no tenga círculos.
    seed_alert(repo, viejo, zona_vieja, expires_at=None)
    reciente, _ = seed_event(repo, magnitude=5.0)
    app = seismic_app(repo)

    response = await request_app(app, "GET", "/internal/v1/seismic/events")
    assert response.status_code == 200
    por_id = {e["id"]: e for e in response.json()["events"]}

    assert por_id[str(viejo)]["zonesExpired"] is True
    assert por_id[str(viejo)]["zones"] == []
    assert por_id[str(reciente)]["zonesExpired"] is False
    assert len(por_id[str(reciente)]["zones"]) >= 1

    # Los triángulos del evento viejo siguen ahí, como siempre.
    afectados = await request_app(
        app, "GET", f"/internal/v1/seismic/events/{viejo}/affected"
    )
    assert afectados.status_code == 200
    assert len(afectados.json()["markers"]) == 1


# CHG-225 — Los círculos duran lo mismo que la alerta del triángulo.


def test_las_zonas_caducan_con_la_metrica_de_la_alerta():
    origen = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    assert seismic.zones_expiry(2.9, origen, 24) == origen + timedelta(minutes=3)
    assert seismic.zones_expiry(3.0, origen, 24) == origen + timedelta(minutes=10)
    assert seismic.zones_expiry(4.4, origen, 24) == origen + timedelta(minutes=10)
    # M ≥ 4.5: solo el tope general de visibilidad las retira.
    assert seismic.zones_expiry(4.5, origen, 24) == origen + timedelta(hours=24)
    assert seismic.zones_expiry(6.8, origen, 24) == origen + timedelta(hours=24)
    assert seismic.zones_expired(2.9, origen, 24, origen + timedelta(minutes=2)) is False
    assert seismic.zones_expired(2.9, origen, 24, origen + timedelta(minutes=3)) is True
    assert seismic.zones_expired(3.5, origen, 24, origen + timedelta(minutes=9)) is False
    assert seismic.zones_expired(3.5, origen, 24, origen + timedelta(minutes=10)) is True
    assert seismic.zones_expired(5.0, origen, 24, origen + timedelta(hours=23)) is False


@pytest.mark.anyio
async def test_un_sismo_leve_pierde_los_circulos_a_los_minutos_pero_el_fuerte_no():
    repo = FakeSeismicRepository()
    leve, _ = seed_event(repo, magnitude=2.8)
    repo.events[leve]["origin_time_utc"] = datetime.now(UTC) - timedelta(
        minutes=4
    )
    moderado, _ = seed_event(repo, magnitude=3.6)
    repo.events[moderado]["origin_time_utc"] = datetime.now(UTC) - timedelta(
        minutes=11
    )
    moderado_fresco, _ = seed_event(repo, magnitude=3.6)
    repo.events[moderado_fresco]["origin_time_utc"] = datetime.now(
        UTC
    ) - timedelta(minutes=5)
    fuerte, _ = seed_event(repo, magnitude=5.2)
    repo.events[fuerte]["origin_time_utc"] = datetime.now(UTC) - timedelta(
        hours=5
    )
    app = seismic_app(repo)

    response = await request_app(app, "GET", "/internal/v1/seismic/events")
    assert response.status_code == 200
    por_id = {e["id"]: e for e in response.json()["events"]}

    assert por_id[str(leve)]["zonesExpired"] is True
    assert por_id[str(leve)]["zones"] == []
    assert por_id[str(moderado)]["zonesExpired"] is True
    assert por_id[str(moderado)]["zones"] == []
    assert por_id[str(moderado_fresco)]["zonesExpired"] is False
    assert len(por_id[str(moderado_fresco)]["zones"]) >= 1
    assert por_id[str(fuerte)]["zonesExpired"] is False
    assert len(por_id[str(fuerte)]["zones"]) >= 1


# CHG-220 — La altitud viaja de la presencia a la instantánea y al panel.


@pytest.mark.anyio
async def test_la_altitud_llega_al_panel_del_contacto():
    repo = FakeSeismicRepository()
    seed_network(repo)
    event_id, zone_id = seed_event(repo)
    alert_id = seed_alert(repo, event_id, zone_id)
    repo.alerts[alert_id]["event_altitude"] = 959.0
    repo.alerts[alert_id]["event_altitude_accuracy"] = 12.0
    app = seismic_app(repo)
    response = await request_app(
        app,
        "GET",
        f"/internal/v1/seismic/alerts/{alert_id}",
        headers=headers_for(CONTACT),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["altitudeMeters"] == 959.0
    assert body["altitudeAccuracyMeters"] == 12.0
    assert "document" not in json.dumps(body).lower()


@pytest.mark.anyio
async def test_sin_altitud_el_panel_la_declara_nula():
    repo = FakeSeismicRepository()
    seed_network(repo)
    event_id, zone_id = seed_event(repo)
    alert_id = seed_alert(repo, event_id, zone_id)
    app = seismic_app(repo)
    response = await request_app(
        app,
        "GET",
        f"/internal/v1/seismic/alerts/{alert_id}",
        headers=headers_for(CONTACT),
    )
    assert response.status_code == 200
    assert response.json()["altitudeMeters"] is None



# CHG-221 — Registro público de sismos.


@pytest.mark.anyio
async def test_el_registro_lista_todo_lo_guardado_reciente_primero():
    repo = FakeSeismicRepository()
    viejo, _ = seed_event(repo, magnitude=4.1)
    repo.events[viejo]["origin_time_utc"] = datetime.now(UTC) - timedelta(
        days=40
    )
    repo.events[viejo]["source"] = "SGC"
    repo.events[viejo]["is_simulated"] = False
    reciente, _ = seed_event(repo, magnitude=5.5)
    app = seismic_app(repo)

    response = await request_app(
        app, "GET", "/internal/v1/seismic/history?limit=10&offset=0"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [i["id"] for i in body["items"]] == [str(reciente), str(viejo)]
    # Más allá de la ventana de 24 h del mapa, el registro lo conserva.
    assert body["items"][1]["source"] == "SGC"
    assert body["items"][1]["simulatedBanner"] is None
    # El simulacro entra con su banda, jamás como sismo real.
    assert body["items"][0]["isSimulated"] is True
    assert body["items"][0]["simulatedBanner"].startswith("🧪")
    assert "zones" not in body["items"][0]

    sin_simulacros = await request_app(
        app,
        "GET",
        "/internal/v1/seismic/history?limit=10&offset=0&includeSimulated=false",
    )
    assert sin_simulacros.json()["total"] == 1
    assert sin_simulacros.json()["items"][0]["id"] == str(viejo)

    pagina2 = await request_app(
        app, "GET", "/internal/v1/seismic/history?limit=1&offset=1"
    )
    assert [i["id"] for i in pagina2.json()["items"]] == [str(viejo)]


# CHG-222 — El proveedor consume la API del catalogador del SGC.


def catalogador_event(event_id, utc_time, magnitude=3.1, **extra):
    base = {
        "id": event_id,
        "status": "manual",
        "agency": "SGC",
        "place": "Istmina - Chocó, Colombia",
        "closer_towns": "Sipí (Chocó) a 16 km",
        "utc_time": utc_time,
        "local_time": utc_time,
        "magnitude": magnitude,
        "mag_type": "MLr_1",
        "event_type": "earthquake",
        "latitude": 4.5568,
        "longitude": -76.7518,
        "depth": 22.0,
    }
    base.update(extra)
    return base


@pytest.mark.anyio
async def test_proveedor_catalogador_pagina_filtra_y_ordena():
    calls = []
    now = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    def handler(request):
        calls.append((request.method, dict(request.url.params)))
        page = request.url.params.get("page")
        if page == "1":
            body = {
                "count": 3,
                "next": "https://apicatalogador.sgc.gov.co/api/events/search/?page=2",
                "results": {
                    "success": True,
                    "results": [
                        catalogador_event("SGC2026aaa", "2026-08-28 11:30:00", 4.2),
                        catalogador_event(
                            "SGC2026fake", "2026-08-28 11:00:00",
                            event_type="not existing",
                        ),
                        catalogador_event("SGC2026bbb", "2026-08-28 09:15:00", 2.8),
                    ],
                },
            }
        else:
            body = {
                "count": 3,
                "next": None,
                "results": {
                    "success": True,
                    "results": [
                        catalogador_event("SGC2026old", "2026-08-27 10:00:00", 5.0),
                    ],
                },
            }
        return httpx.Response(200, json=body)

    provider = seismic_ingest.HttpSgcEventProvider(
        "https://apicatalogador.sgc.gov.co/api/events/search/",
        transport=httpx.MockTransport(handler),
    )
    features = await provider.fetch_recent(now - timedelta(hours=6))

    # POST paginado; la segunda página se pidió y ahí se alcanzó `since`.
    assert [c[0] for c in calls] == ["POST", "POST"]
    assert [c[1]["page"] for c in calls] == ["1", "2"]
    # Sin «not existing», sin lo anterior a since, y ASCENDENTE.
    assert [f["ESP_ID_EVENTO_TXT"] for f in features] == [
        "SGC2026bbb",
        "SGC2026aaa",
    ]
    primero = features[-1]
    assert primero["ESP_MAGNITUD"] == 4.2
    assert primero["ESP_LUGAR"] == "Istmina - Chocó, Colombia"
    assert primero["ESP_ID_SOL_MAGNITUD"] == "manual"
    assert primero["ESP_FECHA"] == int(
        datetime(2026, 8, 28, 11, 30, tzinfo=UTC).timestamp() * 1000
    )
    # Y el normalizador lo entiende tal cual, con el lugar en palabras.
    normalized = seismic.normalize_sgc_feature(primero)
    assert normalized is not None
    assert normalized.description == "Istmina - Chocó, Colombia"
    assert normalized.origin_time_utc == datetime(
        2026, 8, 28, 11, 30, tzinfo=UTC
    )


@pytest.mark.anyio
async def test_proveedor_catalogador_traduce_errores_de_la_api():
    def handler(request):
        return httpx.Response(200, json={"detail": "Método no permitido."})

    provider = seismic_ingest.HttpSgcEventProvider(
        "https://apicatalogador.sgc.gov.co/api/events/search/",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RuntimeError):
        await provider.fetch_recent(datetime.now(UTC) - timedelta(hours=6))


@pytest.mark.anyio
async def test_el_ciclo_guarda_el_lugar_del_sgc():
    repo = FakeSeismicRepository()
    seed_network(repo)
    feature = seismic_ingest.HttpSgcEventProvider._to_feature(
        catalogador_event("SGC2026ccc", "2026-08-28 11:30:00", 3.3)
    )
    provider = FakeSgcProvider([[feature]])
    summary = await seismic_ingest.run_sgc_poll_cycle(repo, provider, None)
    assert summary["created"] == 1
    stored = next(iter(repo.events.values()))
    assert stored["description"] == "Istmina - Chocó, Colombia"


# CHG-223 — El timeout del catalogador es configurable (tarda 5–12 s).


def test_el_timeout_del_sgc_se_lee_del_entorno(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("SGC_TIMEOUT_SECONDS", "45")
    assert Settings.from_environment().sgc_timeout_seconds == 45.0
    monkeypatch.delenv("SGC_TIMEOUT_SECONDS")
    assert Settings.from_environment().sgc_timeout_seconds == 30.0
