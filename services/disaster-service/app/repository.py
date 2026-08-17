import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

import asyncpg

from .models import (
    AidLocationAvailability,
    AidLocationDirectoryCard,
    AidOfferOwnerSummary,
    AidOfferReceipt,
    CommunityContributionReceipt,
    CommunityMealOfferDirectoryCard,
    ContributionActorKind,
    DataClassification,
    DisasterEvent,
    DisasterEventSuggestion,
    HumanImpactSummary,
    MissingPersonDirectoryCard,
    MissingPersonPublicRecord,
    MissingPersonReportReceipt,
    OperationalMapPoint,
    PersonRecord,
    PersonSuggestion,
    PublicPersonStatus,
    SourceReference,
    TemporaryShelterOfferDirectoryCard,
    UnverifiedBuildingReportReceipt,
    VerificationStatus,
)
from . import moderation, offers

# CHG-075: hora local de Colombia (sin horario de verano) para fijar
# el instante público del último avistamiento.
_BOGOTA_TZ = timezone(timedelta(hours=-5))


@dataclass(frozen=True)
class HumanMapCell:
    """Agregado por celda de grilla para la capa humana (CHG-015).

    Cuando `count == 1` los campos `point_*` describen el único punto
    público anónimo de la celda; nunca incluyen identidad.
    """

    cell_x: int
    cell_y: int
    count: int
    missing: int
    reported_deceased: int
    confirmed_alive: int
    confirmed_deceased: int
    latitude: float
    longitude: float
    west: float
    south: float
    east: float
    north: float
    all_operational: bool
    point_id: UUID | None = None
    point_status: str | None = None
    point_precision: str | None = None
    point_verification: str | None = None
    point_source_name: str | None = None
    point_source_type: str | None = None
    point_source_url: str | None = None
    point_updated_at: datetime | None = None


@dataclass(frozen=True)
class StoredReport:
    """Reporte privado listo para persistir; lo sensible llega cifrado."""

    id: UUID
    idempotency_key: str
    public_case_code: str
    first_names: str
    last_names: str
    aliases: str | None
    birth_date: date | None
    approximate_age: int | None
    gender_identity: str | None
    nationality: str | None
    document_type_encrypted: bytes | None
    document_number_encrypted: bytes | None
    height_cm: int | None
    build: str | None
    skin_tone: str | None
    hair_description: str | None
    eye_description: str | None
    distinctive_marks: str | None
    medical_information_encrypted: bytes | None
    last_seen_date: date
    last_seen_time: str | None
    # CHG-015: coordenadas del último avistamiento. Desde CHG-081
    # proyectan el punto del caso en el mapa operativo al publicarse.
    last_seen_latitude: float | None
    last_seen_longitude: float | None
    department: str
    municipality: str
    last_seen_area: str
    # CHG-113: opcional; quien reporta puede no conocer la vestimenta.
    clothing_description: str | None
    circumstances: str
    additional_description: str | None
    reporter_name_encrypted: bytes
    reporter_relationship: str
    reporter_phone_encrypted: bytes | None
    reporter_email_encrypted: bytes | None
    official_report_number: str | None
    # CHG-054: identidad opaca del reportante con sesión (o None).
    reporter_account_id: UUID | None = None
    # CHG-066: instantánea cifrada de la ubicación del reportante.
    reporter_snapshot_latitude_encrypted: bytes | None = None
    reporter_snapshot_longitude_encrypted: bytes | None = None
    # CHG-094: identificación física detallada (en claro), alertas
    # médicas y placa (cifradas), contexto del desplazamiento y
    # consentimiento del reportante. Todos opcionales.
    tattoo_description: str | None = None
    scars_description: str | None = None
    prosthetics_description: str | None = None
    piercings_and_moles: str | None = None
    mental_health_condition_encrypted: bytes | None = None
    vital_medication_encrypted: bytes | None = None
    severe_allergies_encrypted: bytes | None = None
    belongings_description: str | None = None
    transport_mode: str | None = None
    vehicle_details_encrypted: bytes | None = None
    companions_description: str | None = None
    official_authority_name: str | None = None
    reporter_phone_public: bool = False
    reporter_email_public: bool = False


# CHG-035 — Expediente privado de edificio; lo sensible llega cifrado
# y la llave de idempotencia solo llega hasheada.
@dataclass(frozen=True)
class StoredBuildingReport:
    id: UUID
    public_tracking_code: str
    idempotency_key_hash: str
    building_reference: str
    building_type: str
    department: str
    municipality: str
    sector: str
    location_reference_protected: bytes
    address_protected: bytes | None
    latitude_protected: bytes | None
    longitude_protected: bytes | None
    related_disaster_id: UUID | None
    observed_date: date
    observed_time: str | None
    search_status: str
    occupancy_report: str
    pending_reasons: list[str]
    # CHG-093: detalle cifrado del motivo "Otro".
    pending_reason_detail_protected: bytes | None
    observed_conditions: list[str]
    observation_description_protected: bytes
    reporter_name_protected: bytes
    reporter_role_protected: bytes
    reporter_organization_protected: bytes | None
    reporter_phone_protected: bytes | None
    reporter_email_protected: bytes | None
    official_report_number_protected: bytes | None
    truth_confirmed_at: datetime
    photo_authorization_confirmed_at: datetime
    review_acknowledged_at: datetime
    legal_text_version: str
    actor_account_id: UUID | None
    # CHG-066: instantánea cifrada de la ubicación del reportante.
    reporter_snapshot_latitude_protected: bytes | None = None
    reporter_snapshot_longitude_protected: bytes | None = None


@dataclass(frozen=True)
class BuildingProjection:
    """Datos ya saneados y degradados para proyectar `building_pending`.

    El llamador aplica DEC-014 (precisión y texto público) ANTES de
    construir esto; el repositorio nunca ve campos privados en claro.
    """

    title: str
    location_label: str
    latitude: float
    longitude: float
    related_disaster_id: UUID | None
    source_id: UUID


# Fuente ciudadana fija de la proyección (insertada por la migración 012).
BUILDING_REPORT_SOURCE_ID = UUID(
    "11111111-1111-4111-8111-111111111106"
)


# CHG-034 — Aportes ciudadanos privados; lo sensible llega cifrado.
@dataclass(frozen=True)
class StoredStatusReport:
    id: UUID
    person_id: UUID
    idempotency_key: str
    claimed_outcome: str
    evidence_description_encrypted: bytes
    occurred_at: datetime | None
    location_description_encrypted: bytes | None
    actor_kind: ContributionActorKind
    account_id: UUID | None
    # CHG-077: el reportante era del sector salud al reportar; lo
    # declara el gateway, nunca el cliente final.
    reporter_health_sector: bool = False


@dataclass(frozen=True)
class StoredRating:
    id: UUID
    location_id: UUID
    idempotency_key: str
    rating: int
    evidence_description_encrypted: bytes
    actor_kind: ContributionActorKind
    account_id: UUID | None


# CHG-044 — Oferta comunitaria privada; lo sensible llega cifrado con
# la clave EXCLUSIVA de ofertas y la llave idempotente solo hasheada.
@dataclass(frozen=True)
class StoredAidOffer:
    id: UUID
    tracking_code: str
    kind: str
    account_id: UUID
    idempotency_key_hash: str
    request_fingerprint: str
    related_disaster_id: UUID | None
    title_encrypted: bytes
    description_encrypted: bytes
    area_reference_encrypted: bytes
    exact_address_encrypted: bytes | None
    latitude_encrypted: bytes | None
    longitude_encrypted: bytes | None
    contact_name_encrypted: bytes
    contact_phone_encrypted: bytes | None
    contact_email_encrypted: bytes | None
    department: str
    municipality: str
    available_from: datetime
    available_until: datetime
    consent_recorded_at: datetime
    legal_text_version: str


@dataclass(frozen=True)
class StoredMealOfferDetails:
    servings_available: int
    distribution_mode: str
    meal_description_encrypted: bytes
    allergen_information_encrypted: bytes | None


@dataclass(frozen=True)
class StoredShelterOfferDetails:
    spaces_available: int
    shared_space: bool
    accepts_pets: bool | None
    accessibility_notes_encrypted: bytes | None


class AidOfferIdempotencyConflictError(Exception):
    """Misma llave idempotente con un cuerpo distinto (409)."""


class HealthVerifiedCaseError(Exception):
    """CHG-120: el caso tiene una novedad efectiva del sector salud;
    solo el propio sector salud puede seguir reportando (409)."""


class DeceasedOutcomeFinalError(Exception):
    """CHG-122: el caso está en `deceased`, un desenlace definitivo;
    una novedad de `found` no puede sobrescribirlo (409)."""


@dataclass(frozen=True)
class StoredPhoto:
    id: UUID
    position: int
    storage_key: str
    derived_storage_key: str
    content_type: str
    size_bytes: int
    sha256: str
    exif_removed: bool
    malware_scan: str
    # CHG-094: categoría declarada por quien reporta (o None).
    category: str | None = None


class DisasterRepository(Protocol):
    async def ping(self) -> bool: ...

    async def list_events(
        self,
        disaster_type: str | None,
        verification_status: VerificationStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DisasterEvent], int]: ...

    async def people_overview(
        self,
        recent_limit: int,
    ) -> tuple[HumanImpactSummary, list[PersonRecord]]: ...

    async def list_people_records(
        self,
        statuses: list[str] | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[PersonRecord], int]: ...

    async def operational_map_overview(
        self,
        limit: int,
    ) -> tuple[list[OperationalMapPoint], DataClassification]: ...

    async def platform_change_signal(self) -> str: ...

    async def human_map_overview(
        self,
        west: float,
        south: float,
        east: float,
        north: float,
        cell_size: float,
        statuses: list[str] | None,
    ) -> tuple[list[HumanMapCell], dict[str, int]]: ...

    async def search_missing_persons(
        self,
        query: str,
        limit: int,
    ) -> tuple[list[MissingPersonPublicRecord], int]: ...

    async def create_missing_person_report(
        self,
        report: StoredReport,
        photos: list[StoredPhoto],
    ) -> tuple[MissingPersonReportReceipt, bool]: ...

    # CHG-105 — Fotografía pública del caso.
    async def get_public_person_photo(
        self, case_id: UUID
    ) -> dict | None: ...

    async def withdraw_public_person_photo(
        self, case_id: UUID, withdrawn_by: str
    ) -> bool: ...

    # CHG-091 — Sugerencias difusas para prevenir duplicados.
    async def autocomplete_persons(
        self,
        query: str,
        limit: int,
    ) -> list[PersonSuggestion]: ...

    async def check_person_duplicates(
        self,
        full_name: str,
        limit: int,
    ) -> list[PersonSuggestion]: ...

    # CHG-034 — Directorio humanitario y aportes con evidencia.
    async def search_directory_missing_persons(
        self,
        query: str,
        person_status: PublicPersonStatus | None,
        department: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[MissingPersonDirectoryCard], int]: ...

    async def search_directory_aid_locations(
        self,
        kind: str,
        query: str,
        verification_status: VerificationStatus | None,
        availability_status: AidLocationAvailability | None,
        open_now: bool | None,
        department: str | None,
        min_rating: float | None,
        limit: int,
        offset: int,
    ) -> tuple[list[AidLocationDirectoryCard], int]: ...

    # CHG-122: person_is_publishable se retiró — person_public_status
    # responde publicable (estado) o no (None) en una sola consulta.
    async def person_public_status(
        self, person_id: UUID
    ) -> str | None: ...

    async def person_has_effective_health_report(
        self, person_id: UUID
    ) -> bool: ...

    async def aid_location_is_publishable(
        self, location_id: UUID
    ) -> bool: ...

    async def create_person_status_report(
        self,
        report: StoredStatusReport,
        photos: list[StoredPhoto],
    ) -> tuple[CommunityContributionReceipt, bool]: ...

    async def list_person_status_reports(
        self, person_id: UUID, limit: int
    ) -> tuple[str, list[dict]] | None: ...

    async def create_aid_location_rating(
        self,
        rating: StoredRating,
        photos: list[StoredPhoto],
    ) -> tuple[CommunityContributionReceipt, bool]: ...

    async def decide_person_status_report(
        self,
        report_id: UUID,
        decision: moderation.ModerationDecision,
        decided_by_role: str,
    ) -> bool: ...

    async def decide_aid_location_rating(
        self,
        rating_id: UUID,
        decision: moderation.ModerationDecision,
        decided_by_role: str,
    ) -> bool: ...

    # CHG-035 — Reporte de edificio sin verificar.
    async def create_unverified_building_report(
        self,
        report: StoredBuildingReport,
        files: list[StoredPhoto],
        related_event_name: str | None = None,
    ) -> tuple[UnverifiedBuildingReportReceipt, bool]: ...

    # CHG-092 — Autocompletado creable de "Evento relacionado".
    async def autocomplete_disaster_events(
        self,
        query: str,
        limit: int,
    ) -> list[DisasterEventSuggestion]: ...

    async def decide_unverified_building_report(
        self,
        report_id: UUID,
        decision: moderation.ModerationDecision,
        decided_by_role: str,
        moderation_reason_encrypted: bytes | None = None,
        projection: BuildingProjection | None = None,
    ) -> bool: ...

    # CHG-044 — Ofertas comunitarias de comida y alojamiento.
    async def create_aid_offer(
        self,
        offer: StoredAidOffer,
        meal: StoredMealOfferDetails | None,
        shelter: StoredShelterOfferDetails | None,
    ) -> tuple[AidOfferReceipt, bool]: ...

    async def list_owner_aid_offers(
        self,
        account_id: UUID,
        kind: str | None,
        moderation_status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict], int]: ...

    async def update_owner_aid_offer(
        self,
        account_id: UUID,
        offer_id: UUID,
        expected_version: int,
        availability_status: str | None,
        available_units: int | None,
        available_from: datetime | None,
        available_until: datetime | None,
    ) -> tuple[str, dict | None]: ...

    async def expire_aid_offers(self, batch_size: int) -> int: ...

    async def search_directory_aid_offers(
        self,
        kind: str,
        query: str,
        department: str | None,
        limit: int,
        offset: int,
    ) -> tuple[
        list[
            CommunityMealOfferDirectoryCard
            | TemporaryShelterOfferDirectoryCard
        ],
        int,
    ]: ...

    # CHG-066 — Presencia de visitantes con consentimiento.
    async def upsert_visitor_presence(
        self,
        presence_id: UUID,
        account_id: UUID | None,
        latitude: float,
        longitude: float,
        accuracy_meters: float | None,
        platform: str,
    ) -> None: ...

    async def list_visitor_presence(
        self, window_minutes: int, limit: int
    ) -> tuple[list[dict], int]: ...


# Tarjeta de persona sin fuente registrada: atribución genérica pública.
FALLBACK_PERSON_SOURCE = SourceReference(
    name="Registro público CUSOL",
    source_type="citizen",
    url=None,
)

# CHG-091: umbral de similitud trigram fijado por la especificación
# (SIMILARITY(...) > 0.3).
SIMILARITY_THRESHOLD = 0.3

# CHG-092: umbral para asociar EN SILENCIO un evento existente al
# enviar el reporte. Deliberadamente alto: la resolución difusa amplia
# (0.3) ocurre en la UI donde la persona ve y elige; aquí solo se
# reutiliza una variante de tipeo casi idéntica.
EVENT_MATCH_THRESHOLD = 0.85

# CHG-092: fuente ciudadana compartida de los eventos creados desde
# el formulario de reporte.
CITIZEN_EVENT_SOURCE_NAME = "Reporte ciudadano CUSOL"


class PostgresDisasterRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def ping(self) -> bool:
        return await self._pool.fetchval("SELECT 1") == 1

    async def list_events(
        self,
        disaster_type: str | None,
        verification_status: VerificationStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DisasterEvent], int]:
        clauses: list[str] = []
        filter_values: list[object] = []

        if disaster_type is not None:
            filter_values.append(disaster_type)
            clauses.append(
                f"e.disaster_type = ${len(filter_values)}"
            )

        if verification_status is not None:
            filter_values.append(verification_status)
            clauses.append(
                f"e.verification_status = ${len(filter_values)}"
            )

        where_clause = (
            "WHERE " + " AND ".join(clauses) if clauses else ""
        )
        total = await self._pool.fetchval(
            f"""
            SELECT COUNT(*)
            FROM disaster_service.disaster_events e
            {where_clause}
            """,
            *filter_values,
        )

        query_values = [*filter_values, limit, offset]
        limit_parameter = len(filter_values) + 1
        offset_parameter = len(filter_values) + 2
        rows = await self._pool.fetch(
            f"""
            SELECT
                e.id,
                e.title,
                e.description,
                e.disaster_type,
                e.severity,
                e.verification_status,
                CASE
                    WHEN e.location IS NULL THEN NULL
                    ELSE ST_Y(e.location::geometry)
                END AS latitude,
                CASE
                    WHEN e.location IS NULL THEN NULL
                    ELSE ST_X(e.location::geometry)
                END AS longitude,
                e.occurred_at,
                e.updated_at,
                s.name AS source_name,
                s.source_type,
                s.url AS source_url
            FROM disaster_service.disaster_events e
            INNER JOIN disaster_service.sources s ON s.id = e.source_id
            {where_clause}
            ORDER BY e.updated_at DESC
            LIMIT ${limit_parameter} OFFSET ${offset_parameter}
            """,
            *query_values,
        )

        events = [
            DisasterEvent(
                id=row["id"],
                title=row["title"],
                description=row["description"],
                disaster_type=row["disaster_type"],
                severity=row["severity"],
                verification_status=row["verification_status"],
                latitude=row["latitude"],
                longitude=row["longitude"],
                occurred_at=row["occurred_at"],
                updated_at=row["updated_at"],
                source=SourceReference(
                    name=row["source_name"],
                    source_type=row["source_type"],
                    url=row["source_url"],
                ),
            )
            for row in rows
        ]
        return events, int(total)

    async def people_overview(
        self,
        recent_limit: int,
    ) -> tuple[HumanImpactSummary, list[PersonRecord]]:
        counts = await self._pool.fetch(
            """
            SELECT status, COUNT(*) AS quantity
            FROM disaster_service.people
            GROUP BY status
            """
        )
        by_status = {
            row["status"]: int(row["quantity"]) for row in counts
        }
        summary = HumanImpactSummary(
            missing=by_status.get("missing", 0),
            reported_deceased=by_status.get("reported_deceased", 0),
            confirmed_alive=by_status.get("confirmed_alive", 0),
            confirmed_deceased=by_status.get("confirmed_deceased", 0),
        )

        rows = await self._pool.fetch(
            """
            SELECT
                p.id,
                p.display_name,
                p.status,
                p.location,
                p.related_event,
                p.created_at,
                s.name AS source_name,
                s.source_type,
                s.url AS source_url
            FROM disaster_service.people p
            INNER JOIN disaster_service.sources s ON s.id = p.source_id
            ORDER BY p.created_at DESC
            LIMIT $1
            """,
            recent_limit,
        )
        recent = [
            PersonRecord(
                id=row["id"],
                display_name=row["display_name"],
                status=row["status"],
                location=row["location"],
                related_event=row["related_event"],
                created_at=row["created_at"],
                source=SourceReference(
                    name=row["source_name"],
                    source_type=row["source_type"],
                    url=row["source_url"],
                ),
            )
            for row in rows
        ]
        return summary, recent

    async def list_people_records(
        self,
        statuses: list[str] | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[PersonRecord], int]:
        # CHG-018: búsqueda solo sobre campos públicos del contrato
        # (nombre público, ubicación, evento y nombre de fuente); unaccent
        # y lower cubren tildes y mayúsculas. Orden estable para paginar.
        clauses: list[str] = []
        filter_values: list[object] = []

        if statuses:
            filter_values.append(statuses)
            clauses.append(
                f"p.status::text = ANY(${len(filter_values)}::text[])"
            )
        if search:
            filter_values.append(search)
            position = len(filter_values)
            clauses.append(
                f"""(
                lower(unaccent(p.display_name)) LIKE
                    '%' || lower(unaccent(${position})) || '%'
                OR lower(unaccent(p.location)) LIKE
                    '%' || lower(unaccent(${position})) || '%'
                OR lower(unaccent(p.related_event)) LIKE
                    '%' || lower(unaccent(${position})) || '%'
                OR lower(unaccent(s.name)) LIKE
                    '%' || lower(unaccent(${position})) || '%'
                )"""
            )

        where_clause = (
            "WHERE " + " AND ".join(clauses) if clauses else ""
        )
        total = await self._pool.fetchval(
            f"""
            SELECT COUNT(*)
            FROM disaster_service.people p
            INNER JOIN disaster_service.sources s ON s.id = p.source_id
            {where_clause}
            """,
            *filter_values,
        )

        query_values = [*filter_values, limit, offset]
        limit_parameter = len(filter_values) + 1
        offset_parameter = len(filter_values) + 2
        rows = await self._pool.fetch(
            f"""
            SELECT
                p.id,
                p.display_name,
                p.status,
                p.location,
                p.related_event,
                p.created_at,
                s.name AS source_name,
                s.source_type,
                s.url AS source_url
            FROM disaster_service.people p
            INNER JOIN disaster_service.sources s ON s.id = p.source_id
            {where_clause}
            ORDER BY p.created_at DESC, p.id DESC
            LIMIT ${limit_parameter} OFFSET ${offset_parameter}
            """,
            *query_values,
        )
        records = [
            PersonRecord(
                id=row["id"],
                display_name=row["display_name"],
                status=row["status"],
                location=row["location"],
                related_event=row["related_event"],
                created_at=row["created_at"],
                source=SourceReference(
                    name=row["source_name"],
                    source_type=row["source_type"],
                    url=row["source_url"],
                ),
            )
            for row in rows
        ]
        return records, int(total)

    # CHG-082 — Huella barata de cambios de la portada: cambia cuando
    # entra o se modifica cualquier registro visible en dashboards o
    # mapa; la web la sondea para refrescar al instante.
    async def platform_change_signal(self) -> str:
        return await self._pool.fetchval(
            """
            SELECT md5(concat_ws('|',
                (SELECT COUNT(*)
                 FROM disaster_service.operational_map_points),
                (SELECT COALESCE(MAX(updated_at)::text, '')
                 FROM disaster_service.operational_map_points),
                (SELECT COUNT(*)
                 FROM disaster_service.missing_person_cases),
                (SELECT COALESCE(MAX(updated_at)::text, '')
                 FROM disaster_service.missing_person_cases),
                (SELECT COUNT(*)
                 FROM disaster_service.person_status_reports),
                (SELECT COALESCE(MAX(received_at)::text, '')
                 FROM disaster_service.person_status_reports),
                (SELECT COUNT(*) FROM disaster_service.people),
                (SELECT COALESCE(MAX(created_at)::text, '')
                 FROM disaster_service.people),
                (SELECT COUNT(*) FROM disaster_service.aid_locations),
                (SELECT COALESCE(MAX(updated_at)::text, '')
                 FROM disaster_service.aid_locations),
                -- CHG-125: altas y atenciones de solicitudes de ayuda
                -- también refrescan portada y mapa en vivo.
                (SELECT COUNT(*)
                 FROM disaster_service.help_requests),
                (SELECT COALESCE(MAX(created_at)::text, '')
                 FROM disaster_service.help_requests),
                (SELECT COUNT(*)
                 FROM disaster_service.help_request_attenders)
            ))
            """
        )

    async def operational_map_overview(
        self,
        limit: int,
    ) -> tuple[list[OperationalMapPoint], DataClassification]:
        rows = await self._pool.fetch(
            """
            SELECT
                p.id,
                p.category,
                p.title,
                p.description,
                p.location_label,
                ST_Y(p.location::geometry) AS latitude,
                ST_X(p.location::geometry) AS longitude,
                p.coordinate_precision,
                p.verification_status,
                p.related_disaster_id,
                p.data_classification,
                p.updated_at,
                s.name AS source_name,
                s.source_type,
                s.url AS source_url
            FROM disaster_service.operational_map_points p
            INNER JOIN disaster_service.sources s ON s.id = p.source_id
            WHERE ST_Y(p.location::geometry) BETWEEN -90 AND 90
              AND ST_X(p.location::geometry) BETWEEN -180 AND 180
            ORDER BY p.updated_at DESC
            LIMIT $1
            """,
            limit,
        )
        points = [
            OperationalMapPoint(
                id=row["id"],
                category=row["category"],
                title=row["title"],
                location_label=row["location_label"],
                latitude=row["latitude"],
                longitude=row["longitude"],
                coordinate_precision=row["coordinate_precision"],
                verification_status=row["verification_status"],
                related_disaster_id=row["related_disaster_id"],
                description=row["description"],
                source=SourceReference(
                    name=row["source_name"],
                    source_type=row["source_type"],
                    url=row["source_url"],
                ),
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
        operational = bool(rows) and all(
            row["data_classification"] == "operational" for row in rows
        )
        classification: DataClassification = (
            "operational" if operational else "demonstrative"
        )
        return points, classification

    async def human_map_overview(
        self,
        west: float,
        south: float,
        east: float,
        north: float,
        cell_size: float,
        statuses: list[str] | None,
    ) -> tuple[list[HumanMapCell], dict[str, int]]:
        # Una sola consulta agregada por celda de grilla (sin N+1); el
        # filtro espacial usa && sobre geography para aprovechar el GIST.
        rows = await self._pool.fetch(
            """
            WITH filtered AS (
                SELECT
                    pr.id AS public_id,
                    ST_Y(pr.location::geometry) AS latitude,
                    ST_X(pr.location::geometry) AS longitude,
                    pr.coordinate_precision,
                    pr.verification_status,
                    pr.data_classification,
                    pr.updated_at,
                    p.status,
                    s.name AS source_name,
                    s.source_type,
                    s.url AS source_url
                FROM disaster_service.people_map_projection pr
                INNER JOIN disaster_service.people p
                    ON p.id = pr.person_id
                INNER JOIN disaster_service.sources s
                    ON s.id = p.source_id
                WHERE pr.visibility = 'published'
                  AND (
                    $5::text[] IS NULL
                    OR p.status::text = ANY($5::text[])
                  )
                  AND pr.location && ST_MakeEnvelope(
                        $1, $2, $3, $4, 4326
                      )::geography
            )
            SELECT
                floor((longitude + 180.0) / $6)::int AS cell_x,
                floor((latitude + 90.0) / $6)::int AS cell_y,
                COUNT(*)::int AS count,
                COUNT(*) FILTER (WHERE status = 'missing')::int
                    AS missing,
                COUNT(*) FILTER (WHERE status = 'reported_deceased')::int
                    AS reported_deceased,
                COUNT(*) FILTER (WHERE status = 'confirmed_alive')::int
                    AS confirmed_alive,
                COUNT(*) FILTER (WHERE status = 'confirmed_deceased')::int
                    AS confirmed_deceased,
                AVG(latitude) AS latitude,
                AVG(longitude) AS longitude,
                MIN(longitude) AS west,
                MIN(latitude) AS south,
                MAX(longitude) AS east,
                MAX(latitude) AS north,
                bool_and(data_classification = 'operational')
                    AS all_operational,
                (array_agg(public_id ORDER BY public_id))[1]
                    AS point_id,
                (array_agg(status ORDER BY public_id))[1]
                    AS point_status,
                (array_agg(coordinate_precision ORDER BY public_id))[1]
                    AS point_precision,
                (array_agg(verification_status ORDER BY public_id))[1]
                    AS point_verification,
                (array_agg(source_name ORDER BY public_id))[1]
                    AS point_source_name,
                (array_agg(source_type ORDER BY public_id))[1]
                    AS point_source_type,
                (array_agg(source_url ORDER BY public_id))[1]
                    AS point_source_url,
                (array_agg(updated_at ORDER BY public_id))[1]
                    AS point_updated_at
            FROM filtered
            GROUP BY cell_x, cell_y
            ORDER BY count DESC, cell_x, cell_y
            """,
            west,
            south,
            east,
            north,
            statuses,
            cell_size,
        )
        cells = [
            HumanMapCell(
                cell_x=row["cell_x"],
                cell_y=row["cell_y"],
                count=row["count"],
                missing=row["missing"],
                reported_deceased=row["reported_deceased"],
                confirmed_alive=row["confirmed_alive"],
                confirmed_deceased=row["confirmed_deceased"],
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                west=float(row["west"]),
                south=float(row["south"]),
                east=float(row["east"]),
                north=float(row["north"]),
                all_operational=row["all_operational"],
                point_id=row["point_id"],
                point_status=row["point_status"],
                point_precision=row["point_precision"],
                point_verification=row["point_verification"],
                point_source_name=row["point_source_name"],
                point_source_type=row["point_source_type"],
                point_source_url=row["point_source_url"],
                point_updated_at=row["point_updated_at"],
            )
            for row in rows
        ]
        # CHG-099: el desglose por estado de quienes no se pueden
        # dibujar permite que la capa muestre el total real y no solo
        # lo mapeado, que era lo que contradecía a las cifras.
        unmapped_rows = await self._pool.fetch(
            """
            SELECT p.status::text AS status, COUNT(*)::int AS count
            FROM disaster_service.people p
            WHERE (
                $1::text[] IS NULL
                OR p.status::text = ANY($1::text[])
            )
            AND NOT EXISTS (
                SELECT 1
                FROM disaster_service.people_map_projection pr
                WHERE pr.person_id = p.id
                  AND pr.visibility = 'published'
            )
            GROUP BY p.status
            """,
            statuses,
        )
        unmapped = {
            row["status"]: int(row["count"]) for row in unmapped_rows
        }
        return cells, unmapped

    async def search_missing_persons(
        self,
        query: str,
        limit: int,
    ) -> tuple[list[MissingPersonPublicRecord], int]:
        # Solo casos publicados y solo la proyección pública; unaccent y
        # lower en ambos lados cubren tildes y mayúsculas.
        condition = """
            publication_status = 'published'
            AND (
                lower(unaccent(display_name)) LIKE
                    '%' || lower(unaccent($1)) || '%'
                OR lower(unaccent(array_to_string(aliases, ' '))) LIKE
                    '%' || lower(unaccent($1)) || '%'
                OR lower(public_case_code) LIKE '%' || lower($1) || '%'
                OR approximate_age::TEXT = $1
                OR lower(unaccent(municipality)) LIKE
                    '%' || lower(unaccent($1)) || '%'
                OR lower(unaccent(department)) LIKE
                    '%' || lower(unaccent($1)) || '%'
                OR lower(unaccent(last_seen_area)) LIKE
                    '%' || lower(unaccent($1)) || '%'
                OR lower(unaccent(coalesce(clothing_description, '')))
                    LIKE '%' || lower(unaccent($1)) || '%'
                OR lower(unaccent(coalesce(physical_description, '')))
                    LIKE '%' || lower(unaccent($1)) || '%'
                OR lower(unaccent(coalesce(distinctive_marks, '')))
                    LIKE '%' || lower(unaccent($1)) || '%'
            )
        """
        pattern_source = query.strip()
        total = await self._pool.fetchval(
            f"""
            SELECT COUNT(*)
            FROM disaster_service.missing_person_cases
            WHERE {condition}
            """,
            pattern_source,
        )
        rows = await self._pool.fetch(
            f"""
            SELECT
                id, public_case_code, display_name, aliases,
                approximate_age, last_seen_at, last_seen_area,
                municipality, department, clothing_description,
                physical_description, distinctive_marks,
                public_photo_object_key, map_point_id, updated_at,
                data_classification
            FROM disaster_service.missing_person_cases
            WHERE {condition}
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            pattern_source,
            limit,
        )
        records = [
            MissingPersonPublicRecord(
                id=row["id"],
                public_case_code=row["public_case_code"],
                display_name=row["display_name"],
                aliases=list(row["aliases"]),
                approximate_age=row["approximate_age"],
                last_seen_at=row["last_seen_at"],
                last_seen_area=row["last_seen_area"],
                municipality=row["municipality"],
                department=row["department"],
                clothing_description=row["clothing_description"],
                physical_description=row["physical_description"],
                distinctive_marks=row["distinctive_marks"],
                public_photo_url=public_photo_url_for(
                    row["id"], row["public_photo_object_key"]
                ),
                map_point_id=row["map_point_id"],
                updated_at=row["updated_at"],
                data_classification=row["data_classification"],
            )
            for row in rows
        ]
        return records, int(total)

    # CHG-075: el caso público nace junto con el reporte; solo campos
    # autorizados (nada cifrado, sin contacto, sin coordenadas ni
    # fotografías).
    @staticmethod
    def _public_case_projection(
        report: StoredReport,
    ) -> tuple[str, list[str], int | None, datetime, str | None]:
        display_name = " ".join(
            f"{report.first_names} {report.last_names}".split()
        )
        aliases: list[str] = []
        if report.aliases:
            aliases = [
                alias.strip()
                for alias in re.split(r"[,;]", report.aliases)
                if alias.strip()
            ][:10]
        approximate_age = report.approximate_age
        if approximate_age is None and report.birth_date is not None:
            reference = report.last_seen_date
            birthday = report.birth_date
            years = reference.year - birthday.year - (
                (reference.month, reference.day)
                < (birthday.month, birthday.day)
            )
            if 0 <= years <= 120:
                approximate_age = years
        hour, minute = 0, 0
        if report.last_seen_time:
            hour_text, minute_text = report.last_seen_time.split(":")
            hour, minute = int(hour_text), int(minute_text)
        last_seen_at = datetime(
            report.last_seen_date.year,
            report.last_seen_date.month,
            report.last_seen_date.day,
            hour,
            minute,
            tzinfo=_BOGOTA_TZ,
        )
        physical_parts = [
            part
            for part in (
                f"Estatura {report.height_cm} cm"
                if report.height_cm
                else None,
                f"Contextura: {report.build}" if report.build else None,
                f"Piel: {report.skin_tone}" if report.skin_tone else None,
                f"Cabello: {report.hair_description}"
                if report.hair_description
                else None,
                f"Ojos: {report.eye_description}"
                if report.eye_description
                else None,
            )
            if part
        ]
        physical_description = " · ".join(physical_parts) or None
        return (
            display_name,
            aliases,
            approximate_age,
            last_seen_at,
            physical_description,
        )

    async def create_missing_person_report(
        self,
        report: StoredReport,
        photos: list[StoredPhoto],
    ) -> tuple[MissingPersonReportReceipt, bool]:
        async with self._pool.acquire() as connection:
            try:
                async with connection.transaction():
                    row = await connection.fetchrow(
                        """
                        INSERT INTO disaster_service.missing_person_reports (
                            id, idempotency_key, public_case_code,
                            first_names, last_names, aliases, birth_date,
                            approximate_age, gender_identity, nationality,
                            document_type_encrypted,
                            document_number_encrypted,
                            height_cm, build, skin_tone, hair_description,
                            eye_description, distinctive_marks,
                            medical_information_encrypted,
                            last_seen_date, last_seen_time, department,
                            municipality, last_seen_area,
                            clothing_description, circumstances,
                            additional_description,
                            reporter_name_encrypted, reporter_relationship,
                            reporter_phone_encrypted,
                            reporter_email_encrypted,
                            official_report_number,
                            last_seen_latitude, last_seen_longitude,
                            reporter_account_id,
                            reporter_snapshot_latitude_encrypted,
                            reporter_snapshot_longitude_encrypted,
                            tattoo_description, scars_description,
                            prosthetics_description, piercings_and_moles,
                            mental_health_condition_encrypted,
                            vital_medication_encrypted,
                            severe_allergies_encrypted,
                            belongings_description, transport_mode,
                            vehicle_details_encrypted,
                            companions_description,
                            official_authority_name,
                            reporter_phone_public, reporter_email_public
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                            $11, $12, $13, $14, $15, $16, $17, $18, $19,
                            $20, $21, $22, $23, $24, $25, $26, $27, $28,
                            $29, $30, $31, $32, $33, $34, $35, $36, $37,
                            $38, $39, $40, $41, $42, $43, $44, $45, $46,
                            $47, $48, $49, $50, $51
                        )
                        RETURNING id, public_case_code, status, received_at
                        """,
                        report.id,
                        report.idempotency_key,
                        report.public_case_code,
                        report.first_names,
                        report.last_names,
                        report.aliases,
                        report.birth_date,
                        report.approximate_age,
                        report.gender_identity,
                        report.nationality,
                        report.document_type_encrypted,
                        report.document_number_encrypted,
                        report.height_cm,
                        report.build,
                        report.skin_tone,
                        report.hair_description,
                        report.eye_description,
                        report.distinctive_marks,
                        report.medical_information_encrypted,
                        report.last_seen_date,
                        report.last_seen_time,
                        report.department,
                        report.municipality,
                        report.last_seen_area,
                        report.clothing_description,
                        report.circumstances,
                        report.additional_description,
                        report.reporter_name_encrypted,
                        report.reporter_relationship,
                        report.reporter_phone_encrypted,
                        report.reporter_email_encrypted,
                        report.official_report_number,
                        report.last_seen_latitude,
                        report.last_seen_longitude,
                        report.reporter_account_id,
                        report.reporter_snapshot_latitude_encrypted,
                        report.reporter_snapshot_longitude_encrypted,
                        report.tattoo_description,
                        report.scars_description,
                        report.prosthetics_description,
                        report.piercings_and_moles,
                        report.mental_health_condition_encrypted,
                        report.vital_medication_encrypted,
                        report.severe_allergies_encrypted,
                        report.belongings_description,
                        report.transport_mode,
                        report.vehicle_details_encrypted,
                        report.companions_description,
                        report.official_authority_name,
                        report.reporter_phone_public,
                        report.reporter_email_public,
                    )
                    for photo in photos:
                        await connection.execute(
                            """
                            INSERT INTO
                                disaster_service.missing_person_report_photos (
                                id, report_id, position, storage_key,
                                derived_storage_key, content_type,
                                size_bytes, sha256, exif_removed,
                                malware_scan, category
                            ) VALUES (
                                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                                $11
                            )
                            """,
                            photo.id,
                            report.id,
                            photo.position,
                            photo.storage_key,
                            photo.derived_storage_key,
                            photo.content_type,
                            photo.size_bytes,
                            photo.sha256,
                            photo.exif_removed,
                            photo.malware_scan,
                            photo.category,
                        )
                    # CHG-075: publicación inmediata — la proyección
                    # pública se crea en la misma transacción.
                    (
                        display_name,
                        aliases,
                        approximate_age,
                        last_seen_at,
                        physical_description,
                    ) = self._public_case_projection(report)
                    # CHG-081: con coordenadas del último avistamiento
                    # el caso también se proyecta al mapa operativo
                    # (igual que edificios y alertas de voluntariado).
                    map_point_id = None
                    if (
                        report.last_seen_latitude is not None
                        and report.last_seen_longitude is not None
                    ):
                        map_point_id = await connection.fetchval(
                            """
                            INSERT INTO
                                disaster_service.operational_map_points (
                                category, title, description,
                                location_label, location,
                                coordinate_precision,
                                verification_status, source_id,
                                data_classification, updated_at
                            ) VALUES (
                                'missing_person', $1, $2, $3,
                                ST_SetSRID(
                                    ST_MakePoint($4, $5), 4326
                                )::geography,
                                'exact', 'unverified', $6,
                                'operational', NOW()
                            )
                            RETURNING id
                            """,
                            display_name,
                            "Vista por última vez en "
                            + report.last_seen_area,
                            f"{report.municipality}, "
                            f"{report.department}",
                            report.last_seen_longitude,
                            report.last_seen_latitude,
                            self.MISSING_PERSON_SOURCE_ID,
                        )
                    case_id = await connection.fetchval(
                        """
                        INSERT INTO
                            disaster_service.missing_person_cases (
                            public_case_code, display_name, aliases,
                            approximate_age, last_seen_at,
                            last_seen_area, municipality, department,
                            clothing_description,
                            physical_description, distinctive_marks,
                            map_point_id, public_photo_object_key,
                            publication_status, data_classification
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                            $11, $12, $13, 'published', 'operational'
                        )
                        RETURNING id
                        """,
                        report.public_case_code,
                        display_name,
                        aliases,
                        approximate_age,
                        last_seen_at,
                        report.last_seen_area,
                        report.municipality,
                        report.department,
                        report.clothing_description,
                        physical_description,
                        report.distinctive_marks,
                        map_point_id,
                        # CHG-105: el reportante autorizó compartirla al
                        # enviar, así que se publica con el caso.
                        select_public_photo_key(photos),
                    )
                    # CHG-084: el caso publicado también alimenta la
                    # "situación humana" — fila en people (cifras y
                    # tabla del dashboard) y, con coordenadas, la
                    # proyección humana del mapa (approximate).
                    person_row_id = await connection.fetchval(
                        """
                        INSERT INTO disaster_service.people (
                            source_id, display_name, status, location,
                            related_event, latitude, longitude,
                            missing_person_case_id
                        ) VALUES (
                            $1, $2, 'missing', $3, $4, $5, $6, $7
                        )
                        RETURNING id
                        """,
                        self.MISSING_PERSON_SOURCE_ID,
                        display_name,
                        f"{report.municipality}, {report.department}",
                        "Reporte ciudadano de persona desaparecida",
                        report.last_seen_latitude,
                        report.last_seen_longitude,
                        case_id,
                    )
                    if (
                        report.last_seen_latitude is not None
                        and report.last_seen_longitude is not None
                    ):
                        await connection.execute(
                            _PEOPLE_PROJECTION_INSERT_SQL,
                            person_row_id,
                            report.last_seen_longitude,
                            report.last_seen_latitude,
                        )
                    await connection.execute(
                        """
                        INSERT INTO disaster_service.missing_person_audit (
                            event_type, report_id, detail
                        ) VALUES ($1, $2, $3)
                        """,
                        "report_received",
                        report.id,
                        f"fotos={len(photos)}",
                    )
                    await connection.execute(
                        """
                        INSERT INTO disaster_service.missing_person_audit (
                            event_type, report_id, detail
                        ) VALUES ($1, $2, $3)
                        """,
                        "case_published",
                        report.id,
                        report.public_case_code,
                    )
            except asyncpg.UniqueViolationError:
                # Reintento idempotente: devolver la constancia original.
                existing = await connection.fetchrow(
                    """
                    SELECT id, public_case_code, status, received_at
                    FROM disaster_service.missing_person_reports
                    WHERE idempotency_key = $1
                    """,
                    report.idempotency_key,
                )
                if existing is None:
                    raise
                # CHG-075: la constancia informa la publicación del
                # caso, no el estado interno de verificación.
                return (
                    MissingPersonReportReceipt(
                        id=existing["id"],
                        public_case_code=existing["public_case_code"],
                        status="published",
                        received_at=existing["received_at"],
                    ),
                    False,
                )
        return (
            MissingPersonReportReceipt(
                id=row["id"],
                public_case_code=row["public_case_code"],
                status="published",
                received_at=row["received_at"],
            ),
            True,
        )

    # CHG-034 — Directorio humanitario y aportes con evidencia.

    async def search_directory_missing_persons(
        self,
        query: str,
        person_status: PublicPersonStatus | None,
        department: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[MissingPersonDirectoryCard], int]:
        # Solo proyecciones publicadas; el filtro se aplica antes de
        # paginar y el orden es estable (updated_at DESC, id DESC).
        clauses = ["mc.publication_status = 'published'"]
        values: list[object] = [query.strip()]
        clauses.append(
            """(
            lower(unaccent(mc.display_name)) LIKE
                '%' || lower(unaccent($1)) || '%'
            OR lower(unaccent(array_to_string(mc.aliases, ' '))) LIKE
                '%' || lower(unaccent($1)) || '%'
            OR lower(mc.public_case_code) LIKE '%' || lower($1) || '%'
            OR lower(unaccent(mc.municipality)) LIKE
                '%' || lower(unaccent($1)) || '%'
            OR lower(unaccent(mc.department)) LIKE
                '%' || lower(unaccent($1)) || '%'
            OR lower(unaccent(mc.last_seen_area)) LIKE
                '%' || lower(unaccent($1)) || '%'
            )"""
        )
        if person_status is not None:
            values.append(person_status)
            clauses.append(f"mc.public_status::text = ${len(values)}")
        if department is not None:
            values.append(department.strip())
            clauses.append(
                f"lower(unaccent(mc.department)) = "
                f"lower(unaccent(${len(values)}))"
            )

        where_clause = "WHERE " + " AND ".join(clauses)
        total = await self._pool.fetchval(
            f"""
            SELECT COUNT(*)
            FROM disaster_service.missing_person_cases mc
            {where_clause}
            """,
            *values,
        )
        limit_parameter = len(values) + 1
        offset_parameter = len(values) + 2
        rows = await self._pool.fetch(
            f"""
            SELECT
                mc.id, mc.public_case_code, mc.display_name,
                mc.public_status, mc.approximate_age, mc.last_seen_at,
                mc.last_seen_area, mc.municipality, mc.department,
                mc.public_photo_object_key, mc.updated_at,
                mc.data_classification,
                s.name AS source_name,
                s.source_type,
                s.url AS source_url
            FROM disaster_service.missing_person_cases mc
            LEFT JOIN disaster_service.sources s ON s.id = mc.source_id
            {where_clause}
            ORDER BY mc.updated_at DESC, mc.id DESC
            LIMIT ${limit_parameter} OFFSET ${offset_parameter}
            """,
            *values,
            limit,
            offset,
        )
        cards = [
            MissingPersonDirectoryCard(
                id=row["id"],
                public_case_code=row["public_case_code"],
                display_name=row["display_name"],
                status=row["public_status"],
                approximate_age=row["approximate_age"],
                last_seen_at=row["last_seen_at"],
                last_seen_area=row["last_seen_area"],
                municipality=row["municipality"],
                department=row["department"],
                public_photo_url=public_photo_url_for(
                    row["id"], row["public_photo_object_key"]
                ),
                source=(
                    SourceReference(
                        name=row["source_name"],
                        source_type=row["source_type"],
                        url=row["source_url"],
                    )
                    if row["source_name"] is not None
                    else FALLBACK_PERSON_SOURCE
                ),
                updated_at=row["updated_at"],
                data_classification=row["data_classification"],
            )
            for row in rows
        ]
        return cards, int(total)

    # CHG-105 — Fotografía pública del caso: solo de casos publicados
    # y solo el objeto derivado (sin EXIF); el original en cuarentena
    # jamás se sirve.
    async def get_public_person_photo(self, case_id: UUID) -> dict | None:
        row = await self._pool.fetchrow(
            """
            SELECT mc.public_photo_object_key AS object_key,
                   p.content_type
            FROM disaster_service.missing_person_cases mc
            LEFT JOIN disaster_service.missing_person_report_photos p
                ON p.derived_storage_key = mc.public_photo_object_key
            WHERE mc.id = $1
              AND mc.publication_status = 'published'
              AND mc.public_photo_object_key IS NOT NULL
            """,
            case_id,
        )
        if row is None:
            return None
        return {
            "object_key": row["object_key"],
            "content_type": row["content_type"] or "image/jpeg",
        }

    # CHG-105 — Retirada rápida: deja de publicarse sin tocar el
    # expediente, que conserva la fotografía original.
    async def withdraw_public_person_photo(
        self, case_id: UUID, withdrawn_by: str
    ) -> bool:
        result = await self._pool.execute(
            """
            UPDATE disaster_service.missing_person_cases
            SET public_photo_object_key = NULL,
                public_photo_withdrawn_at = NOW(),
                public_photo_withdrawn_by = $2,
                updated_at = NOW()
            WHERE id = $1 AND public_photo_object_key IS NOT NULL
            """,
            case_id,
            withdrawn_by,
        )
        return result.endswith("1")

    # CHG-091 — Sugerencias difusas para prevenir duplicados. Solo la
    # proyección pública publicada. similarity() cubre nombre completo
    # contra nombre completo; word_similarity() cubre lo que se escribe
    # a medias ("balentina" → "Valentina Gómez (caso demo)" da 0.7 por
    # palabra y solo 0.24 global, que perdería el caso). La subcadena
    # conserva el comportamiento exacto de la búsqueda existente.
    _SUGGESTION_SQL = """
        SELECT
            mc.id, mc.public_case_code, mc.display_name,
            mc.public_status, mc.approximate_age, mc.last_seen_at,
            mc.last_seen_area, mc.municipality, mc.department,
            mc.public_photo_object_key, mc.updated_at,
            mc.data_classification,
            s.name AS source_name, s.source_type, s.url AS source_url,
            GREATEST(
                similarity(
                    disaster_service.immutable_unaccent(mc.display_name),
                    disaster_service.immutable_unaccent($1)
                ),
                word_similarity(
                    disaster_service.immutable_unaccent($1),
                    disaster_service.immutable_unaccent(mc.display_name)
                ),
                word_similarity(
                    disaster_service.immutable_unaccent($1),
                    disaster_service.immutable_unaccent(
                        array_to_string(mc.aliases, ' ')
                    )
                ),
                -- El código es identificador exacto: si coincide, la
                -- sugerencia manda con similitud plena.
                CASE
                    WHEN lower(mc.public_case_code)
                        LIKE '%' || lower($1) || '%'
                    THEN 1.0::real
                    ELSE 0.0::real
                END
            ) AS match_similarity
        FROM disaster_service.missing_person_cases mc
        LEFT JOIN disaster_service.sources s ON s.id = mc.source_id
        WHERE mc.publication_status = 'published'
            AND (
                GREATEST(
                    similarity(
                        disaster_service.immutable_unaccent(
                            mc.display_name
                        ),
                        disaster_service.immutable_unaccent($1)
                    ),
                    word_similarity(
                        disaster_service.immutable_unaccent($1),
                        disaster_service.immutable_unaccent(
                            mc.display_name
                        )
                    ),
                    word_similarity(
                        disaster_service.immutable_unaccent($1),
                        disaster_service.immutable_unaccent(
                            array_to_string(mc.aliases, ' ')
                        )
                    )
                ) > $3
                OR disaster_service.immutable_unaccent(mc.display_name)
                    LIKE '%' || disaster_service.immutable_unaccent($1)
                    || '%'
                OR lower(mc.public_case_code)
                    LIKE '%' || lower($1) || '%'
                OR disaster_service.immutable_unaccent(mc.municipality)
                    LIKE '%' || disaster_service.immutable_unaccent($1)
                    || '%'
            )
        ORDER BY match_similarity DESC, mc.updated_at DESC, mc.id DESC
        LIMIT $2
    """

    def _suggestion_from_row(self, row: dict) -> PersonSuggestion:
        return PersonSuggestion(
            id=row["id"],
            public_case_code=row["public_case_code"],
            display_name=row["display_name"],
            status=row["public_status"],
            approximate_age=row["approximate_age"],
            last_seen_at=row["last_seen_at"],
            last_seen_area=row["last_seen_area"],
            municipality=row["municipality"],
            department=row["department"],
            public_photo_url=public_photo_url_for(
                row["id"], row["public_photo_object_key"]
            ),
            source=(
                SourceReference(
                    name=row["source_name"],
                    source_type=row["source_type"],
                    url=row["source_url"],
                )
                if row["source_name"] is not None
                else FALLBACK_PERSON_SOURCE
            ),
            updated_at=row["updated_at"],
            data_classification=row["data_classification"],
            similarity=min(1.0, float(row["match_similarity"])),
        )

    async def autocomplete_persons(
        self,
        query: str,
        limit: int,
    ) -> list[PersonSuggestion]:
        rows = await self._pool.fetch(
            self._SUGGESTION_SQL,
            query.strip(),
            limit,
            SIMILARITY_THRESHOLD,
        )
        return [self._suggestion_from_row(row) for row in rows]

    async def check_person_duplicates(
        self,
        full_name: str,
        limit: int,
    ) -> list[PersonSuggestion]:
        # Al verificar duplicados la subcadena de municipio/código no
        # aplica: se compara el nombre completo digitado contra el
        # nombre público, exigiendo el umbral de similitud.
        rows = await self._pool.fetch(
            """
            SELECT * FROM (
            """
            + self._SUGGESTION_SQL
            + """
            ) candidates
            WHERE candidates.match_similarity > $3
            """,
            full_name.strip(),
            limit,
            SIMILARITY_THRESHOLD,
        )
        return [self._suggestion_from_row(row) for row in rows]

    async def search_directory_aid_locations(
        self,
        kind: str,
        query: str,
        verification_status: VerificationStatus | None,
        availability_status: AidLocationAvailability | None,
        open_now: bool | None,
        department: str | None,
        min_rating: float | None,
        limit: int,
        offset: int,
    ) -> tuple[list[AidLocationDirectoryCard], int]:
        clauses = [
            "al.publication_status = 'published'",
            "al.kind::text = $1",
        ]
        values: list[object] = [kind, query.strip()]
        clauses.append(
            """(
            lower(unaccent(al.name)) LIKE
                '%' || lower(unaccent($2)) || '%'
            OR lower(unaccent(al.location_label)) LIKE
                '%' || lower(unaccent($2)) || '%'
            OR lower(unaccent(al.municipality)) LIKE
                '%' || lower(unaccent($2)) || '%'
            OR lower(unaccent(al.department)) LIKE
                '%' || lower(unaccent($2)) || '%'
            )"""
        )
        if verification_status is not None:
            values.append(verification_status)
            clauses.append(
                f"al.verification_status::text = ${len(values)}"
            )
        if availability_status is not None:
            values.append(availability_status)
            clauses.append(
                f"al.availability_status::text = ${len(values)}"
            )
        if open_now is not None:
            values.append(open_now)
            clauses.append(f"al.open_now IS NOT DISTINCT FROM ${len(values)}")
        if department is not None:
            values.append(department.strip())
            clauses.append(
                f"lower(unaccent(al.department)) = "
                f"lower(unaccent(${len(values)}))"
            )
        if min_rating is not None:
            values.append(min_rating)
            clauses.append(f"al.average_rating >= ${len(values)}")

        where_clause = "WHERE " + " AND ".join(clauses)
        total = await self._pool.fetchval(
            f"""
            SELECT COUNT(*)
            FROM disaster_service.aid_locations al
            {where_clause}
            """,
            *values,
        )
        limit_parameter = len(values) + 1
        offset_parameter = len(values) + 2
        rows = await self._pool.fetch(
            f"""
            SELECT
                al.id, al.kind, al.name, al.location_label,
                al.municipality, al.department, al.verification_status,
                al.availability_status, al.open_now,
                al.accepted_supplies, al.average_rating,
                al.ratings_count, al.updated_at, al.data_classification,
                s.name AS source_name,
                s.source_type,
                s.url AS source_url
            FROM disaster_service.aid_locations al
            INNER JOIN disaster_service.sources s ON s.id = al.source_id
            {where_clause}
            ORDER BY al.updated_at DESC, al.id DESC
            LIMIT ${limit_parameter} OFFSET ${offset_parameter}
            """,
            *values,
            limit,
            offset,
        )
        cards = [
            AidLocationDirectoryCard(
                kind=row["kind"],
                id=row["id"],
                name=row["name"],
                location_label=row["location_label"],
                municipality=row["municipality"],
                department=row["department"],
                verification_status=row["verification_status"],
                availability_status=row["availability_status"],
                open_now=row["open_now"],
                accepted_supplies=list(row["accepted_supplies"]),
                average_rating=(
                    float(row["average_rating"])
                    if row["average_rating"] is not None
                    else None
                ),
                ratings_count=row["ratings_count"],
                source=SourceReference(
                    name=row["source_name"],
                    source_type=row["source_type"],
                    url=row["source_url"],
                ),
                updated_at=row["updated_at"],
                data_classification=row["data_classification"],
            )
            for row in rows
        ]
        return cards, int(total)

    async def aid_location_is_publishable(self, location_id: UUID) -> bool:
        return bool(
            await self._pool.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM disaster_service.aid_locations
                    WHERE id = $1 AND publication_status = 'published'
                )
                """,
                location_id,
            )
        )

    async def person_has_effective_health_report(
        self, person_id: UUID
    ) -> bool:
        return bool(
            await self._pool.fetchval(
                _PERSON_HAS_EFFECTIVE_HEALTH_REPORT_SQL, person_id
            )
        )

    async def person_public_status(
        self, person_id: UUID
    ) -> str | None:
        """CHG-122: estado público del caso publicado; None si no
        existe o no es publicable (indistinguibles a propósito)."""
        status = await self._pool.fetchval(
            _PERSON_PUBLIC_STATUS_IF_PUBLISHED_SQL, person_id
        )
        return None if status is None else str(status)

    async def list_person_status_reports(
        self, person_id: UUID, limit: int
    ) -> tuple[str, list[dict]] | None:
        """Novedades visibles de una persona publicada (CHG-077).

        Devuelve (public_status, filas sin descifrar) o None si la
        persona no existe o no está publicada. Las rechazadas,
        retiradas o archivadas jamás se listan.
        """
        status = await self._pool.fetchval(
            """
            SELECT public_status
            FROM disaster_service.missing_person_cases
            WHERE id = $1 AND publication_status = 'published'
            """,
            person_id,
        )
        if status is None:
            return None
        rows = await self._pool.fetch(
            """
            SELECT id, claimed_outcome,
                   evidence_description_encrypted,
                   location_description_encrypted,
                   occurred_at, received_at, actor_kind,
                   reporter_health_sector, moderation_status
            FROM disaster_service.person_status_reports
            WHERE person_id = $1
              AND moderation_status NOT IN ('rejected', 'withdrawn')
              AND archived_at IS NULL
            ORDER BY received_at DESC
            LIMIT $2
            """,
            person_id,
            limit,
        )
        return str(status), [dict(row) for row in rows]

    async def create_person_status_report(
        self,
        report: StoredStatusReport,
        photos: list[StoredPhoto],
    ) -> tuple[CommunityContributionReceipt, bool]:
        async with self._pool.acquire() as connection:
            try:
                async with connection.transaction():
                    # CHG-120: con una novedad efectiva del sector
                    # salud, el caso no recibe más novedades salvo del
                    # propio sector salud. El pre-chequeo del endpoint
                    # ya filtró; esta comprobación dentro de la
                    # transacción cierra la carrera entre dos envíos
                    # simultáneos.
                    if not report.reporter_health_sector:
                        blocked = await connection.fetchval(
                            _PERSON_HAS_EFFECTIVE_HEALTH_REPORT_SQL,
                            report.person_id,
                        )
                        if blocked:
                            raise HealthVerifiedCaseError
                    # CHG-122: `deceased` es terminal para TODOS los
                    # roles, sector salud incluido — una "encontrada"
                    # posterior no lo sobrescribe. Igual que arriba,
                    # el pre-chequeo del endpoint ya filtró y esta
                    # comprobación cierra la carrera.
                    if report.claimed_outcome == "found":
                        current_status = await connection.fetchval(
                            _PERSON_PUBLIC_STATUS_IF_PUBLISHED_SQL,
                            report.person_id,
                        )
                        if current_status == "deceased":
                            raise DeceasedOutcomeFinalError
                    # CHG-077: crear una novedad SÍ puede cambiar el
                    # estado público — sector salud de inmediato y el
                    # umbral comunitario de 5 coincidencias; el
                    # recálculo corre en esta misma transacción.
                    row = await connection.fetchrow(
                        """
                        INSERT INTO disaster_service.person_status_reports (
                            id, person_id, idempotency_key,
                            claimed_outcome,
                            evidence_description_encrypted, occurred_at,
                            location_description_encrypted, actor_kind,
                            account_id, reporter_health_sector
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
                        )
                        RETURNING id, moderation_status, actor_kind,
                                  received_at
                        """,
                        report.id,
                        report.person_id,
                        report.idempotency_key,
                        report.claimed_outcome,
                        report.evidence_description_encrypted,
                        report.occurred_at,
                        report.location_description_encrypted,
                        report.actor_kind,
                        report.account_id,
                        report.reporter_health_sector,
                    )
                    await connection.execute(
                        _PERSON_PUBLIC_STATUS_BY_PERSON_SQL,
                        report.person_id,
                    )
                    # CHG-084: el estado humano acompaña al caso.
                    await connection.execute(
                        _PEOPLE_STATUS_BY_PERSON_SQL,
                        report.person_id,
                    )
                    for photo in photos:
                        await connection.execute(
                            """
                            INSERT INTO
                                disaster_service.person_status_report_photos (
                                id, report_id, position, storage_key,
                                derived_storage_key, content_type,
                                size_bytes, sha256, exif_removed,
                                malware_scan
                            ) VALUES (
                                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
                            )
                            """,
                            photo.id,
                            report.id,
                            photo.position,
                            photo.storage_key,
                            photo.derived_storage_key,
                            photo.content_type,
                            photo.size_bytes,
                            photo.sha256,
                            photo.exif_removed,
                            photo.malware_scan,
                        )
                    await connection.execute(
                        """
                        INSERT INTO
                            disaster_service.community_contribution_audit (
                            event_type, contribution_kind,
                            contribution_id, detail
                        ) VALUES ($1, $2, $3, $4)
                        """,
                        "status_report_received",
                        "person_status_report",
                        report.id,
                        f"fotos={len(photos)} actor={report.actor_kind}",
                    )
            except asyncpg.UniqueViolationError:
                existing = await connection.fetchrow(
                    """
                    SELECT id, moderation_status, actor_kind, received_at
                    FROM disaster_service.person_status_reports
                    WHERE idempotency_key = $1
                    """,
                    report.idempotency_key,
                )
                if existing is None:
                    raise
                return (
                    _contribution_receipt(existing),
                    False,
                )
        return _contribution_receipt(row), True

    async def create_aid_location_rating(
        self,
        rating: StoredRating,
        photos: list[StoredPhoto],
    ) -> tuple[CommunityContributionReceipt, bool]:
        async with self._pool.acquire() as connection:
            try:
                async with connection.transaction():
                    # Crear la valoración no afecta promedio ni conteo;
                    # solo las aceptadas cuentan, vía decide_*.
                    row = await connection.fetchrow(
                        """
                        INSERT INTO disaster_service.aid_location_ratings (
                            id, location_id, idempotency_key, rating,
                            evidence_description_encrypted, actor_kind,
                            account_id
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        RETURNING id, moderation_status, actor_kind,
                                  received_at
                        """,
                        rating.id,
                        rating.location_id,
                        rating.idempotency_key,
                        rating.rating,
                        rating.evidence_description_encrypted,
                        rating.actor_kind,
                        rating.account_id,
                    )
                    for photo in photos:
                        await connection.execute(
                            """
                            INSERT INTO
                                disaster_service.aid_location_rating_photos (
                                id, rating_id, position, storage_key,
                                derived_storage_key, content_type,
                                size_bytes, sha256, exif_removed,
                                malware_scan
                            ) VALUES (
                                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
                            )
                            """,
                            photo.id,
                            rating.id,
                            photo.position,
                            photo.storage_key,
                            photo.derived_storage_key,
                            photo.content_type,
                            photo.size_bytes,
                            photo.sha256,
                            photo.exif_removed,
                            photo.malware_scan,
                        )
                    await connection.execute(
                        """
                        INSERT INTO
                            disaster_service.community_contribution_audit (
                            event_type, contribution_kind,
                            contribution_id, detail
                        ) VALUES ($1, $2, $3, $4)
                        """,
                        "rating_received",
                        "aid_location_rating",
                        rating.id,
                        f"fotos={len(photos)} actor={rating.actor_kind}",
                    )
            except asyncpg.UniqueViolationError:
                existing = await connection.fetchrow(
                    """
                    SELECT id, moderation_status, actor_kind, received_at
                    FROM disaster_service.aid_location_ratings
                    WHERE idempotency_key = $1
                    """,
                    rating.idempotency_key,
                )
                if existing is None:
                    raise
                return (
                    _contribution_receipt(existing),
                    False,
                )
        return _contribution_receipt(row), True

    async def decide_person_status_report(
        self,
        report_id: UUID,
        decision: moderation.ModerationDecision,
        decided_by_role: str,
    ) -> bool:
        moderation.ensure_moderator_role(decided_by_role)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                current = await connection.fetchrow(
                    """
                    SELECT moderation_status, person_id
                    FROM disaster_service.person_status_reports
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    report_id,
                )
                if current is None:
                    return False
                moderation.ensure_transition(
                    current["moderation_status"], decision
                )
                await connection.execute(
                    """
                    UPDATE disaster_service.person_status_reports
                    SET moderation_status = $2,
                        decided_at = NOW(),
                        decided_by_role = $3
                    WHERE id = $1
                    """,
                    report_id,
                    decision,
                    decided_by_role,
                )
                # La proyección pública se recalcula en la MISMA
                # transacción con las reglas de prioridad de CHG-077
                # (admin > sector salud > umbral comunitario).
                await connection.execute(
                    _PERSON_PUBLIC_STATUS_BY_PERSON_SQL,
                    current["person_id"],
                )
                # CHG-084: el estado humano acompaña al caso.
                await connection.execute(
                    _PEOPLE_STATUS_BY_PERSON_SQL,
                    current["person_id"],
                )
                await connection.execute(
                    """
                    INSERT INTO
                        disaster_service.community_contribution_audit (
                        event_type, contribution_kind, contribution_id,
                        detail
                    ) VALUES ($1, $2, $3, $4)
                    """,
                    "status_report_decided",
                    "person_status_report",
                    report_id,
                    f"decision={decision} rol={decided_by_role}",
                )
        return True

    async def decide_aid_location_rating(
        self,
        rating_id: UUID,
        decision: moderation.ModerationDecision,
        decided_by_role: str,
    ) -> bool:
        moderation.ensure_moderator_role(decided_by_role)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                current = await connection.fetchrow(
                    """
                    SELECT moderation_status, location_id
                    FROM disaster_service.aid_location_ratings
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    rating_id,
                )
                if current is None:
                    return False
                moderation.ensure_transition(
                    current["moderation_status"], decision
                )
                await connection.execute(
                    """
                    UPDATE disaster_service.aid_location_ratings
                    SET moderation_status = $2,
                        decided_at = NOW(),
                        decided_by_role = $3
                    WHERE id = $1
                    """,
                    rating_id,
                    decision,
                    decided_by_role,
                )
                # Agregado transaccional: promedio y conteo se
                # recalculan sobre valoraciones aceptadas únicamente.
                await connection.execute(
                    """
                    UPDATE disaster_service.aid_locations al
                    SET average_rating = sub.avg_rating,
                        ratings_count = sub.quantity,
                        updated_at = NOW()
                    FROM (
                        SELECT
                            ROUND(AVG(r.rating)::numeric, 2)
                                AS avg_rating,
                            COUNT(*)::int AS quantity
                        FROM disaster_service.aid_location_ratings r
                        WHERE r.location_id = $1
                          AND r.moderation_status = 'accepted'
                    ) sub
                    WHERE al.id = $1
                    """,
                    current["location_id"],
                )
                await connection.execute(
                    """
                    INSERT INTO
                        disaster_service.community_contribution_audit (
                        event_type, contribution_kind, contribution_id,
                        detail
                    ) VALUES ($1, $2, $3, $4)
                    """,
                    "rating_decided",
                    "aid_location_rating",
                    rating_id,
                    f"decision={decision} rol={decided_by_role}",
                )
        return True


    # CHG-035 — Reporte de edificio sin verificar.

    # CHG-092 — Autocompletado creable de "Evento relacionado".
    async def autocomplete_disaster_events(
        self,
        query: str,
        limit: int,
    ) -> list[DisasterEventSuggestion]:
        rows = await self._pool.fetch(
            """
            SELECT
                id, title, disaster_type, verification_status,
                occurred_at,
                GREATEST(
                    similarity(
                        disaster_service.immutable_unaccent(title),
                        disaster_service.immutable_unaccent($1)
                    ),
                    word_similarity(
                        disaster_service.immutable_unaccent($1),
                        disaster_service.immutable_unaccent(title)
                    )
                ) AS match_similarity
            FROM disaster_service.disaster_events
            WHERE
                GREATEST(
                    similarity(
                        disaster_service.immutable_unaccent(title),
                        disaster_service.immutable_unaccent($1)
                    ),
                    word_similarity(
                        disaster_service.immutable_unaccent($1),
                        disaster_service.immutable_unaccent(title)
                    )
                ) > $3
                OR disaster_service.immutable_unaccent(title)
                    LIKE '%' || disaster_service.immutable_unaccent($1)
                    || '%'
            ORDER BY match_similarity DESC, updated_at DESC, id DESC
            LIMIT $2
            """,
            query.strip(),
            limit,
            SIMILARITY_THRESHOLD,
        )
        return [
            DisasterEventSuggestion(
                id=row["id"],
                title=row["title"],
                disaster_type=row["disaster_type"],
                verification_status=row["verification_status"],
                occurred_at=row["occurred_at"],
                similarity=min(1.0, float(row["match_similarity"])),
            )
            for row in rows
        ]

    # CHG-092 — Deduplica o crea el evento nombrado, dentro de la
    # transacción del reporte: exacto normalizado → asocia; casi
    # idéntico (>0.85) → asocia; si no, lo crea sin verificar con la
    # fuente ciudadana compartida.
    async def _resolve_or_create_event(
        self, connection, title: str
    ) -> UUID:
        normalized = title.strip()
        existing = await connection.fetchrow(
            """
            SELECT id,
                similarity(
                    disaster_service.immutable_unaccent(title),
                    disaster_service.immutable_unaccent($1)
                ) AS match_similarity
            FROM disaster_service.disaster_events
            WHERE disaster_service.immutable_unaccent(title)
                    = disaster_service.immutable_unaccent($1)
                OR similarity(
                    disaster_service.immutable_unaccent(title),
                    disaster_service.immutable_unaccent($1)
                ) > $2
            ORDER BY
                (disaster_service.immutable_unaccent(title)
                    = disaster_service.immutable_unaccent($1)) DESC,
                match_similarity DESC
            LIMIT 1
            """,
            normalized,
            EVENT_MATCH_THRESHOLD,
        )
        if existing is not None:
            return existing["id"]

        source_id = await connection.fetchval(
            """
            SELECT id FROM disaster_service.sources
            WHERE name = $1 AND source_type = 'citizen'
            LIMIT 1
            """,
            CITIZEN_EVENT_SOURCE_NAME,
        )
        if source_id is None:
            source_id = await connection.fetchval(
                """
                INSERT INTO disaster_service.sources (name, source_type)
                VALUES ($1, 'citizen')
                RETURNING id
                """,
                CITIZEN_EVENT_SOURCE_NAME,
            )

        return await connection.fetchval(
            """
            INSERT INTO disaster_service.disaster_events (
                source_id, title, disaster_type, verification_status
            ) VALUES ($1, $2, 'reported', 'unverified')
            RETURNING id
            """,
            source_id,
            normalized,
        )

    async def create_unverified_building_report(
        self,
        report: StoredBuildingReport,
        files: list[StoredPhoto],
        related_event_name: str | None = None,
    ) -> tuple[UnverifiedBuildingReportReceipt, bool]:
        related_disaster_id = report.related_disaster_id
        async with self._pool.acquire() as connection:
            try:
                async with connection.transaction():
                    # CHG-092: el nombre digitado se resuelve o crea en
                    # esta misma transacción — si el reporte falla, no
                    # queda un evento huérfano recién creado.
                    if related_event_name:
                        related_disaster_id = (
                            await self._resolve_or_create_event(
                                connection, related_event_name
                            )
                        )
                    # Crear el expediente NUNCA escribe el mapa operativo.
                    row = await connection.fetchrow(
                        """
                        INSERT INTO
                            disaster_service.unverified_building_reports (
                            id, public_tracking_code,
                            idempotency_key_hash, building_reference,
                            building_type, department, municipality,
                            sector, location_reference_protected,
                            address_protected, latitude_protected,
                            longitude_protected, related_disaster_id,
                            observed_date, observed_time, search_status,
                            occupancy_report, pending_reasons,
                            pending_reason_detail_protected,
                            observed_conditions,
                            observation_description_protected,
                            reporter_name_protected,
                            reporter_role_protected,
                            reporter_organization_protected,
                            reporter_phone_protected,
                            reporter_email_protected,
                            official_report_number_protected,
                            truth_confirmed_at,
                            photo_authorization_confirmed_at,
                            review_acknowledged_at, legal_text_version,
                            actor_account_id,
                            reporter_snapshot_latitude_protected,
                            reporter_snapshot_longitude_protected
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                            $11, $12, $13, $14, $15, $16, $17, $18,
                            $19, $20, $21, $22, $23, $24, $25, $26,
                            $27, $28, $29, $30, $31, $32, $33, $34
                        )
                        RETURNING id, public_tracking_code,
                                  moderation_status, created_at
                        """,
                        report.id,
                        report.public_tracking_code,
                        report.idempotency_key_hash,
                        report.building_reference,
                        report.building_type,
                        report.department,
                        report.municipality,
                        report.sector,
                        report.location_reference_protected,
                        report.address_protected,
                        report.latitude_protected,
                        report.longitude_protected,
                        related_disaster_id,
                        report.observed_date,
                        report.observed_time,
                        report.search_status,
                        report.occupancy_report,
                        report.pending_reasons,
                        report.pending_reason_detail_protected,
                        report.observed_conditions,
                        report.observation_description_protected,
                        report.reporter_name_protected,
                        report.reporter_role_protected,
                        report.reporter_organization_protected,
                        report.reporter_phone_protected,
                        report.reporter_email_protected,
                        report.official_report_number_protected,
                        report.truth_confirmed_at,
                        report.photo_authorization_confirmed_at,
                        report.review_acknowledged_at,
                        report.legal_text_version,
                        report.actor_account_id,
                        report.reporter_snapshot_latitude_protected,
                        report.reporter_snapshot_longitude_protected,
                    )
                    for file in files:
                        await connection.execute(
                            """
                            INSERT INTO disaster_service
                                .unverified_building_report_files (
                                id, report_id, position, object_key,
                                derived_object_key, content_type,
                                size_bytes, sha256, malware_scan,
                                exif_removed
                            ) VALUES (
                                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
                            )
                            """,
                            file.id,
                            report.id,
                            file.position,
                            file.storage_key,
                            file.derived_storage_key,
                            file.content_type,
                            file.size_bytes,
                            file.sha256,
                            file.malware_scan,
                            file.exif_removed,
                        )
                    await connection.execute(
                        """
                        INSERT INTO
                            disaster_service.community_contribution_audit (
                            event_type, contribution_kind,
                            contribution_id, detail
                        ) VALUES ($1, $2, $3, $4)
                        """,
                        "building_report_received",
                        "unverified_building_report",
                        report.id,
                        f"fotos={len(files)}",
                    )
            except asyncpg.ForeignKeyViolationError:
                raise
            except asyncpg.UniqueViolationError:
                # Reintento idempotente: misma constancia original.
                existing = await connection.fetchrow(
                    """
                    SELECT id, public_tracking_code, moderation_status,
                           created_at
                    FROM disaster_service.unverified_building_reports
                    WHERE idempotency_key_hash = $1
                    """,
                    report.idempotency_key_hash,
                )
                if existing is None:
                    raise
                return _building_receipt(existing), False
        return _building_receipt(row), True

    async def decide_unverified_building_report(
        self,
        report_id: UUID,
        decision: moderation.ModerationDecision,
        decided_by_role: str,
        moderation_reason_encrypted: bytes | None = None,
        projection: BuildingProjection | None = None,
    ) -> bool:
        moderation.ensure_moderator_role(decided_by_role)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                current = await connection.fetchrow(
                    """
                    SELECT moderation_status, map_point_id
                    FROM disaster_service.unverified_building_reports
                    WHERE id = $1
                    FOR UPDATE
                    """,
                    report_id,
                )
                if current is None:
                    return False
                moderation.ensure_transition(
                    current["moderation_status"], decision
                )
                await connection.execute(
                    """
                    UPDATE disaster_service.unverified_building_reports
                    SET moderation_status = $2,
                        moderated_at = NOW(),
                        moderated_by = $3,
                        moderation_reason_protected = $4,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    report_id,
                    decision,
                    decided_by_role,
                    moderation_reason_encrypted,
                )
                map_point_id = current["map_point_id"]
                projected = False
                if decision == "accepted" and projection is not None:
                    # Proyección saneada creada o actualizada en la
                    # MISMA transacción; el expediente conserva el
                    # enlace interno (nunca público).
                    if map_point_id is None:
                        map_point_id = await connection.fetchval(
                            """
                            INSERT INTO
                                disaster_service.operational_map_points (
                                category, title, description,
                                location_label, location,
                                coordinate_precision,
                                verification_status,
                                related_disaster_id, source_id,
                                data_classification, updated_at
                            ) VALUES (
                                'building_pending', $1, NULL, $2,
                                ST_SetSRID(
                                    ST_MakePoint($3, $4), 4326
                                )::geography,
                                'approximate', 'under_review',
                                $5, $6, 'demonstrative', NOW()
                            )
                            RETURNING id
                            """,
                            projection.title,
                            projection.location_label,
                            projection.longitude,
                            projection.latitude,
                            projection.related_disaster_id,
                            projection.source_id,
                        )
                        await connection.execute(
                            """
                            UPDATE disaster_service
                                .unverified_building_reports
                            SET map_point_id = $2
                            WHERE id = $1
                            """,
                            report_id,
                            map_point_id,
                        )
                    else:
                        await connection.execute(
                            """
                            UPDATE disaster_service.operational_map_points
                            SET title = $2,
                                location_label = $3,
                                location = ST_SetSRID(
                                    ST_MakePoint($4, $5), 4326
                                )::geography,
                                related_disaster_id = $6,
                                updated_at = NOW()
                            WHERE id = $1
                            """,
                            map_point_id,
                            projection.title,
                            projection.location_label,
                            projection.longitude,
                            projection.latitude,
                            projection.related_disaster_id,
                        )
                    projected = True
                elif decision != "accepted" and map_point_id is not None:
                    # Rechazo o retiro posterior elimina la proyección.
                    await connection.execute(
                        """
                        UPDATE disaster_service.unverified_building_reports
                        SET map_point_id = NULL
                        WHERE id = $1
                        """,
                        report_id,
                    )
                    await connection.execute(
                        """
                        DELETE FROM disaster_service.operational_map_points
                        WHERE id = $1
                        """,
                        map_point_id,
                    )
                await connection.execute(
                    """
                    INSERT INTO
                        disaster_service.community_contribution_audit (
                        event_type, contribution_kind, contribution_id,
                        detail
                    ) VALUES ($1, $2, $3, $4)
                    """,
                    "building_report_decided",
                    "unverified_building_report",
                    report_id,
                    f"decision={decision} rol={decided_by_role} "
                    f"proyeccion={'si' if projected else 'no'}",
                )
        return True


    # CHG-044 — Ofertas comunitarias de comida y alojamiento.

    async def create_aid_offer(
        self,
        offer: StoredAidOffer,
        meal: StoredMealOfferDetails | None,
        shelter: StoredShelterOfferDetails | None,
    ) -> tuple[AidOfferReceipt, bool]:
        async with self._pool.acquire() as connection:
            try:
                async with connection.transaction():
                    # Expediente + detalle + auditoría: todo o nada.
                    # Crear una oferta JAMÁS escribe la proyección.
                    row = await connection.fetchrow(
                        """
                        INSERT INTO disaster_service.aid_offers (
                            id, tracking_code, kind, account_id,
                            idempotency_key, request_fingerprint,
                            related_disaster_id, title_encrypted,
                            description_encrypted,
                            area_reference_encrypted,
                            exact_address_encrypted, latitude_encrypted,
                            longitude_encrypted, contact_name_encrypted,
                            contact_phone_encrypted,
                            contact_email_encrypted, department,
                            municipality, available_from,
                            available_until, truth_confirmed,
                            contact_consent, review_acknowledged,
                            public_summary_consent, consent_recorded_at,
                            legal_text_version
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                            $11, $12, $13, $14, $15, $16, $17, $18,
                            $19, $20, TRUE, TRUE, TRUE, TRUE, $21, $22
                        )
                        RETURNING id, tracking_code, kind,
                                  moderation_status,
                                  availability_status, received_at,
                                  version
                        """,
                        offer.id,
                        offer.tracking_code,
                        offer.kind,
                        offer.account_id,
                        offer.idempotency_key_hash,
                        offer.request_fingerprint,
                        offer.related_disaster_id,
                        offer.title_encrypted,
                        offer.description_encrypted,
                        offer.area_reference_encrypted,
                        offer.exact_address_encrypted,
                        offer.latitude_encrypted,
                        offer.longitude_encrypted,
                        offer.contact_name_encrypted,
                        offer.contact_phone_encrypted,
                        offer.contact_email_encrypted,
                        offer.department,
                        offer.municipality,
                        offer.available_from,
                        offer.available_until,
                        offer.consent_recorded_at,
                        offer.legal_text_version,
                    )
                    if meal is not None:
                        await connection.execute(
                            """
                            INSERT INTO disaster_service
                                .community_meal_offer_details (
                                offer_id, servings_available,
                                distribution_mode,
                                meal_description_encrypted,
                                allergen_information_encrypted,
                                food_safety_confirmed
                            ) VALUES ($1, $2, $3, $4, $5, TRUE)
                            """,
                            offer.id,
                            meal.servings_available,
                            meal.distribution_mode,
                            meal.meal_description_encrypted,
                            meal.allergen_information_encrypted,
                        )
                    if shelter is not None:
                        await connection.execute(
                            """
                            INSERT INTO disaster_service
                                .temporary_shelter_offer_details (
                                offer_id, spaces_available, shared_space,
                                accepts_pets,
                                accessibility_notes_encrypted,
                                shelter_safety_confirmed
                            ) VALUES ($1, $2, $3, $4, $5, TRUE)
                            """,
                            offer.id,
                            shelter.spaces_available,
                            shelter.shared_space,
                            shelter.accepts_pets,
                            shelter.accessibility_notes_encrypted,
                        )
                    await connection.execute(
                        """
                        INSERT INTO
                            disaster_service.community_contribution_audit (
                            event_type, contribution_kind,
                            contribution_id, detail
                        ) VALUES ($1, $2, $3, $4)
                        """,
                        "aid_offer_received",
                        _offer_admin_kind(offer.kind),
                        offer.id,
                        "actor=authenticated",
                    )
            except asyncpg.ForeignKeyViolationError:
                raise
            except asyncpg.UniqueViolationError:
                existing = await connection.fetchrow(
                    """
                    SELECT id, tracking_code, kind, moderation_status,
                           availability_status, received_at, version,
                           request_fingerprint
                    FROM disaster_service.aid_offers
                    WHERE account_id = $1 AND idempotency_key = $2
                    """,
                    offer.account_id,
                    offer.idempotency_key_hash,
                )
                if existing is None:
                    raise
                if not offers.same_fingerprint(
                    existing["request_fingerprint"],
                    offer.request_fingerprint,
                ):
                    raise AidOfferIdempotencyConflictError()
                return _aid_offer_receipt(existing), False
        return _aid_offer_receipt(row), True

    async def list_owner_aid_offers(
        self,
        account_id: UUID,
        kind: str | None,
        moderation_status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict], int]:
        clauses = ["o.account_id = $1"]
        values: list[object] = [account_id]
        if kind is not None:
            values.append(kind)
            clauses.append(f"o.kind::text = ${len(values)}")
        if moderation_status is not None:
            values.append(moderation_status)
            clauses.append(
                f"({_OFFER_MODERATION_EXPR}) = ${len(values)}"
            )
        where_clause = "WHERE " + " AND ".join(clauses)
        total = await self._pool.fetchval(
            f"""
            SELECT COUNT(*)
            FROM disaster_service.aid_offers o
            {where_clause}
            """,
            *values,
        )
        limit_parameter = len(values) + 1
        offset_parameter = len(values) + 2
        rows = await self._pool.fetch(
            f"""
            SELECT {_OFFER_OWNER_COLUMNS}
            FROM disaster_service.aid_offers o
            LEFT JOIN disaster_service.community_meal_offer_details m
                ON m.offer_id = o.id
            LEFT JOIN disaster_service.temporary_shelter_offer_details t
                ON t.offer_id = o.id
            {where_clause}
            ORDER BY o.updated_at DESC, o.id DESC
            LIMIT ${limit_parameter} OFFSET ${offset_parameter}
            """,
            *values,
            limit,
            offset,
        )
        return [dict(row) for row in rows], int(total)

    async def update_owner_aid_offer(
        self,
        account_id: UUID,
        offer_id: UUID,
        expected_version: int,
        availability_status: str | None,
        available_units: int | None,
        available_from: datetime | None,
        available_until: datetime | None,
    ) -> tuple[str, dict | None]:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                current = await connection.fetchrow(
                    """
                    SELECT o.id, o.kind::text AS kind, o.version,
                           o.availability_status::text
                               AS availability_status,
                           o.available_from, o.available_until,
                           o.archived_at
                    FROM disaster_service.aid_offers o
                    WHERE o.id = $1 AND o.account_id = $2
                    FOR UPDATE
                    """,
                    offer_id,
                    account_id,
                )
                # Oferta ajena o inexistente: indistinguibles (404).
                if current is None:
                    return "not_found", None
                if current["archived_at"] is not None:
                    raise offers.OwnerTransitionError(
                        "La oferta está archivada y no admite cambios."
                    )
                if current["version"] != expected_version:
                    return "version_conflict", None
                resolved_availability, resolved_units = (
                    offers.resolve_owner_update(
                        current["kind"],
                        current["availability_status"],
                        availability_status,
                        available_units,
                    )
                )
                new_from = available_from or current["available_from"]
                new_until = available_until or current["available_until"]
                if new_until <= new_from:
                    raise offers.OwnerUpdateInvalidError(
                        "availableUntil debe ser posterior a "
                        "availableFrom."
                    )
                if (
                    available_until is not None
                    and new_until <= datetime.now(UTC)
                ):
                    raise offers.OwnerUpdateInvalidError(
                        "availableUntil debe estar en el futuro."
                    )
                final_availability = (
                    resolved_availability
                    or current["availability_status"]
                )
                await connection.execute(
                    """
                    UPDATE disaster_service.aid_offers
                    SET availability_status = $2,
                        available_from = $3,
                        available_until = $4,
                        version = version + 1,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    offer_id,
                    final_availability,
                    new_from,
                    new_until,
                )
                if resolved_units is not None:
                    detail_table = (
                        "disaster_service.community_meal_offer_details"
                        if current["kind"] == "community_meal"
                        else "disaster_service"
                        ".temporary_shelter_offer_details"
                    )
                    units_column = (
                        "servings_available"
                        if current["kind"] == "community_meal"
                        else "spaces_available"
                    )
                    await connection.execute(
                        f"""
                        UPDATE {detail_table}
                        SET {units_column} = $2
                        WHERE offer_id = $1
                        """,
                        offer_id,
                        resolved_units,
                    )
                # La proyección pública (si existe) se sincroniza en la
                # MISMA transacción: pausar/completar/retirar/vencer la
                # oculta porque el directorio solo lista `active`.
                await connection.execute(
                    """
                    UPDATE disaster_service.aid_offer_publications
                    SET availability_status = $2,
                        available_from = $3,
                        available_until = $4,
                        available_units = COALESCE($5, available_units),
                        updated_at = NOW()
                    WHERE offer_id = $1
                    """,
                    offer_id,
                    final_availability,
                    new_from,
                    new_until,
                    resolved_units,
                )
                await connection.execute(
                    """
                    INSERT INTO
                        disaster_service.community_contribution_audit (
                        event_type, contribution_kind, contribution_id,
                        detail
                    ) VALUES ($1, $2, $3, $4)
                    """,
                    "aid_offer_owner_updated",
                    _offer_admin_kind(current["kind"]),
                    offer_id,
                    f"estado={final_availability}",
                )
                updated = await connection.fetchrow(
                    f"""
                    SELECT {_OFFER_OWNER_COLUMNS}
                    FROM disaster_service.aid_offers o
                    LEFT JOIN
                        disaster_service.community_meal_offer_details m
                        ON m.offer_id = o.id
                    LEFT JOIN
                        disaster_service.temporary_shelter_offer_details t
                        ON t.offer_id = o.id
                    WHERE o.id = $1
                    """,
                    offer_id,
                )
        return "ok", dict(updated)

    async def expire_aid_offers(self, batch_size: int) -> int:
        expired_total = 0
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                rows = await connection.fetch(
                    """
                    SELECT id, kind::text AS kind
                    FROM disaster_service.aid_offers
                    WHERE availability_status IN
                          ('scheduled', 'active', 'paused')
                      AND available_until <= NOW()
                    ORDER BY available_until
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    batch_size,
                )
                if not rows:
                    return 0
                identifiers = [row["id"] for row in rows]
                await connection.execute(
                    """
                    UPDATE disaster_service.aid_offers
                    SET availability_status = 'expired',
                        version = version + 1,
                        updated_at = NOW()
                    WHERE id = ANY($1::uuid[])
                    """,
                    identifiers,
                )
                await connection.execute(
                    """
                    UPDATE disaster_service.aid_offer_publications
                    SET availability_status = 'expired',
                        updated_at = NOW()
                    WHERE offer_id = ANY($1::uuid[])
                    """,
                    identifiers,
                )
                for row in rows:
                    await connection.execute(
                        """
                        INSERT INTO
                            disaster_service.community_contribution_audit (
                            event_type, contribution_kind,
                            contribution_id, detail
                        ) VALUES ($1, $2, $3, $4)
                        """,
                        "aid_offer_expired",
                        _offer_admin_kind(row["kind"]),
                        row["id"],
                        "estado=expired",
                    )
                expired_total = len(rows)
        return expired_total

    async def search_directory_aid_offers(
        self,
        kind: str,
        query: str,
        department: str | None,
        limit: int,
        offset: int,
    ) -> tuple[
        list[
            CommunityMealOfferDirectoryCard
            | TemporaryShelterOfferDirectoryCard
        ],
        int,
    ]:
        # Solo la proyección pública activa, vigente y con capacidad;
        # jamás se une con columnas privadas del expediente.
        clauses = [
            "p.publication_status = 'published'",
            "p.availability_status = 'active'",
            "p.available_from <= NOW()",
            "p.available_until >= NOW()",
            "p.available_units > 0",
            "p.kind::text = $1",
        ]
        values: list[object] = [kind, query.strip()]
        clauses.append(
            """(
            lower(unaccent(p.title)) LIKE
                '%' || lower(unaccent($2)) || '%'
            OR lower(unaccent(p.description)) LIKE
                '%' || lower(unaccent($2)) || '%'
            OR lower(unaccent(p.area_reference)) LIKE
                '%' || lower(unaccent($2)) || '%'
            OR lower(unaccent(p.municipality)) LIKE
                '%' || lower(unaccent($2)) || '%'
            OR lower(unaccent(p.department)) LIKE
                '%' || lower(unaccent($2)) || '%'
            )"""
        )
        if department is not None:
            values.append(department.strip())
            clauses.append(
                f"lower(unaccent(p.department)) = "
                f"lower(unaccent(${len(values)}))"
            )
        where_clause = "WHERE " + " AND ".join(clauses)
        total = await self._pool.fetchval(
            f"""
            SELECT COUNT(*)
            FROM disaster_service.aid_offer_publications p
            {where_clause}
            """,
            *values,
        )
        limit_parameter = len(values) + 1
        offset_parameter = len(values) + 2
        rows = await self._pool.fetch(
            f"""
            SELECT
                p.id, p.public_offer_code, p.kind::text AS kind,
                p.title, p.description, p.area_reference,
                p.municipality, p.department,
                p.availability_status::text AS availability_status,
                p.available_from, p.available_until, p.available_units,
                p.distribution_mode::text AS distribution_mode,
                p.meal_description, p.allergen_information,
                p.shared_space, p.accepts_pets, p.accessibility_notes,
                p.verification_status::text AS verification_status,
                p.data_classification, p.updated_at,
                s.name AS source_name,
                s.source_type,
                s.url AS source_url
            FROM disaster_service.aid_offer_publications p
            INNER JOIN disaster_service.sources s ON s.id = p.source_id
            {where_clause}
            ORDER BY p.updated_at DESC, p.id DESC
            LIMIT ${limit_parameter} OFFSET ${offset_parameter}
            """,
            *values,
            limit,
            offset,
        )
        cards: list[
            CommunityMealOfferDirectoryCard
            | TemporaryShelterOfferDirectoryCard
        ] = []
        for row in rows:
            source = SourceReference(
                name=row["source_name"],
                source_type=row["source_type"],
                url=row["source_url"],
            )
            if row["kind"] == "community_meal":
                cards.append(
                    CommunityMealOfferDirectoryCard(
                        id=row["id"],
                        public_offer_code=row["public_offer_code"],
                        title=row["title"],
                        description=row["description"],
                        area_reference=row["area_reference"],
                        municipality=row["municipality"],
                        department=row["department"],
                        availability_status=row["availability_status"],
                        available_from=row["available_from"],
                        available_until=row["available_until"],
                        servings_available=row["available_units"],
                        distribution_mode=row["distribution_mode"],
                        meal_description=row["meal_description"],
                        allergen_information=row["allergen_information"],
                        verification_status=row["verification_status"],
                        source=source,
                        updated_at=row["updated_at"],
                        data_classification=row["data_classification"],
                    )
                )
            else:
                cards.append(
                    TemporaryShelterOfferDirectoryCard(
                        id=row["id"],
                        public_offer_code=row["public_offer_code"],
                        title=row["title"],
                        description=row["description"],
                        area_reference=row["area_reference"],
                        municipality=row["municipality"],
                        department=row["department"],
                        availability_status=row["availability_status"],
                        available_from=row["available_from"],
                        available_until=row["available_until"],
                        spaces_available=row["available_units"],
                        shared_space=row["shared_space"],
                        accepts_pets=row["accepts_pets"],
                        accessibility_notes=row["accessibility_notes"],
                        verification_status=row["verification_status"],
                        source=source,
                        updated_at=row["updated_at"],
                        data_classification=row["data_classification"],
                    )
                )
        return cards, int(total)

    # CHG-066 — Presencia de visitantes con consentimiento.

    async def upsert_visitor_presence(
        self,
        presence_id: UUID,
        account_id: UUID | None,
        latitude: float,
        longitude: float,
        accuracy_meters: float | None,
        platform: str,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO disaster_service.visitor_presence (
                presence_id, account_id, latitude, longitude,
                accuracy_meters, platform
            ) VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (presence_id) DO UPDATE SET
                account_id = COALESCE(
                    EXCLUDED.account_id,
                    disaster_service.visitor_presence.account_id
                ),
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                accuracy_meters = EXCLUDED.accuracy_meters,
                platform = EXCLUDED.platform,
                updated_at = NOW()
            """,
            presence_id,
            account_id,
            latitude,
            longitude,
            accuracy_meters,
            platform,
        )

    async def list_visitor_presence(
        self, window_minutes: int, limit: int
    ) -> tuple[list[dict], int]:
        # Retención corta: purga oportunista de filas viejas (>24 h).
        await self._pool.execute(
            """
            DELETE FROM disaster_service.visitor_presence
            WHERE updated_at < NOW() - INTERVAL '24 hours'
            """
        )
        total = await self._pool.fetchval(
            """
            SELECT COUNT(*)
            FROM disaster_service.visitor_presence
            WHERE updated_at >= NOW() - ($1 || ' minutes')::interval
            """,
            str(window_minutes),
        )
        rows = await self._pool.fetch(
            """
            SELECT presence_id, account_id, latitude, longitude,
                   accuracy_meters, platform, first_seen_at, updated_at
            FROM disaster_service.visitor_presence
            WHERE updated_at >= NOW() - ($1 || ' minutes')::interval
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            str(window_minutes),
            limit,
        )
        return [dict(row) for row in rows], int(total)

    # CHG-069 — "Mi espacio": reportes propios con novedades de
    # terceros y alertas ciudadanas de voluntariado.

    VOLUNTEER_ALERT_SOURCE_ID = UUID(
        "11111111-1111-4111-8111-111111111108"
    )

    # CHG-081 — Fuente fija de los puntos de persona desaparecida
    # (provisionada en la migración 022).
    MISSING_PERSON_SOURCE_ID = UUID(
        "11111111-1111-4111-8111-111111111109"
    )

    async def list_my_reports(self, account_id: UUID) -> list[dict]:
        rows = await self._pool.fetch(
            """
            SELECT r.id,
                   'missing_person_report' AS kind,
                   r.public_case_code AS reference_code,
                   (r.first_names || ' ' || r.last_names) AS title,
                   r.status::text AS status,
                   r.received_at
            FROM disaster_service.missing_person_reports r
            WHERE r.reporter_account_id = $1
            UNION ALL
            SELECT b.id,
                   'unverified_building_report' AS kind,
                   b.public_tracking_code AS reference_code,
                   b.building_reference AS title,
                   b.moderation_status::text AS status,
                   b.created_at AS received_at
            FROM disaster_service.unverified_building_reports b
            WHERE b.actor_account_id = $1
            ORDER BY received_at DESC
            LIMIT 200
            """,
            account_id,
        )
        return [dict(row) for row in rows]

    async def list_report_novelties(
        self, account_id: UUID, report_ids: list[UUID]
    ) -> dict[UUID, list[dict]]:
        """Novedades de estado que OTRAS personas aportaron sobre los
        casos originados en reportes de esta cuenta."""
        if not report_ids:
            return {}
        rows = await self._pool.fetch(
            """
            SELECT r.id AS report_id,
                   psr.claimed_outcome::text AS claimed_outcome,
                   psr.moderation_status::text AS moderation_status,
                   psr.received_at
            FROM disaster_service.missing_person_reports r
            INNER JOIN disaster_service.missing_person_cases c
                ON c.public_case_code = r.public_case_code
            INNER JOIN disaster_service.person_status_reports psr
                ON psr.person_id = c.id
            WHERE r.id = ANY($2::uuid[])
              AND r.reporter_account_id = $1
              AND psr.account_id IS DISTINCT FROM $1
            ORDER BY psr.received_at DESC
            """,
            account_id,
            report_ids,
        )
        novelties: dict[UUID, list[dict]] = {}
        for row in rows:
            novelties.setdefault(row["report_id"], []).append(dict(row))
        return novelties

    async def create_volunteer_alert(
        self,
        account_id: UUID,
        description: str,
        address: str,
        latitude: float,
        longitude: float,
    ) -> dict:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                map_point_id = await connection.fetchval(
                    """
                    INSERT INTO disaster_service.operational_map_points (
                        category, title, description, location_label,
                        location, coordinate_precision,
                        verification_status, source_id,
                        data_classification, updated_at
                    ) VALUES (
                        'volunteers_needed', 'Se necesitan voluntarios',
                        $1, $2,
                        ST_SetSRID(ST_MakePoint($3, $4), 4326)::geography,
                        'exact', 'unverified', $5, 'operational', NOW()
                    )
                    RETURNING id
                    """,
                    description,
                    address,
                    longitude,
                    latitude,
                    self.VOLUNTEER_ALERT_SOURCE_ID,
                )
                row = await connection.fetchrow(
                    """
                    INSERT INTO disaster_service.volunteer_alerts (
                        account_id, description, address,
                        latitude, longitude, map_point_id
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id, description, address, latitude,
                              longitude, status, created_at, updated_at
                    """,
                    account_id,
                    description,
                    address,
                    latitude,
                    longitude,
                    map_point_id,
                )
        return dict(row)

    async def list_my_volunteer_alerts(
        self, account_id: UUID
    ) -> list[dict]:
        rows = await self._pool.fetch(
            """
            SELECT id, description, address, latitude, longitude,
                   status, created_at, updated_at
            FROM disaster_service.volunteer_alerts
            WHERE account_id = $1
            ORDER BY created_at DESC
            LIMIT 100
            """,
            account_id,
        )
        return [dict(row) for row in rows]

    async def resolve_volunteer_alert(
        self, account_id: UUID, alert_id: UUID
    ) -> dict | None:
        """Marca resuelta una alerta PROPIA y retira su marcador; el
        expediente no se borra (nunca borramos)."""
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    UPDATE disaster_service.volunteer_alerts
                    SET status = 'resolved', updated_at = NOW()
                    WHERE id = $2 AND account_id = $1
                      AND status = 'active'
                    RETURNING id, description, address, latitude,
                              longitude, status, created_at, updated_at,
                              map_point_id
                    """,
                    account_id,
                    alert_id,
                )
                if row is None:
                    return None
                if row["map_point_id"] is not None:
                    await connection.execute(
                        """
                        DELETE FROM
                            disaster_service.operational_map_points
                        WHERE id = $1
                          AND category = 'volunteers_needed'
                        """,
                        row["map_point_id"],
                    )
        result = dict(row)
        result.pop("map_point_id", None)
        return result

    # CHG-125 — «Necesitamos ayuda»: la vigencia manda. Ninguna
    # consulta devuelve solicitudes con expires_at vencido; nada se
    # borra ni cambia de estado al expirar (DEC-125-02).

    async def create_help_request(
        self,
        *,
        idempotency_key: str,
        public_code: str,
        reporter_account_id: UUID | None,
        description: str,
        address: str,
        latitude: float | None,
        longitude: float | None,
        notification_radius_km: int | None,
        duration_hours: int,
        photo_storage_key: str | None,
        photo_derived_storage_key: str | None,
        photo_content_type: str | None,
    ) -> tuple[dict, bool]:
        """Inserta la solicitud calculando expires_at en servidor.

        El reintento con la misma Idempotency-Key devuelve la fila
        original con created=False (mismo pacto que los reportes).
        """
        row = await self._pool.fetchrow(
            """
            INSERT INTO disaster_service.help_requests (
                idempotency_key, public_code, reporter_account_id,
                description, address, latitude, longitude,
                notification_radius_km, duration_hours,
                photo_storage_key, photo_derived_storage_key,
                photo_content_type, expires_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                NOW() + make_interval(hours => $9)
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id, public_code, created_at, expires_at
            """,
            idempotency_key,
            public_code,
            reporter_account_id,
            description,
            address,
            latitude,
            longitude,
            notification_radius_km,
            duration_hours,
            photo_storage_key,
            photo_derived_storage_key,
            photo_content_type,
        )
        if row is not None:
            return dict(row), True
        existing = await self._pool.fetchrow(
            """
            SELECT id, public_code, created_at, expires_at
            FROM disaster_service.help_requests
            WHERE idempotency_key = $1
            """,
            idempotency_key,
        )
        return dict(existing), False

    async def list_active_help_requests(
        self,
        limit: int,
        offset: int,
        account_id: UUID | None,
    ) -> tuple[list[dict], int]:
        rows = await self._pool.fetch(
            """
            SELECT
                hr.id,
                hr.description,
                hr.address,
                hr.latitude,
                hr.longitude,
                hr.notification_radius_km,
                hr.created_at,
                hr.expires_at,
                (SELECT COUNT(*)
                 FROM disaster_service.help_request_attenders a
                 WHERE a.help_request_id = hr.id) AS attenders_count,
                ($3::uuid IS NOT NULL AND EXISTS (
                    SELECT 1
                    FROM disaster_service.help_request_attenders a
                    WHERE a.help_request_id = hr.id
                      AND a.account_id = $3
                )) AS attended_by_me,
                hr.photo_derived_storage_key IS NOT NULL AS has_photo
            FROM disaster_service.help_requests hr
            WHERE hr.expires_at > NOW()
            ORDER BY hr.created_at DESC, hr.id DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
            account_id,
        )
        total = await self._pool.fetchval(
            """
            SELECT COUNT(*)
            FROM disaster_service.help_requests
            WHERE expires_at > NOW()
            """
        )
        return [dict(row) for row in rows], int(total)

    async def attend_help_request(
        self, request_id: UUID, account_id: UUID
    ) -> dict | None:
        """Registra la atención de forma idempotente (DEC-125-03)."""
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                active = await connection.fetchval(
                    """
                    SELECT 1
                    FROM disaster_service.help_requests
                    WHERE id = $1 AND expires_at > NOW()
                    """,
                    request_id,
                )
                if active is None:
                    return None
                await connection.execute(
                    """
                    INSERT INTO
                        disaster_service.help_request_attenders (
                            help_request_id, account_id
                        )
                    VALUES ($1, $2)
                    ON CONFLICT (help_request_id, account_id)
                    DO NOTHING
                    """,
                    request_id,
                    account_id,
                )
                count = await connection.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM disaster_service.help_request_attenders
                    WHERE help_request_id = $1
                    """,
                    request_id,
                )
        return {"id": request_id, "attenders_count": int(count)}

    async def get_help_request_photo(
        self, request_id: UUID
    ) -> dict | None:
        row = await self._pool.fetchrow(
            """
            SELECT photo_derived_storage_key AS object_key,
                   photo_content_type AS content_type
            FROM disaster_service.help_requests
            WHERE id = $1
              AND expires_at > NOW()
              AND photo_derived_storage_key IS NOT NULL
            """,
            request_id,
        )
        return None if row is None else dict(row)

    # CHG-036 — Consola de superadministración (bandeja unificada,
    # mutaciones con versión y auditoría append-only).

    async def admin_list_submissions(
        self,
        q: str | None,
        kind: str | None,
        status: str | None,
        received_from: datetime | None,
        received_to: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict], int]:
        clauses: list[str] = []
        values: list[object] = []
        if q is not None:
            values.append(q.strip())
            position = len(values)
            clauses.append(
                f"""lower(unaccent(
                    tracking_code || ' ' || title || ' ' ||
                    coalesce(location_label, '')
                )) LIKE '%' || lower(unaccent(${position})) || '%'"""
            )
        if kind is not None:
            values.append(kind)
            clauses.append(f"kind = ${len(values)}")
        if status is not None:
            values.append(status)
            clauses.append(f"admin_status = ${len(values)}")
        if received_from is not None:
            values.append(received_from)
            clauses.append(f"received_at >= ${len(values)}")
        if received_to is not None:
            values.append(received_to)
            clauses.append(f"received_at <= ${len(values)}")
        where_clause = (
            "WHERE " + " AND ".join(clauses) if clauses else ""
        )
        base = f"""
            WITH unified AS ({_ADMIN_UNIFIED_CTE}),
            classified AS (
                SELECT *, {_ADMIN_STATUS_EXPR} AS admin_status
                FROM unified
            )
        """
        total = await self._pool.fetchval(
            f"{base} SELECT COUNT(*) FROM classified {where_clause}",
            *values,
        )
        limit_parameter = len(values) + 1
        offset_parameter = len(values) + 2
        rows = await self._pool.fetch(
            f"""
            {base}
            SELECT * FROM classified
            {where_clause}
            ORDER BY (account_id IS NOT NULL) DESC,
                     received_at DESC, id DESC
            LIMIT ${limit_parameter} OFFSET ${offset_parameter}
            """,
            *values,
            limit,
            offset,
        )
        # CHG-054: los envíos hechos con cuenta van primero en la
        # bandeja (prioridad de revisión sobre los anónimos).
        return [dict(row) for row in rows], int(total)

    async def admin_submissions_overview(self) -> dict:
        base = f"""
            WITH unified AS ({_ADMIN_UNIFIED_CTE}),
            classified AS (
                SELECT *, {_ADMIN_STATUS_EXPR} AS admin_status
                FROM unified
            )
        """
        counts = await self._pool.fetch(
            f"""
            {base}
            SELECT admin_status, kind, COUNT(*)::int AS quantity,
                   MIN(received_at) AS oldest
            FROM classified
            GROUP BY admin_status, kind
            """
        )
        accepted_today = await self._pool.fetchval(
            """
            SELECT COUNT(*)
            FROM administration.audit_events
            WHERE action = 'submission_accepted'
              AND result = 'success'
              AND occurred_at >= date_trunc('day', NOW())
            """
        )
        recent = await self._pool.fetch(
            """
            SELECT id, action, resource_kind, occurred_at, result
            FROM administration.audit_events
            ORDER BY occurred_at DESC, id DESC
            LIMIT 10
            """
        )
        return {
            "counts": [dict(row) for row in counts],
            "accepted_today": int(accepted_today),
            "recent_activity": [dict(row) for row in recent],
        }

    async def admin_get_submission_summary(
        self, submission_id: UUID
    ) -> dict | None:
        row = await self._pool.fetchrow(
            f"""
            WITH unified AS ({_ADMIN_UNIFIED_CTE}),
            classified AS (
                SELECT *, {_ADMIN_STATUS_EXPR} AS admin_status
                FROM unified
            )
            SELECT * FROM classified WHERE id = $1
            """,
            submission_id,
        )
        return dict(row) if row is not None else None

    async def admin_get_submission(
        self, submission_id: UUID
    ) -> tuple[str, dict, list[dict]] | None:
        for kind, meta in _ADMIN_TABLES.items():
            row = await self._pool.fetchrow(
                meta["detail_sql"], submission_id
            )
            if row is None:
                continue
            if meta["photo_table"] is None:
                # CHG-044: las ofertas no llevan evidencia adjunta.
                return kind, dict(row), []
            evidence = await self._pool.fetch(
                f"""
                SELECT id, content_type, size_bytes, malware_scan,
                       created_at, {meta['derived_column']}
                       AS derived_key
                FROM {meta['photo_table']}
                WHERE {meta['photo_fk']} = $1
                ORDER BY position
                """,
                submission_id,
            )
            return kind, dict(row), [dict(item) for item in evidence]
        return None

    async def _admin_audit(
        self,
        connection,
        actor_account_id: UUID,
        actor_display_name: str,
        action: str,
        resource_kind: str,
        resource_id: UUID | None,
        result: str,
        reason_encrypted: bytes | None,
        changed_fields: list[str],
        correlation_id: UUID | None,
    ) -> UUID:
        return await connection.fetchval(
            """
            INSERT INTO administration.audit_events (
                actor_account_id, actor_display_name, action,
                resource_kind, resource_id, result, reason_protected,
                changed_fields, request_correlation_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
            """,
            actor_account_id,
            actor_display_name,
            action,
            resource_kind,
            resource_id,
            result,
            reason_encrypted,
            changed_fields,
            correlation_id,
        )

    async def admin_write_audit(
        self,
        actor_account_id: UUID,
        actor_display_name: str,
        action: str,
        resource_kind: str,
        resource_id: UUID | None,
        result: str,
        reason_encrypted: bytes | None = None,
        changed_fields: list[str] | None = None,
        correlation_id: UUID | None = None,
    ) -> UUID:
        async with self._pool.acquire() as connection:
            return await self._admin_audit(
                connection,
                actor_account_id,
                actor_display_name,
                action,
                resource_kind,
                resource_id,
                result,
                reason_encrypted,
                changed_fields or [],
                correlation_id,
            )

    async def admin_mutate_submission(
        self,
        kind: str,
        submission_id: UUID,
        expected_version: int,
        action: str,
        actor_account_id: UUID,
        actor_display_name: str,
        reason_encrypted: bytes | None,
        columns: dict[str, object] | None = None,
        correlation_id: UUID | None = None,
    ) -> tuple[str, UUID | None, int | None]:
        """Aplica una mutación administrativa en UNA transacción.

        `action`: edit | accept | reject | request_changes | archive |
        restore. Devuelve (outcome, audit_event_id, nueva_version) con
        outcome en {'ok', 'conflict', 'not_found'}. La verificación de
        `expectedVersion` en el UPDATE garantiza el 409 ante carreras.
        """
        meta = _ADMIN_TABLES[kind]
        table = meta["table"]
        sets: list[str] = [
            "version = version + 1",
            "updated_at = NOW()",
        ]
        values: list[object] = []
        changed_fields: list[str] = []

        def add(expression: str, *expression_values: object) -> None:
            placeholders = [
                f"${len(values) + index + 1}"
                for index in range(len(expression_values))
            ]
            sets.append(expression.format(*placeholders))
            values.extend(expression_values)

        if action == "edit":
            for column, value in (columns or {}).items():
                add(f"{column} = {{0}}", value)
                changed_fields.append(column)
        elif action == "accept":
            add(f"{meta['status_column']} = '{meta['accepted_value']}'")
            sets.append("needs_information = FALSE")
            if meta["decided_columns"]:
                sets.append(meta["decided_columns"])
        elif action == "reject":
            add(f"{meta['status_column']} = '{meta['rejected_value']}'")
            sets.append("needs_information = FALSE")
            if meta["decided_columns"]:
                sets.append(meta["decided_columns"])
        elif action == "request_changes":
            sets.append("needs_information = TRUE")
        elif action == "archive":
            add("archived_at = NOW(), archived_by = {0}", actor_account_id)
        elif action == "restore":
            sets.append("archived_at = NULL, archived_by = NULL")
        else:  # pragma: no cover - protegido por el contrato
            raise ValueError(f"Acción administrativa desconocida: {action}")

        values.append(submission_id)
        id_parameter = len(values)
        values.append(expected_version)
        version_parameter = len(values)

        async with self._pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    f"""
                    UPDATE {table}
                    SET {', '.join(sets)}
                    WHERE id = ${id_parameter}
                      AND version = ${version_parameter}
                    RETURNING id, version
                    """,
                    *values,
                )
                if row is None:
                    exists = await connection.fetchval(
                        f"SELECT 1 FROM {table} WHERE id = $1",
                        submission_id,
                    )
                    outcome = "conflict" if exists else "not_found"
                    if outcome == "conflict":
                        # Auditoría mínima del intento fallido, sin
                        # payload sensible.
                        await self._admin_audit(
                            connection,
                            actor_account_id,
                            actor_display_name,
                            f"submission_{action}",
                            kind,
                            submission_id,
                            "failed",
                            None,
                            [],
                            correlation_id,
                        )
                    return outcome, None, None

                # Efectos de dominio tras aceptar/rechazar/restaurar:
                # misma transacción que el cambio de estado.
                # CHG-077: archivar/restaurar también cuenta — las
                # novedades archivadas no suman al umbral comunitario.
                if action in (
                    "accept", "reject", "archive", "restore"
                ) and kind == "person_status_report":
                    await connection.execute(
                        _PERSON_PROJECTION_RECOMPUTE_SQL,
                        submission_id,
                    )
                    # CHG-084: el estado humano acompaña al caso.
                    await connection.execute(
                        _PEOPLE_STATUS_BY_NOVELTY_SQL,
                        submission_id,
                    )
                if action in ("accept", "reject") and kind == (
                    "aid_location_rating"
                ):
                    await connection.execute(
                        _RATING_AGGREGATE_RECOMPUTE_SQL,
                        submission_id,
                    )
                # CHG-075: el reporte de persona nace publicado.
                # Rechazar lo retira (`rejected`), borrar/archivar lo
                # oculta (`withdrawn`), aceptar o restaurar lo vuelve
                # a publicar; editar propaga los campos compartidos.
                if kind == "missing_person_report":
                    if action == "edit":
                        await connection.execute(
                            _CASE_EDIT_SYNC_SQL, submission_id
                        )
                    elif action in _CASE_PUBLICATION_BY_ACTION:
                        await connection.execute(
                            _CASE_PUBLICATION_SYNC_SQL,
                            submission_id,
                            _CASE_PUBLICATION_BY_ACTION[action],
                        )
                        # CHG-081: el punto del mapa acompaña la
                        # publicación del caso.
                        if action in ("reject", "archive"):
                            await connection.execute(
                                _PERSON_MAP_POINT_HIDE_SQL,
                                submission_id,
                            )
                            # CHG-084: la fila humana también se
                            # retira (la proyección cae en cascada).
                            await connection.execute(
                                _PEOPLE_HIDE_SQL,
                                submission_id,
                            )
                        else:  # accept / restore
                            point_id = await connection.fetchval(
                                _PERSON_MAP_POINT_RESTORE_SQL,
                                submission_id,
                                self.MISSING_PERSON_SOURCE_ID,
                            )
                            if point_id is not None:
                                await connection.execute(
                                    _PERSON_MAP_POINT_LINK_SQL,
                                    submission_id,
                                    point_id,
                                )
                            # CHG-084: recrear la fila humana y su
                            # proyección si quedó sin ellas.
                            restored = await connection.fetchrow(
                                _PEOPLE_RESTORE_SQL,
                                submission_id,
                                self.MISSING_PERSON_SOURCE_ID,
                            )
                            if (
                                restored is not None
                                and restored["latitude"] is not None
                                and restored["longitude"] is not None
                            ):
                                await connection.execute(
                                    _PEOPLE_PROJECTION_INSERT_SQL,
                                    restored["id"],
                                    restored["longitude"],
                                    restored["latitude"],
                                )
                # unverified_building_report aceptado NO proyecta el
                # mapa: DEC-012–DEC-014 pendientes (ver CHG-035).
                # CHG-044: rechazar o archivar una oferta oculta su
                # proyección en la misma transacción; la aceptación
                # está bloqueada aguas arriba (DEC-020/DEC-021).
                if action in ("reject", "archive") and kind in (
                    "community_meal_offer",
                    "temporary_shelter_offer",
                ):
                    await connection.execute(
                        _OFFER_PUBLICATION_HIDE_SQL,
                        submission_id,
                    )

                audit_event_id = await self._admin_audit(
                    connection,
                    actor_account_id,
                    actor_display_name,
                    ("submission_accepted" if action == "accept"
                     else f"submission_{action}"),
                    kind,
                    submission_id,
                    "success",
                    reason_encrypted,
                    changed_fields,
                    correlation_id,
                )
        return "ok", audit_event_id, int(row["version"])

    async def admin_get_evidence(
        self, submission_id: UUID, evidence_id: UUID
    ) -> dict | None:
        for kind, meta in _ADMIN_TABLES.items():
            if meta["photo_table"] is None:
                continue
            row = await self._pool.fetchrow(
                f"""
                SELECT id, content_type, size_bytes, malware_scan,
                       created_at, {meta['derived_column']}
                       AS derived_key
                FROM {meta['photo_table']}
                WHERE id = $1 AND {meta['photo_fk']} = $2
                """,
                evidence_id,
                submission_id,
            )
            if row is not None:
                return {**dict(row), "kind": kind}
        return None

    async def admin_list_audit_events(
        self,
        q: str | None,
        action: str | None,
        result: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict], int]:
        clauses: list[str] = []
        values: list[object] = []
        if q is not None:
            values.append(q.strip())
            position = len(values)
            clauses.append(
                f"""lower(unaccent(
                    action || ' ' || resource_kind || ' ' ||
                    actor_display_name
                )) LIKE '%' || lower(unaccent(${position})) || '%'"""
            )
        if action is not None:
            values.append(action)
            clauses.append(f"action = ${len(values)}")
        if result is not None:
            values.append(result)
            clauses.append(f"result = ${len(values)}")
        where_clause = (
            "WHERE " + " AND ".join(clauses) if clauses else ""
        )
        total = await self._pool.fetchval(
            f"""
            SELECT COUNT(*)
            FROM administration.audit_events
            {where_clause}
            """,
            *values,
        )
        limit_parameter = len(values) + 1
        offset_parameter = len(values) + 2
        rows = await self._pool.fetch(
            f"""
            SELECT id, occurred_at, actor_account_id,
                   actor_display_name, action, resource_kind,
                   resource_id, result, reason_protected
            FROM administration.audit_events
            {where_clause}
            ORDER BY occurred_at DESC, id DESC
            LIMIT ${limit_parameter} OFFSET ${offset_parameter}
            """,
            *values,
            limit,
            offset,
        )
        return [dict(row) for row in rows], int(total)


# CHG-044 — Helpers de ofertas comunitarias.

# Estado de moderación proyectado hacia el propietario y la consola:
# archivo lógico y solicitud de información se superponen al dominio.
_OFFER_MODERATION_EXPR = """
    CASE
        WHEN o.archived_at IS NOT NULL THEN 'archived'
        WHEN o.needs_information THEN 'needs_information'
        ELSE o.moderation_status::text
    END
"""

# Columnas del resumen de propietario; el título viaja cifrado y lo
# descifra la capa HTTP con la clave exclusiva de ofertas.
_OFFER_OWNER_COLUMNS = f"""
    o.id, o.tracking_code, o.kind::text AS kind,
    o.title_encrypted,
    ({_OFFER_MODERATION_EXPR}) AS moderation_status,
    o.availability_status::text AS availability_status,
    COALESCE(m.servings_available, t.spaces_available, 0)
        AS available_units,
    CASE o.kind::text
        WHEN 'community_meal' THEN 'servings'
        ELSE 'spaces'
    END AS capacity_unit,
    o.available_from, o.available_until, o.received_at,
    o.updated_at, o.version
"""


def _offer_admin_kind(kind: str) -> str:
    return (
        "community_meal_offer"
        if kind == "community_meal"
        else "temporary_shelter_offer"
    )


def _aid_offer_receipt(row) -> AidOfferReceipt:
    # Constancia estable al reintento: siempre informa la recepción
    # original en revisión, aunque la moderación ya haya decidido.
    return AidOfferReceipt(
        id=row["id"],
        tracking_code=row["tracking_code"],
        kind=row["kind"],
        moderation_status="under_review",
        availability_status="scheduled",
        received_at=row["received_at"],
        version=row["version"],
    )


# Rechazar o archivar una oferta oculta su proyección pública en la
# MISMA transacción (si existiera; la aceptación sigue bloqueada).
_OFFER_PUBLICATION_HIDE_SQL = """
    UPDATE disaster_service.aid_offer_publications
    SET publication_status = 'withdrawn',
        updated_at = NOW()
    WHERE offer_id = $1
"""


# CHG-036 — Bandeja unificada: una fila por expediente, sin PII en el
# resumen (títulos genéricos, códigos públicos y zona municipal).
_ADMIN_UNIFIED_CTE = """
    SELECT
        r.id,
        'missing_person_report' AS kind,
        r.public_case_code AS tracking_code,
        'Reporte de persona desaparecida' AS title,
        r.municipality || ', ' || r.department AS location_label,
        CASE
            WHEN r.reporter_account_id IS NOT NULL
                THEN 'Reporte con cuenta'
            ELSE 'Reporte anónimo'
        END AS source_label,
        r.status::text AS domain_status,
        r.needs_information,
        r.archived_at,
        r.received_at,
        r.updated_at,
        r.version,
        (SELECT COUNT(*)
         FROM disaster_service.missing_person_report_photos p
         WHERE p.report_id = r.id)::int AS evidence_count,
        r.reporter_account_id AS account_id
    FROM disaster_service.missing_person_reports r
    UNION ALL
    SELECT
        b.id,
        'unverified_building_report',
        b.public_tracking_code,
        left('Edificio sin verificar — ' || b.building_reference, 200),
        b.municipality || ', ' || b.department,
        CASE
            WHEN b.actor_account_id IS NOT NULL
                THEN 'Reporte con cuenta'
            ELSE 'Reporte anónimo'
        END,
        b.moderation_status::text,
        b.needs_information,
        b.archived_at,
        b.created_at,
        b.updated_at,
        b.version,
        (SELECT COUNT(*)
         FROM disaster_service.unverified_building_report_files f
         WHERE f.report_id = b.id)::int,
        b.actor_account_id
    FROM disaster_service.unverified_building_reports b
    UNION ALL
    SELECT
        s.id,
        'person_status_report',
        'NOV-' || upper(left(s.id::text, 8)),
        CASE s.claimed_outcome::text
            WHEN 'found' THEN 'Novedad de persona — reportada con vida'
            ELSE 'Novedad de persona — reportada fallecida'
        END,
        NULL,
        CASE s.actor_kind::text
            WHEN 'authenticated' THEN 'Aporte con cuenta'
            ELSE 'Aporte anónimo'
        END,
        s.moderation_status::text,
        s.needs_information,
        s.archived_at,
        s.received_at,
        s.updated_at,
        s.version,
        (SELECT COUNT(*)
         FROM disaster_service.person_status_report_photos p
         WHERE p.report_id = s.id)::int,
        s.account_id
    FROM disaster_service.person_status_reports s
    UNION ALL
    SELECT
        t.id,
        'aid_location_rating',
        'VAL-' || upper(left(t.id::text, 8)),
        left('Valoración ' || t.rating || '/5 — ' || al.name, 200),
        al.municipality || ', ' || al.department,
        CASE t.actor_kind::text
            WHEN 'authenticated' THEN 'Aporte con cuenta'
            ELSE 'Aporte anónimo'
        END,
        t.moderation_status::text,
        t.needs_information,
        t.archived_at,
        t.received_at,
        t.updated_at,
        t.version,
        (SELECT COUNT(*)
         FROM disaster_service.aid_location_rating_photos p
         WHERE p.rating_id = t.id)::int,
        t.account_id
    FROM disaster_service.aid_location_ratings t
    INNER JOIN disaster_service.aid_locations al
        ON al.id = t.location_id
    UNION ALL
    SELECT
        o.id,
        CASE o.kind::text
            WHEN 'community_meal' THEN 'community_meal_offer'
            ELSE 'temporary_shelter_offer'
        END,
        o.tracking_code,
        CASE o.kind::text
            WHEN 'community_meal' THEN 'Oferta de comida comunitaria'
            ELSE 'Oferta de alojamiento temporal'
        END,
        o.municipality || ', ' || o.department,
        'Oferta con cuenta',
        o.moderation_status::text,
        o.needs_information,
        o.archived_at,
        o.received_at,
        o.updated_at,
        o.version,
        0,
        o.account_id
    FROM disaster_service.aid_offers o
"""

# Proyección del estado del dominio al estado administrativo unificado
# (espejo SQL de admin.unified_status).
_ADMIN_STATUS_EXPR = """
    CASE
        WHEN archived_at IS NOT NULL OR domain_status = 'withdrawn'
            THEN 'archived'
        WHEN needs_information THEN 'needs_information'
        WHEN domain_status IN ('verified', 'accepted') THEN 'accepted'
        WHEN domain_status = 'rejected' THEN 'rejected'
        ELSE 'under_review'
    END
"""

# CHG-077 — Estado público de la persona, por prioridad:
# 1) la última novedad ACEPTADA por el super admin manda;
# 2) la última novedad no rechazada del SECTOR SALUD aplica de
#    inmediato el desenlace que declara;
# 3) umbral comunitario: >= 5 novedades no rechazadas con el mismo
#    desenlace (más reportes gana; empate: la más reciente);
# 4) sin nada de lo anterior: `missing`.
_PERSON_PUBLIC_STATUS_EXPRESSION = """
    COALESCE(
        (
            SELECT r.claimed_outcome
            FROM disaster_service.person_status_reports r
            WHERE r.person_id = mc.id
              AND r.moderation_status = 'accepted'
              AND r.archived_at IS NULL
            ORDER BY r.decided_at DESC NULLS LAST,
                     r.received_at DESC
            LIMIT 1
        ),
        (
            -- CHG-122: `deceased` es definitivo. Con novedades de
            -- salud contradictorias ganaba la más reciente y una
            -- "encontrada" posterior pisaba el fallecimiento; ahora
            -- `deceased` gana aunque no sea la última. Revertirlo
            -- exige invalidar el registro (rechazar/archivar la
            -- novedad), nunca sobrescribirlo con otra novedad.
            SELECT r.claimed_outcome
            FROM disaster_service.person_status_reports r
            WHERE r.person_id = mc.id
              AND r.moderation_status NOT IN ('rejected', 'withdrawn')
              AND r.archived_at IS NULL
              AND r.reporter_health_sector
            ORDER BY (r.claimed_outcome = 'deceased') DESC,
                     r.received_at DESC
            LIMIT 1
        ),
        (
            -- CHG-107: el umbral cuenta CUENTAS DISTINTAS, no reportes.
            -- Antes era COUNT(*), así que un mismo actor enviando cinco
            -- novedades cambiaba el estado público de una persona
            -- desaparecida; con reportes anónimos, sin coste alguno.
            -- Ahora solo suman aportes con cuenta identificable y cada
            -- cuenta cuenta una vez: reunir cinco exige cinco correos
            -- verificados distintos. Los anónimos se siguen guardando y
            -- se revisan, pero no mueven el estado por sí solos.
            SELECT r.claimed_outcome
            FROM disaster_service.person_status_reports r
            WHERE r.person_id = mc.id
              AND r.moderation_status NOT IN ('rejected', 'withdrawn')
              AND r.archived_at IS NULL
              AND r.account_id IS NOT NULL
            GROUP BY r.claimed_outcome
            HAVING COUNT(DISTINCT r.account_id) >= 5
            -- CHG-122: si ambos desenlaces alcanzan el umbral,
            -- `deceased` es definitivo y gana sobre los votos.
            ORDER BY (r.claimed_outcome = 'deceased') DESC,
                     COUNT(DISTINCT r.account_id) DESC,
                     MAX(r.received_at) DESC
            LIMIT 1
        ),
        'missing'
    )
"""

# CHG-120 — La misma condición que la prioridad 2 del estado público:
# una novedad del sector salud no rechazada/retirada y sin archivar.
# Mientras exista, el caso no recibe novedades de otros actores; si el
# super admin la rechaza o archiva, el bloqueo cae en el mismo acto.
_PERSON_HAS_EFFECTIVE_HEALTH_REPORT_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM disaster_service.person_status_reports r
        WHERE r.person_id = $1
          AND r.reporter_health_sector
          AND r.moderation_status NOT IN ('rejected', 'withdrawn')
          AND r.archived_at IS NULL
    )
"""

# CHG-122 — Estado público del caso solo si está publicado (para el
# guardián de transición: `deceased` no se sobrescribe con `found`).
_PERSON_PUBLIC_STATUS_IF_PUBLISHED_SQL = """
    SELECT public_status
    FROM disaster_service.missing_person_cases
    WHERE id = $1 AND publication_status = 'published'
"""

# Variante por persona (creación de novedades y decisiones directas).
_PERSON_PUBLIC_STATUS_BY_PERSON_SQL = f"""
    UPDATE disaster_service.missing_person_cases mc
    SET public_status = {_PERSON_PUBLIC_STATUS_EXPRESSION},
        updated_at = NOW()
    WHERE mc.id = $1
"""

# Variante por novedad (consola administrativa unificada).
_PERSON_PROJECTION_RECOMPUTE_SQL = f"""
    UPDATE disaster_service.missing_person_cases mc
    SET public_status = {_PERSON_PUBLIC_STATUS_EXPRESSION},
        updated_at = NOW()
    WHERE mc.id = (
        SELECT person_id
        FROM disaster_service.person_status_reports
        WHERE id = $1
    )
"""

# Misma regla que decide_aid_location_rating: solo aceptadas suman.
_RATING_AGGREGATE_RECOMPUTE_SQL = """
    UPDATE disaster_service.aid_locations al
    SET average_rating = sub.avg_rating,
        ratings_count = sub.quantity,
        updated_at = NOW()
    FROM (
        SELECT
            ROUND(AVG(r.rating)::numeric, 2) AS avg_rating,
            COUNT(*)::int AS quantity
        FROM disaster_service.aid_location_ratings r
        WHERE r.location_id = (
            SELECT location_id
            FROM disaster_service.aid_location_ratings
            WHERE id = $1
        )
          AND r.moderation_status = 'accepted'
    ) sub
    WHERE al.id = (
        SELECT location_id
        FROM disaster_service.aid_location_ratings
        WHERE id = $1
    )
"""

# CHG-075 — El caso público nace publicado con el reporte; las
# decisiones del admin sobre el expediente privado se reflejan en la
# proyección pública dentro de la misma transacción.
_CASE_PUBLICATION_SYNC_SQL = """
    UPDATE disaster_service.missing_person_cases mc
    SET publication_status =
            $2::disaster_service.case_publication_status,
        updated_at = NOW()
    FROM disaster_service.missing_person_reports r
    WHERE r.id = $1
      AND mc.public_case_code = r.public_case_code
"""

# CHG-075 — Editar el expediente propaga los campos publicados que
# comparten reporte y caso.
_CASE_EDIT_SYNC_SQL = """
    UPDATE disaster_service.missing_person_cases mc
    SET department = r.department,
        municipality = r.municipality,
        last_seen_area = r.last_seen_area,
        clothing_description = r.clothing_description,
        updated_at = NOW()
    FROM disaster_service.missing_person_reports r
    WHERE r.id = $1
      AND mc.public_case_code = r.public_case_code
"""

_CASE_PUBLICATION_BY_ACTION = {
    "accept": "published",
    "restore": "published",
    "reject": "rejected",
    "archive": "withdrawn",
}

# CHG-105 — Ruta pública de la foto de un caso. Se construye a partir
# del identificador del caso, que ya es público: la clave del objeto
# nunca sale al cliente porque contiene el id del expediente privado.
def public_photo_url_for(case_id, object_key: str | None) -> str | None:
    if not object_key:
        return None
    return f"/api/v1/public/missing-persons/{case_id}/photo"


# CHG-105 — La foto que se publica con el caso. Se prefiere la que el
# reportante marcó como rostro reciente (CHG-094); si no declaró
# categorías, la primera que envió. Siempre el objeto DERIVADO, que va
# sin metadatos EXIF: el original queda en cuarentena y jamás se sirve.
def select_public_photo_key(photos: list["StoredPhoto"]) -> str | None:
    if not photos:
        return None

    preferidas = [p for p in photos if p.category == "recent_face"]
    elegida = preferidas[0] if preferidas else min(
        photos, key=lambda p: p.position
    )
    return elegida.derived_storage_key


# CHG-084 — Proyección humana del caso: fila en people (cifras y
# tabla) y punto 'approximate' en people_map_projection (DEC-007
# prohíbe 'exact').
_PEOPLE_PROJECTION_INSERT_SQL = """
    INSERT INTO disaster_service.people_map_projection (
        person_id, location, coordinate_precision,
        verification_status, visibility, data_classification,
        updated_at
    ) VALUES (
        $1,
        ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography,
        'approximate', 'unverified', 'published', 'operational', NOW()
    )
    ON CONFLICT (person_id) DO NOTHING
"""

# CHG-084 — El estado público del caso (missing/found/deceased) se
# refleja en people.status: found→confirmed_alive (la comunidad o el
# equipo la dieron por encontrada) y deceased→reported_deceased (la
# plataforma no certifica fallecimientos oficialmente).
_PEOPLE_STATUS_CASE_EXPRESSION = """
    (CASE
        WHEN mc.public_status::text = 'found' THEN 'confirmed_alive'
        -- CHG-124: el sector salud tiene potestad para certificar el
        -- deceso; con una novedad efectiva suya declarándolo, la
        -- persona pasa a muerte CONFIRMADA. Sin ese respaldo queda en
        -- reportada (la plataforma no certifica por sí sola). La
        -- condición es la misma de CHG-120/122: si la consola
        -- invalida esa novedad, la confirmación cae con ella.
        WHEN mc.public_status::text = 'deceased' THEN
            CASE WHEN EXISTS (
                SELECT 1
                FROM disaster_service.person_status_reports hr
                WHERE hr.person_id = mc.id
                  AND hr.reporter_health_sector
                  AND hr.claimed_outcome = 'deceased'
                  AND hr.moderation_status NOT IN ('rejected', 'withdrawn')
                  AND hr.archived_at IS NULL
            ) THEN 'confirmed_deceased'
            ELSE 'reported_deceased' END
        ELSE 'missing'
    END)::disaster_service.human_status
"""

_PEOPLE_STATUS_BY_PERSON_SQL = f"""
    UPDATE disaster_service.people p
    SET status = {_PEOPLE_STATUS_CASE_EXPRESSION}
    FROM disaster_service.missing_person_cases mc
    WHERE mc.id = $1
      AND p.missing_person_case_id = mc.id
"""

_PEOPLE_STATUS_BY_NOVELTY_SQL = f"""
    UPDATE disaster_service.people p
    SET status = {_PEOPLE_STATUS_CASE_EXPRESSION}
    FROM disaster_service.missing_person_cases mc
    WHERE mc.id = (
        SELECT person_id
        FROM disaster_service.person_status_reports
        WHERE id = $1
    )
      AND p.missing_person_case_id = mc.id
"""

# CHG-084 — Retirar el caso borra su fila humana (la proyección cae
# en cascada); republicar la recrea desde el caso y el expediente.
_PEOPLE_HIDE_SQL = """
    DELETE FROM disaster_service.people p
    USING disaster_service.missing_person_cases mc,
          disaster_service.missing_person_reports r
    WHERE r.id = $1
      AND mc.public_case_code = r.public_case_code
      AND p.missing_person_case_id = mc.id
"""

_PEOPLE_RESTORE_SQL = f"""
    INSERT INTO disaster_service.people (
        source_id, display_name, status, location, related_event,
        latitude, longitude, missing_person_case_id
    )
    SELECT $2, mc.display_name, {_PEOPLE_STATUS_CASE_EXPRESSION},
           mc.municipality || ', ' || mc.department,
           'Reporte ciudadano de persona desaparecida',
           r.last_seen_latitude, r.last_seen_longitude, mc.id
    FROM disaster_service.missing_person_cases mc
    INNER JOIN disaster_service.missing_person_reports r
        ON mc.public_case_code = r.public_case_code
    WHERE r.id = $1
      AND NOT EXISTS (
          SELECT 1 FROM disaster_service.people p
          WHERE p.missing_person_case_id = mc.id
      )
    RETURNING id, latitude, longitude
"""

# CHG-081 — Retirar el caso también retira su punto del mapa: se
# desvincula y se borra en la misma sentencia (el chequeo de la FK
# ocurre al cierre de la sentencia).
_PERSON_MAP_POINT_HIDE_SQL = """
    WITH target AS (
        SELECT mc.id AS case_id, mc.map_point_id AS point_id
        FROM disaster_service.missing_person_cases mc
        INNER JOIN disaster_service.missing_person_reports r
            ON mc.public_case_code = r.public_case_code
        WHERE r.id = $1
          AND mc.map_point_id IS NOT NULL
    ), unlink AS (
        UPDATE disaster_service.missing_person_cases mc
        SET map_point_id = NULL, updated_at = NOW()
        FROM target
        WHERE mc.id = target.case_id
    )
    DELETE FROM disaster_service.operational_map_points p
    USING target
    WHERE p.id = target.point_id
"""

# CHG-081 — Republicar el caso recrea su punto si el reporte tiene
# coordenadas y el caso quedó sin punto (devuelve el id creado o None).
_PERSON_MAP_POINT_RESTORE_SQL = """
    INSERT INTO disaster_service.operational_map_points (
        category, title, description, location_label, location,
        coordinate_precision, verification_status, source_id,
        data_classification, updated_at
    )
    SELECT 'missing_person', mc.display_name,
           'Vista por última vez en ' || mc.last_seen_area,
           mc.municipality || ', ' || mc.department,
           ST_SetSRID(
               ST_MakePoint(
                   r.last_seen_longitude, r.last_seen_latitude
               ),
               4326
           )::geography,
           'exact', 'unverified', $2, 'operational', NOW()
    FROM disaster_service.missing_person_cases mc
    INNER JOIN disaster_service.missing_person_reports r
        ON mc.public_case_code = r.public_case_code
    WHERE r.id = $1
      AND mc.map_point_id IS NULL
      AND r.last_seen_latitude IS NOT NULL
      AND r.last_seen_longitude IS NOT NULL
    RETURNING id
"""

_PERSON_MAP_POINT_LINK_SQL = """
    UPDATE disaster_service.missing_person_cases mc
    SET map_point_id = $2, updated_at = NOW()
    FROM disaster_service.missing_person_reports r
    WHERE r.id = $1
      AND mc.public_case_code = r.public_case_code
"""

# Metadatos por tipo: tabla, columna de estado, valores de decisión,
# tabla de evidencia y consulta de detalle.
_ADMIN_TABLES: dict[str, dict[str, str | None]] = {
    "missing_person_report": {
        "table": "disaster_service.missing_person_reports",
        "status_column": "status",
        "accepted_value": "verified",
        "rejected_value": "rejected",
        "decided_columns": "",
        "photo_table": "disaster_service.missing_person_report_photos",
        "photo_fk": "report_id",
        "derived_column": "derived_storage_key",
        "detail_sql": (
            "SELECT * FROM disaster_service.missing_person_reports "
            "WHERE id = $1"
        ),
    },
    "unverified_building_report": {
        "table": "disaster_service.unverified_building_reports",
        "status_column": "moderation_status",
        "accepted_value": "accepted",
        "rejected_value": "rejected",
        "decided_columns": (
            "moderated_at = NOW(), moderated_by = 'super_admin'"
        ),
        "photo_table": (
            "disaster_service.unverified_building_report_files"
        ),
        "photo_fk": "report_id",
        "derived_column": "derived_object_key",
        "detail_sql": (
            "SELECT * FROM disaster_service.unverified_building_reports "
            "WHERE id = $1"
        ),
    },
    "person_status_report": {
        "table": "disaster_service.person_status_reports",
        "status_column": "moderation_status",
        "accepted_value": "accepted",
        "rejected_value": "rejected",
        "decided_columns": (
            "decided_at = NOW(), decided_by_role = 'super_admin'"
        ),
        "photo_table": (
            "disaster_service.person_status_report_photos"
        ),
        "photo_fk": "report_id",
        "derived_column": "derived_storage_key",
        "detail_sql": (
            "SELECT * FROM disaster_service.person_status_reports "
            "WHERE id = $1"
        ),
    },
    "aid_location_rating": {
        "table": "disaster_service.aid_location_ratings",
        "status_column": "moderation_status",
        "accepted_value": "accepted",
        "rejected_value": "rejected",
        "decided_columns": (
            "decided_at = NOW(), decided_by_role = 'super_admin'"
        ),
        "photo_table": (
            "disaster_service.aid_location_rating_photos"
        ),
        "photo_fk": "rating_id",
        "derived_column": "derived_storage_key",
        "detail_sql": (
            "SELECT t.*, al.name AS location_name "
            "FROM disaster_service.aid_location_ratings t "
            "INNER JOIN disaster_service.aid_locations al "
            "ON al.id = t.location_id WHERE t.id = $1"
        ),
    },
    # CHG-044 — Las ofertas no tienen evidencia fotográfica; ambas
    # comparten tabla y se distinguen por `kind` en el detalle.
    "community_meal_offer": {
        "table": "disaster_service.aid_offers",
        "status_column": "moderation_status",
        "accepted_value": "accepted",
        "rejected_value": "rejected",
        "decided_columns": "decided_at = NOW()",
        "photo_table": None,
        "photo_fk": None,
        "derived_column": None,
        "detail_sql": (
            "SELECT o.*, d.servings_available, d.distribution_mode, "
            "d.meal_description_encrypted, "
            "d.allergen_information_encrypted, d.food_safety_confirmed "
            "FROM disaster_service.aid_offers o "
            "INNER JOIN disaster_service.community_meal_offer_details d "
            "ON d.offer_id = o.id "
            "WHERE o.id = $1 AND o.kind = 'community_meal'"
        ),
    },
    "temporary_shelter_offer": {
        "table": "disaster_service.aid_offers",
        "status_column": "moderation_status",
        "accepted_value": "accepted",
        "rejected_value": "rejected",
        "decided_columns": "decided_at = NOW()",
        "photo_table": None,
        "photo_fk": None,
        "derived_column": None,
        "detail_sql": (
            "SELECT o.*, d.spaces_available, d.shared_space, "
            "d.accepts_pets, d.accessibility_notes_encrypted, "
            "d.shelter_safety_confirmed "
            "FROM disaster_service.aid_offers o "
            "INNER JOIN "
            "disaster_service.temporary_shelter_offer_details d "
            "ON d.offer_id = o.id "
            "WHERE o.id = $1 AND o.kind = 'temporary_shelter'"
        ),
    },
}


def _building_receipt(row) -> UnverifiedBuildingReportReceipt:
    # Respuesta estable al reintento: la constancia siempre informa
    # `under_review`, aunque la moderación ya haya decidido.
    return UnverifiedBuildingReportReceipt(
        id=row["id"],
        public_tracking_code=row["public_tracking_code"],
        status="under_review",
        received_at=row["created_at"],
    )


def _contribution_receipt(row) -> CommunityContributionReceipt:
    # Respuesta estable al reintento (contrato): la constancia siempre
    # informa `under_review`, aunque la moderación ya haya decidido.
    return CommunityContributionReceipt(
        id=row["id"],
        status="under_review",
        actor_kind=row["actor_kind"],
        received_at=row["received_at"],
    )
