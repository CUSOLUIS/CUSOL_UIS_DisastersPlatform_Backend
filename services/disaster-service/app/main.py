import base64
import hashlib
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

import asyncpg
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field, TypeAdapter, ValidationError

from . import admin as admin_rules
from . import notifications
from . import offers as offer_rules
from .config import Settings
from .models import (
    AidOfferKind,
    AidOfferModerationStatus,
    AidOfferOwnerPage,
    AidOfferOwnerSummary,
    AidOfferOwnerUpdateInput,
    AidOfferReceipt,
    CommunityMealOfferInput,
    TemporaryShelterOfferInput,
    AdminAuditEvent,
    AdminAuditPage,
    AdminDecisionInput,
    AdminEvidence,
    AdminEvidenceAccessGrant,
    AdminField,
    AdminModerationStatus,
    AdminMutationReceipt,
    AdminSubmissionDetail,
    AdminSubmissionEditInput,
    AdminSubmissionKind,
    AdminSubmissionPage,
    AdminSubmissionSummary,
    AdminVersionedReasonInput,
    AidLocationAvailability,
    AidLocationRatingInput,
    CommunityContributionReceipt,
    DisasterEventList,
    HealthStatus,
    HumanImpactOverview,
    HumanitarianDirectoryKind,
    HumanitarianDirectorySearchResponse,
    HumanMapBounds,
    HumanMapCluster,
    HumanMapOverview,
    HumanMapPoint,
    HumanMapStatusCounts,
    HumanStatus,
    MissingPersonReportInput,
    MissingPersonReportReceipt,
    MissingPersonSearchResponse,
    MissingPersonStatusReportInput,
    OperationalMapOverview,
    OperationalMapPoint,
    OperationalMapSummary,
    PeopleRecordPage,
    PublicPersonStatus,
    UnverifiedBuildingReportInput,
    UnverifiedBuildingReportReceipt,
    VerificationStatus,
    AdminVisitorPresence,
    AdminVisitorPresencePage,
    VisitorPresenceInput,
    VisitorPresenceReceipt,
)
from .photos import (
    MalwareScanner,
    PhotoProcessingError,
    SignatureMalwareScanner,
    sniff_image_type,
    strip_metadata,
)
from .models import SourceReference
from .repository import (
    AidOfferIdempotencyConflictError,
    DisasterRepository,
    HumanMapCell,
    PostgresDisasterRepository,
    StoredAidOffer,
    StoredBuildingReport,
    StoredMealOfferDetails,
    StoredPhoto,
    StoredRating,
    StoredReport,
    StoredShelterOfferDetails,
    StoredStatusReport,
)
from .storage import (
    LocalObjectStorage,
    ObjectStorage,
    StorageUnavailableError,
)


def problem(status_code: int, title: str, detail: str) -> JSONResponse:
    """Respuesta `application/problem+json` sin datos sensibles."""
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": title,
            "status": status_code,
            "detail": detail,
        },
    )


def build_fernet(passphrase: str) -> Fernet:
    digest = hashlib.sha256(passphrase.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def generate_public_case_code(now: datetime) -> str:
    return f"MP-{now.year}-{secrets.token_hex(4).upper()}"


# CHG-035 — Versión del texto legal de los tres consentimientos.
BUILDING_REPORT_LEGAL_TEXT_VERSION = "chg-035/legal-v1"


def generate_public_tracking_code(now: datetime) -> str:
    """Código público aleatorio y no secuencial (CHG-035)."""
    return f"BR-{now.year}-{secrets.token_hex(4).upper()}"


# CHG-036 — Campos del detalle administrativo por tipo:
# (clave, etiqueta, columna, clasificación, transformación, inputKind).
# La transformación `decrypt` descifra campos protegidos SOLO para la
# consola autorizada; `list` une arreglos en texto legible.
ADMIN_FIELD_SPECS: dict[str, list[tuple]] = {
    "missing_person_report": [
        ("publicCaseCode", "Código público", "public_case_code",
         "public", None, "text"),
        ("firstNames", "Nombres", "first_names", "private", None, "text"),
        ("lastNames", "Apellidos", "last_names", "private", None, "text"),
        ("aliases", "Alias", "aliases", "private", None, "text"),
        ("birthDate", "Fecha de nacimiento", "birth_date", "private",
         None, "date"),
        ("approximateAge", "Edad aproximada", "approximate_age",
         "private", None, "number"),
        ("documentType", "Tipo de documento",
         "document_type_encrypted", "protected", "decrypt", "text"),
        ("documentNumber", "Número de documento",
         "document_number_encrypted", "protected", "decrypt", "text"),
        ("medicalInformation", "Información médica",
         "medical_information_encrypted", "protected", "decrypt",
         "multiline"),
        ("distinctiveMarks", "Señales particulares",
         "distinctive_marks", "private", None, "multiline"),
        ("lastSeenDate", "Última vez vista (fecha)", "last_seen_date",
         "private", None, "date"),
        ("lastSeenTime", "Última vez vista (hora)", "last_seen_time",
         "private", None, "time"),
        ("lastSeenLatitude", "Latitud privada", "last_seen_latitude",
         "protected", None, "number"),
        ("lastSeenLongitude", "Longitud privada", "last_seen_longitude",
         "protected", None, "number"),
        ("department", "Departamento", "department", "public", None,
         "text"),
        ("municipality", "Municipio", "municipality", "public", None,
         "text"),
        ("lastSeenArea", "Zona de última vez", "last_seen_area",
         "private", None, "text"),
        ("clothingDescription", "Vestimenta", "clothing_description",
         "private", None, "multiline"),
        ("circumstances", "Circunstancias", "circumstances", "private",
         None, "multiline"),
        ("additionalDescription", "Descripción adicional",
         "additional_description", "private", None, "multiline"),
        ("reporterName", "Reportante", "reporter_name_encrypted",
         "protected", "decrypt", "text"),
        ("reporterRelationship", "Parentesco", "reporter_relationship",
         "private", None, "text"),
        ("reporterPhone", "Teléfono del reportante",
         "reporter_phone_encrypted", "protected", "decrypt", "text"),
        ("reporterEmail", "Correo del reportante",
         "reporter_email_encrypted", "protected", "decrypt", "email"),
        ("officialReportNumber", "Número de denuncia",
         "official_report_number", "private", None, "text"),
        ("reporterLatitude", "Latitud del reportante",
         "reporter_snapshot_latitude_encrypted", "protected",
         "decrypt", "number"),
        ("reporterLongitude", "Longitud del reportante",
         "reporter_snapshot_longitude_encrypted", "protected",
         "decrypt", "number"),
    ],
    "unverified_building_report": [
        ("buildingReference", "Referencia del edificio",
         "building_reference", "public", None, "text"),
        ("buildingType", "Tipo de edificio", "building_type", "public",
         None, "text"),
        ("department", "Departamento", "department", "public", None,
         "text"),
        ("municipality", "Municipio", "municipality", "public", None,
         "text"),
        ("sector", "Sector", "sector", "public", None, "text"),
        ("locationReference", "Referencia de ubicación",
         "location_reference_protected", "protected", "decrypt",
         "text"),
        ("address", "Dirección exacta", "address_protected",
         "protected", "decrypt", "text"),
        ("latitude", "Latitud privada", "latitude_protected",
         "protected", "decrypt", "number"),
        ("longitude", "Longitud privada", "longitude_protected",
         "protected", "decrypt", "number"),
        ("observedDate", "Fecha de observación", "observed_date",
         "private", None, "date"),
        ("observedTime", "Hora de observación", "observed_time",
         "private", None, "time"),
        ("searchStatus", "Estado de búsqueda", "search_status",
         "public", None, "text"),
        ("occupancyReport", "Reporte de ocupación", "occupancy_report",
         "public", None, "text"),
        ("pendingReasons", "Motivos pendientes", "pending_reasons",
         "public", "list", "text"),
        ("observedConditions", "Condiciones visibles",
         "observed_conditions", "public", "list", "text"),
        ("observationDescription", "Descripción de la observación",
         "observation_description_protected", "protected", "decrypt",
         "multiline"),
        ("reporterName", "Reportante", "reporter_name_protected",
         "protected", "decrypt", "text"),
        ("reporterRole", "Rol del reportante",
         "reporter_role_protected", "protected", "decrypt", "text"),
        ("reporterOrganization", "Organización",
         "reporter_organization_protected", "protected", "decrypt",
         "text"),
        ("reporterPhone", "Teléfono del reportante",
         "reporter_phone_protected", "protected", "decrypt", "text"),
        ("reporterEmail", "Correo del reportante",
         "reporter_email_protected", "protected", "decrypt", "email"),
        ("officialReportNumber", "Número de reporte oficial",
         "official_report_number_protected", "protected", "decrypt",
         "text"),
        ("reporterLatitude", "Latitud del reportante",
         "reporter_snapshot_latitude_protected", "protected",
         "decrypt", "number"),
        ("reporterLongitude", "Longitud del reportante",
         "reporter_snapshot_longitude_protected", "protected",
         "decrypt", "number"),
    ],
    "person_status_report": [
        ("claimedOutcome", "Resultado alegado", "claimed_outcome",
         "public", None, "text"),
        ("evidenceDescription", "Descripción de la evidencia",
         "evidence_description_encrypted", "protected", "decrypt",
         "multiline"),
        ("occurredAt", "Momento del hecho", "occurred_at", "private",
         None, "date"),
        ("locationDescription", "Descripción del lugar",
         "location_description_encrypted", "protected", "decrypt",
         "text"),
        ("actorKind", "Tipo de aporte", "actor_kind", "private", None,
         "text"),
    ],
    "aid_location_rating": [
        ("locationName", "Lugar valorado", "location_name", "public",
         None, "text"),
        ("rating", "Estrellas", "rating", "public", None, "number"),
        ("evidenceDescription", "Descripción de la evidencia",
         "evidence_description_encrypted", "protected", "decrypt",
         "multiline"),
        ("actorKind", "Tipo de aporte", "actor_kind", "private", None,
         "text"),
    ],
    # CHG-044 — Ofertas comunitarias. Los campos sensibles se descifran
    # con la clave EXCLUSIVA de ofertas (`decrypt_aid`), solo para la
    # consola autorizada.
    "community_meal_offer": [
        ("trackingCode", "Código de seguimiento", "tracking_code",
         "public", None, "text"),
        ("title", "Título propuesto", "title_encrypted", "protected",
         "decrypt_aid", "text"),
        ("description", "Descripción propuesta",
         "description_encrypted", "protected", "decrypt_aid",
         "multiline"),
        ("department", "Departamento", "department", "public", None,
         "text"),
        ("municipality", "Municipio", "municipality", "public", None,
         "text"),
        ("areaReference", "Referencia de zona",
         "area_reference_encrypted", "protected", "decrypt_aid",
         "text"),
        ("exactAddress", "Dirección exacta",
         "exact_address_encrypted", "protected", "decrypt_aid", "text"),
        ("latitude", "Latitud privada", "latitude_encrypted",
         "protected", "decrypt_aid", "number"),
        ("longitude", "Longitud privada", "longitude_encrypted",
         "protected", "decrypt_aid", "number"),
        ("availableFrom", "Inicio de disponibilidad", "available_from",
         "private", None, "date"),
        ("availableUntil", "Fin de disponibilidad", "available_until",
         "private", None, "date"),
        ("availabilityStatus", "Disponibilidad",
         "availability_status", "public", None, "text"),
        ("servingsAvailable", "Raciones disponibles",
         "servings_available", "public", None, "number"),
        ("distributionMode", "Modalidad de entrega",
         "distribution_mode", "public", None, "text"),
        ("mealDescription", "Descripción de la comida",
         "meal_description_encrypted", "protected", "decrypt_aid",
         "multiline"),
        ("allergenInformation", "Alérgenos",
         "allergen_information_encrypted", "protected", "decrypt_aid",
         "multiline"),
        ("foodSafetyConfirmed", "Manipulación segura declarada",
         "food_safety_confirmed", "public", None, "text"),
        ("contactName", "Contacto", "contact_name_encrypted",
         "protected", "decrypt_aid", "text"),
        ("contactPhone", "Teléfono de contacto",
         "contact_phone_encrypted", "protected", "decrypt_aid", "text"),
        ("contactEmail", "Correo de contacto",
         "contact_email_encrypted", "protected", "decrypt_aid",
         "email"),
    ],
    "temporary_shelter_offer": [
        ("trackingCode", "Código de seguimiento", "tracking_code",
         "public", None, "text"),
        ("title", "Título propuesto", "title_encrypted", "protected",
         "decrypt_aid", "text"),
        ("description", "Descripción propuesta",
         "description_encrypted", "protected", "decrypt_aid",
         "multiline"),
        ("department", "Departamento", "department", "public", None,
         "text"),
        ("municipality", "Municipio", "municipality", "public", None,
         "text"),
        ("areaReference", "Referencia de zona",
         "area_reference_encrypted", "protected", "decrypt_aid",
         "text"),
        ("exactAddress", "Dirección exacta",
         "exact_address_encrypted", "protected", "decrypt_aid", "text"),
        ("latitude", "Latitud privada", "latitude_encrypted",
         "protected", "decrypt_aid", "number"),
        ("longitude", "Longitud privada", "longitude_encrypted",
         "protected", "decrypt_aid", "number"),
        ("availableFrom", "Inicio de disponibilidad", "available_from",
         "private", None, "date"),
        ("availableUntil", "Fin de disponibilidad", "available_until",
         "private", None, "date"),
        ("availabilityStatus", "Disponibilidad",
         "availability_status", "public", None, "text"),
        ("spacesAvailable", "Espacios disponibles", "spaces_available",
         "public", None, "number"),
        ("sharedSpace", "Espacio compartido", "shared_space", "public",
         None, "text"),
        ("acceptsPets", "Acepta mascotas", "accepts_pets", "public",
         None, "text"),
        ("accessibilityNotes", "Notas de accesibilidad",
         "accessibility_notes_encrypted", "protected", "decrypt_aid",
         "multiline"),
        ("shelterSafetyConfirmed", "Seguridad básica declarada",
         "shelter_safety_confirmed", "public", None, "text"),
        ("contactName", "Contacto", "contact_name_encrypted",
         "protected", "decrypt_aid", "text"),
        ("contactPhone", "Teléfono de contacto",
         "contact_phone_encrypted", "protected", "decrypt_aid", "text"),
        ("contactEmail", "Correo de contacto",
         "contact_email_encrypted", "protected", "decrypt_aid",
         "email"),
    ],
}

# Unión discriminada del contrato para el ingreso de ofertas (CHG-044).
AID_OFFER_INPUT_ADAPTER: TypeAdapter = TypeAdapter(
    Annotated[
        CommunityMealOfferInput | TemporaryShelterOfferInput,
        Field(discriminator="kind"),
    ]
)


def protect_missing_person(
    point: OperationalMapPoint,
) -> OperationalMapPoint:
    """Aplica la regla pública de DEC-007 antes de serializar.

    Un punto `missing_person` representa una zona pública de búsqueda:
    mientras DEC-007 esté pendiente nunca se publica con precisión
    `exact`; si la base contiene un dato así, se degrada a zona
    aproximada redondeando la coordenada (~1 km).
    """
    if point.category != "missing_person":
        return point
    if point.coordinate_precision != "exact":
        return point
    return point.model_copy(
        update={
            "coordinate_precision": "approximate",
            "latitude": round(point.latitude, 2),
            "longitude": round(point.longitude, 2),
        }
    )


# CHG-015 — Capa geográfica de situación humana.
HUMAN_MAP_GRID_DIVISIONS = 20

# CHG-034 — Una valoración admite hasta tres fotografías (contrato).
MAX_RATING_PHOTOS = 3


def human_map_cell_size(
    zoom: int,
    west: float,
    south: float,
    east: float,
    north: float,
) -> float:
    """Tamaño determinista de celda de la grilla de clustering.

    La componente de zoom divide el mundo en celdas de ~1/4 de tesela;
    las componentes de bbox garantizan a lo sumo 20×20 celdas visibles,
    de modo que la respuesta nunca supera 500 features.
    """
    zoom_cell = 360.0 / (2**zoom * 4)
    return max(
        zoom_cell,
        (east - west) / HUMAN_MAP_GRID_DIVISIONS,
        (north - south) / HUMAN_MAP_GRID_DIVISIONS,
    )


def encode_map_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def decode_map_cursor(cursor: str) -> int | None:
    """Devuelve el offset del cursor opaco, o None si es inválido."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    if not raw.startswith("o:"):
        return None
    try:
        offset = int(raw[2:])
    except ValueError:
        return None
    return offset if offset >= 0 else None


def build_human_map_features(
    cells: list[HumanMapCell],
    zoom: int,
) -> list[HumanMapCluster | HumanMapPoint]:
    """Convierte celdas en features: clusters primero, luego puntos.

    El orden es determinista (clusters por count descendente e id;
    puntos por id) para que el cursor sea estable entre páginas.
    """
    clusters: list[HumanMapCluster] = []
    points: list[HumanMapPoint] = []
    for cell in cells:
        if cell.count == 1:
            precision = cell.point_precision
            latitude = cell.latitude
            longitude = cell.longitude
            if precision == "exact":
                # Defensa en profundidad DEC-007: la base ya lo impide,
                # pero jamás se publica una coordenada exacta.
                precision = "approximate"
                latitude = round(latitude, 2)
                longitude = round(longitude, 2)
            points.append(
                HumanMapPoint(
                    id=cell.point_id,
                    status=cell.point_status,
                    latitude=latitude,
                    longitude=longitude,
                    coordinate_precision=precision,
                    verification_status=cell.point_verification,
                    source=SourceReference(
                        name=cell.point_source_name,
                        source_type=cell.point_source_type,
                        url=cell.point_source_url,
                    ),
                    updated_at=cell.point_updated_at,
                )
            )
        else:
            clusters.append(
                HumanMapCluster(
                    id=f"z{zoom}:x{cell.cell_x}:y{cell.cell_y}",
                    latitude=cell.latitude,
                    longitude=cell.longitude,
                    count=cell.count,
                    status_counts=HumanMapStatusCounts(
                        missing=cell.missing,
                        reported_deceased=cell.reported_deceased,
                        confirmed_alive=cell.confirmed_alive,
                        confirmed_deceased=cell.confirmed_deceased,
                    ),
                    bounds=HumanMapBounds(
                        west=cell.west,
                        south=cell.south,
                        east=cell.east,
                        north=cell.north,
                    ),
                )
            )
    clusters.sort(key=lambda cluster: (-cluster.count, cluster.id))
    points.sort(key=lambda point: str(point.id))
    return [*clusters, *points]


def create_app(
    settings: Settings | None = None,
    repository: DisasterRepository | None = None,
    storage: ObjectStorage | None = None,
    scanner: MalwareScanner | None = None,
    notifier: notifications.ReportNotifier | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_environment()
    object_storage = storage or LocalObjectStorage(
        resolved_settings.upload_dir
    )
    malware_scanner = scanner or SignatureMalwareScanner()
    report_notifier = notifier or notifications.HttpReportNotifier(
        resolved_settings.identity_service_url,
        resolved_settings.notification_timeout_seconds,
    )
    fernet = build_fernet(resolved_settings.report_encryption_key)

    def encrypt(value: str | None) -> bytes | None:
        if value is None or value == "":
            return None
        return fernet.encrypt(value.encode())

    # CHG-044: clave EXCLUSIVA de ofertas desde un secreto montado. Si
    # falta o es insegura, readiness cae y toda escritura de ofertas se
    # rechaza con 503; el resto del servicio no la usa jamás.
    aid_offer_fernet: Fernet | None = None
    aid_offer_key_error: str | None = None
    try:
        aid_offer_fernet = build_fernet(
            offer_rules.load_aid_offer_key(
                resolved_settings.aid_offer_encryption_key_file
            )
        )
    except offer_rules.AidOfferKeyError as error:
        aid_offer_key_error = str(error)

    def encrypt_aid(value: str | None) -> bytes | None:
        if value is None or value == "":
            return None
        if aid_offer_fernet is None:
            raise offer_rules.AidOfferKeyError(
                aid_offer_key_error or "Clave de ofertas no disponible."
            )
        return aid_offer_fernet.encrypt(value.encode())

    def aid_offer_key_unavailable() -> JSONResponse:
        return problem(
            503,
            "Cifrado de ofertas no disponible",
            "La clave de cifrado de ofertas no está disponible; "
            "ningún dato quedó registrado.",
        )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if repository is not None:
            application.state.repository = repository
            yield
            return

        pool = await asyncpg.create_pool(
            resolved_settings.database_url,
            min_size=resolved_settings.database_pool_min_size,
            max_size=resolved_settings.database_pool_max_size,
            command_timeout=5,
        )
        application.state.repository = PostgresDisasterRepository(pool)
        try:
            yield
        finally:
            await pool.close()

    application = FastAPI(
        title="CUSOL UIS Disasters Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    def get_repository(request: Request) -> DisasterRepository:
        return request.app.state.repository

    @application.get(
        "/health/live",
        response_model=HealthStatus,
        tags=["Platform"],
    )
    async def liveness() -> HealthStatus:
        return HealthStatus(status="ok", service="disaster-service")

    @application.get(
        "/health/ready",
        response_model=HealthStatus,
        responses={503: {"description": "Base de datos no disponible"}},
        tags=["Platform"],
    )
    async def readiness(
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        try:
            ready = await data.ping()
        except (asyncpg.PostgresError, TimeoutError):
            ready = False

        # CHG-044: sin la clave de ofertas el servicio no está listo.
        if not ready or aid_offer_fernet is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "service": "disaster-service",
                },
            )

        return HealthStatus(status="ok", service="disaster-service")

    @application.get(
        "/internal/v1/disasters",
        response_model=DisasterEventList,
        response_model_by_alias=True,
        tags=["Disasters"],
    )
    async def list_disasters(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
        disaster_type: Annotated[
            str | None, Query(alias="disasterType", min_length=1)
        ] = None,
        verification_status: Annotated[
            Literal[
                "unverified", "under_review", "verified", "rejected"
            ]
            | None,
            Query(alias="verificationStatus"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> DisasterEventList:
        items, total = await data.list_events(
            disaster_type=disaster_type,
            verification_status=verification_status,
            limit=limit,
            offset=offset,
        )
        return DisasterEventList(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        )

    @application.get(
        "/internal/v1/people/overview",
        response_model=HumanImpactOverview,
        response_model_by_alias=True,
        tags=["People"],
    )
    async def people_overview(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
        recent_limit: Annotated[
            int, Query(alias="recentLimit", ge=10, le=50)
        ] = 10,
    ) -> HumanImpactOverview:
        summary, recent = await data.people_overview(recent_limit)
        return HumanImpactOverview(
            summary=summary,
            recent_people=recent,
            generated_at=datetime.now(UTC),
        )

    @application.get(
        "/internal/v1/people/records",
        response_model=PeopleRecordPage,
        response_model_by_alias=True,
        tags=["People"],
    )
    async def list_people_records(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
        offset: Annotated[int, Query(ge=0)] = 0,
        statuses: Annotated[
            list[HumanStatus] | None, Query()
        ] = None,
        q: Annotated[str | None, Query(max_length=100)] = None,
    ) -> PeopleRecordPage | JSONResponse:
        if limit not in (10, 25, 50):
            return problem(
                422,
                "Tamaño de página inválido",
                "El tamaño de página debe ser 10, 25 o 50.",
            )
        search = (q or "").strip()
        if q is not None and q.strip() and len(search) < 2:
            return problem(
                422,
                "Búsqueda inválida",
                "La búsqueda requiere entre 2 y 100 caracteres.",
            )
        unique_statuses = (
            list(dict.fromkeys(statuses)) if statuses else None
        )
        items, total = await data.list_people_records(
            unique_statuses,
            search or None,
            limit,
            offset,
        )
        return PeopleRecordPage(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            generated_at=datetime.now(UTC),
        )

    @application.get(
        "/internal/v1/people/map-overview",
        response_model=HumanMapOverview,
        response_model_by_alias=True,
        tags=["People"],
    )
    async def human_map_overview(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
        west: Annotated[float, Query(ge=-180, le=180)],
        south: Annotated[float, Query(ge=-90, le=90)],
        east: Annotated[float, Query(ge=-180, le=180)],
        north: Annotated[float, Query(ge=-90, le=90)],
        zoom: Annotated[int, Query(ge=3, le=19)],
        statuses: Annotated[
            list[HumanStatus] | None, Query()
        ] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
        cursor: Annotated[str | None, Query(max_length=100)] = None,
    ):
        if west >= east or south >= north:
            return problem(
                422,
                "Área inválida",
                "El bbox requiere west < east y south < north.",
            )
        offset = 0
        if cursor is not None:
            decoded = decode_map_cursor(cursor)
            if decoded is None:
                return problem(
                    422,
                    "Cursor inválido",
                    "El cursor no corresponde a esta consulta.",
                )
            offset = decoded

        cell_size = human_map_cell_size(zoom, west, south, east, north)
        cells, unmapped = await data.human_map_overview(
            west,
            south,
            east,
            north,
            cell_size,
            list(statuses) if statuses else None,
        )
        features = build_human_map_features(cells, zoom)
        total_mapped = sum(cell.count for cell in cells)
        page = features[offset:offset + limit]
        next_cursor = (
            encode_map_cursor(offset + limit)
            if offset + limit < len(features)
            else None
        )
        classification = (
            "operational"
            if cells and all(cell.all_operational for cell in cells)
            else "demonstrative"
        )
        return HumanMapOverview(
            features=page,
            total_matched=total_mapped + unmapped,
            total_mapped=total_mapped,
            unmapped_count=unmapped,
            returned_features=len(page),
            next_cursor=next_cursor,
            generated_at=datetime.now(UTC),
            data_classification=classification,
        )

    @application.get(
        "/internal/v1/operational-map/overview",
        response_model=OperationalMapOverview,
        response_model_by_alias=True,
        tags=["OperationalMap"],
    )
    async def operational_map_overview(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> OperationalMapOverview:
        points, classification = await data.operational_map_overview(
            limit
        )
        items = [protect_missing_person(point) for point in points]
        by_category = {
            "missing_person": 0,
            "collection_center": 0,
            "collection_point": 0,
            "rubble_reviewed": 0,
            "rubble_pending": 0,
            "building_pending": 0,
            "community_meal": 0,
            "temporary_shelter": 0,
        }
        for item in items:
            by_category[item.category] += 1
        return OperationalMapOverview(
            summary=OperationalMapSummary(
                missing_person=by_category["missing_person"],
                collection_center=by_category["collection_center"],
                collection_point=by_category["collection_point"],
                rubble_reviewed=by_category["rubble_reviewed"],
                rubble_pending=by_category["rubble_pending"],
                building_pending=by_category["building_pending"],
                community_meal=by_category["community_meal"],
                temporary_shelter=by_category["temporary_shelter"],
            ),
            items=items,
            generated_at=datetime.now(UTC),
            data_classification=classification,
        )

    @application.get(
        "/internal/v1/missing-persons/search",
        response_model=MissingPersonSearchResponse,
        response_model_by_alias=True,
        tags=["MissingPersons"],
    )
    async def search_missing_persons(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
        q: Annotated[str, Query(min_length=2, max_length=100)],
        limit: Annotated[int, Query(ge=1, le=20)] = 10,
    ):
        if len(q.strip()) < 2:
            return problem(
                422,
                "Consulta inválida",
                "La consulta requiere al menos dos caracteres.",
            )
        items, total = await data.search_missing_persons(q, limit)
        return MissingPersonSearchResponse(
            items=items, total=total, query=q
        )

    @application.post(
        "/internal/v1/missing-person-reports",
        status_code=201,
        response_model=MissingPersonReportReceipt,
        response_model_by_alias=True,
        tags=["MissingPersons"],
    )
    async def create_missing_person_report(
        request: Request,
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
    ):
        limits = resolved_settings
        idempotency_key = request.headers.get(
            "idempotency-key", ""
        ).strip()
        if not 16 <= len(idempotency_key) <= 128:
            return problem(
                422,
                "Encabezado requerido",
                "Idempotency-Key debe tener entre 16 y 128 caracteres.",
            )
        # CHG-054: cuenta opcional declarada por el gateway; el canal
        # sigue siendo público y jamás exige sesión.
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        _actor_kind, reporter_account_id = actor

        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit():
            # Margen de 2 MiB para payload y fronteras multipart.
            if int(declared) > limits.max_total_photo_bytes + 2_097_152:
                return problem(
                    413,
                    "Carga demasiado grande",
                    "El envío supera el máximo total de 50 MiB.",
                )

        try:
            form = await request.form()
        except Exception:
            return problem(
                422,
                "Formulario inválido",
                "No fue posible interpretar el envío multipart.",
            )

        payload_part = form.get("payload")
        if payload_part is None:
            return problem(
                422,
                "Datos incompletos",
                "Falta la parte JSON `payload`.",
            )
        if isinstance(payload_part, str):
            raw_payload = payload_part.encode()
        else:
            raw_payload = await payload_part.read()

        try:
            payload = MissingPersonReportInput.model_validate_json(
                raw_payload
            )
        except ValidationError as error:
            # Nunca se devuelven ni registran los valores enviados.
            fields = sorted(
                {
                    str(item["loc"][0]) if item["loc"] else "payload"
                    for item in error.errors()
                }
            )
            return problem(
                422,
                "Datos inválidos",
                "Revisa los campos: " + ", ".join(fields) + ".",
            )

        if payload.reporter_phone is None and payload.reporter_email is None:
            return problem(
                422,
                "Contacto requerido",
                "Se requiere al menos teléfono o correo del reportante.",
            )
        if payload.last_seen_date > datetime.now(UTC).date():
            return problem(
                422,
                "Fecha inválida",
                "La fecha de última visualización no puede ser futura.",
            )

        photo_parts = [
            value
            for key, value in form.multi_items()
            if key == "photos"
        ]
        if not 1 <= len(photo_parts) <= limits.max_photos:
            return problem(
                422,
                "Cantidad de fotografías inválida",
                "El reporte requiere entre una y cinco fotografías.",
            )

        prepared: list[tuple[int, bytes, str]] = []
        total_bytes = 0
        for index, part in enumerate(photo_parts, start=1):
            if isinstance(part, str):
                return problem(
                    415,
                    "Fotografía inválida",
                    f"La fotografía {index} no es un archivo.",
                )
            content = await part.read()
            if len(content) > limits.max_photo_bytes:
                return problem(
                    413,
                    "Archivo demasiado grande",
                    f"La fotografía {index} supera el máximo de 10 MiB.",
                )
            total_bytes += len(content)
            if total_bytes > limits.max_total_photo_bytes:
                return problem(
                    413,
                    "Carga demasiado grande",
                    "El envío supera el máximo total de 50 MiB.",
                )
            sniffed = sniff_image_type(content)
            if sniffed is None:
                return problem(
                    415,
                    "Formato no permitido",
                    f"La fotografía {index} no es JPEG, PNG, WebP ni "
                    "HEIC/HEIF según su contenido real.",
                )
            if not malware_scanner.scan(content):
                return problem(
                    415,
                    "Contenido no permitido",
                    f"La fotografía {index} no superó el análisis de "
                    "seguridad.",
                )
            prepared.append((index, content, sniffed))

        received_at = datetime.now(UTC)
        report_id = uuid4()
        saved_keys: list[str] = []
        stored_photos: list[StoredPhoto] = []

        def cleanup() -> None:
            for key in saved_keys:
                object_storage.delete(key)

        try:
            for index, content, sniffed in prepared:
                sanitized = strip_metadata(content, sniffed)
                photo_id = uuid4()
                prefix = f"missing-person-reports/{report_id}"
                original_key = f"{prefix}/original/{photo_id}.bin"
                derived_key = (
                    f"{prefix}/derived/{photo_id}.{sanitized.extension}"
                )
                object_storage.save(original_key, content)
                saved_keys.append(original_key)
                object_storage.save(derived_key, sanitized.data)
                saved_keys.append(derived_key)
                stored_photos.append(
                    StoredPhoto(
                        id=photo_id,
                        position=index,
                        storage_key=original_key,
                        derived_storage_key=derived_key,
                        content_type=sanitized.content_type,
                        size_bytes=len(content),
                        sha256=hashlib.sha256(content).hexdigest(),
                        exif_removed=True,
                        malware_scan="clean",
                    )
                )
        except PhotoProcessingError:
            cleanup()
            return problem(
                415,
                "Fotografía no procesable",
                "Alguna fotografía no pudo validarse como imagen segura.",
            )
        except StorageUnavailableError:
            cleanup()
            return problem(
                503,
                "Almacenamiento no disponible",
                "No fue posible resguardar las fotografías; el reporte "
                "no fue registrado.",
            )

        stored_report = StoredReport(
            id=report_id,
            idempotency_key=idempotency_key,
            public_case_code=generate_public_case_code(received_at),
            first_names=payload.first_names,
            last_names=payload.last_names,
            aliases=payload.aliases,
            birth_date=payload.birth_date,
            approximate_age=payload.approximate_age,
            gender_identity=payload.gender_identity,
            nationality=payload.nationality,
            document_type_encrypted=encrypt(payload.document_type),
            document_number_encrypted=encrypt(payload.document_number),
            height_cm=payload.height_cm,
            build=payload.build,
            skin_tone=payload.skin_tone,
            hair_description=payload.hair_description,
            eye_description=payload.eye_description,
            distinctive_marks=payload.distinctive_marks,
            medical_information_encrypted=encrypt(
                payload.medical_information
            ),
            last_seen_date=payload.last_seen_date,
            last_seen_time=payload.last_seen_time,
            last_seen_latitude=payload.last_seen_latitude,
            last_seen_longitude=payload.last_seen_longitude,
            department=payload.department,
            municipality=payload.municipality,
            last_seen_area=payload.last_seen_area,
            clothing_description=payload.clothing_description,
            circumstances=payload.circumstances,
            additional_description=payload.additional_description,
            reporter_name_encrypted=encrypt(payload.reporter_name),
            reporter_relationship=payload.reporter_relationship,
            reporter_phone_encrypted=encrypt(payload.reporter_phone),
            reporter_email_encrypted=encrypt(payload.reporter_email),
            official_report_number=payload.official_report_number,
            reporter_account_id=reporter_account_id,
            reporter_snapshot_latitude_encrypted=(
                None
                if payload.reporter_latitude is None
                else encrypt(repr(payload.reporter_latitude))
            ),
            reporter_snapshot_longitude_encrypted=(
                None
                if payload.reporter_longitude is None
                else encrypt(repr(payload.reporter_longitude))
            ),
        )

        try:
            receipt, created = await data.create_missing_person_report(
                stored_report, stored_photos
            )
        except asyncpg.PostgresError:
            cleanup()
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar el reporte; ningún dato "
                "quedó publicado.",
            )

        if not created:
            # Reintento idempotente: los archivos de este intento sobran.
            cleanup()

        return receipt

    # CHG-034 — Directorio humanitario y aportes con evidencia.

    def validate_idempotency_key(request: Request) -> str | JSONResponse:
        key = request.headers.get("idempotency-key", "").strip()
        if not 16 <= len(key) <= 128:
            return problem(
                422,
                "Encabezado requerido",
                "Idempotency-Key debe tener entre 16 y 128 caracteres.",
            )
        return key

    def resolve_actor(
        request: Request,
    ) -> tuple[str, UUID | None] | JSONResponse:
        """Actor declarado por el gateway; nunca por el cliente final."""
        actor_kind = request.headers.get(
            "x-actor-kind", "anonymous"
        ).strip()
        if actor_kind not in ("anonymous", "authenticated"):
            return problem(
                422, "Actor inválido", "Tipo de actor no reconocido."
            )
        account_id: UUID | None = None
        if actor_kind == "authenticated":
            raw_account = request.headers.get("x-account-id", "").strip()
            try:
                account_id = UUID(raw_account)
            except ValueError:
                return problem(
                    422,
                    "Actor inválido",
                    "La ruta autenticada requiere la cuenta asociada.",
                )
        return actor_kind, account_id

    def check_declared_length(request: Request) -> JSONResponse | None:
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit():
            # Margen de 2 MiB para payload y fronteras multipart.
            limit = resolved_settings.max_total_photo_bytes + 2_097_152
            if int(declared) > limit:
                return problem(
                    413,
                    "Carga demasiado grande",
                    "El envío supera el máximo total de 50 MiB.",
                )
        return None

    async def read_payload_part(form) -> bytes | JSONResponse:
        payload_part = form.get("payload")
        if payload_part is None:
            return problem(
                422,
                "Datos incompletos",
                "Falta la parte JSON `payload`.",
            )
        if isinstance(payload_part, str):
            return payload_part.encode()
        return await payload_part.read()

    def invalid_fields_problem(error: ValidationError) -> JSONResponse:
        # Nunca se devuelven ni registran los valores enviados.
        fields = sorted(
            {
                str(item["loc"][0]) if item["loc"] else "payload"
                for item in error.errors()
            }
        )
        return problem(
            422,
            "Datos inválidos",
            "Revisa los campos: " + ", ".join(fields) + ".",
        )

    async def prepare_photo_parts(
        form, minimum: int, maximum: int, count_detail: str
    ) -> list[tuple[int, bytes, str]] | JSONResponse:
        """Valida cantidad, tamaño, tipo real y malware; todo o nada."""
        photo_parts = [
            value
            for key, value in form.multi_items()
            if key == "photos"
        ]
        if not minimum <= len(photo_parts) <= maximum:
            return problem(
                422, "Cantidad de fotografías inválida", count_detail
            )
        prepared: list[tuple[int, bytes, str]] = []
        total_bytes = 0
        for index, part in enumerate(photo_parts, start=1):
            if isinstance(part, str):
                return problem(
                    415,
                    "Fotografía inválida",
                    f"La fotografía {index} no es un archivo.",
                )
            content = await part.read()
            if len(content) > resolved_settings.max_photo_bytes:
                return problem(
                    413,
                    "Archivo demasiado grande",
                    f"La fotografía {index} supera el máximo de 10 MiB.",
                )
            total_bytes += len(content)
            if total_bytes > resolved_settings.max_total_photo_bytes:
                return problem(
                    413,
                    "Carga demasiado grande",
                    "El envío supera el máximo total de 50 MiB.",
                )
            sniffed = sniff_image_type(content)
            if sniffed is None:
                return problem(
                    415,
                    "Formato no permitido",
                    f"La fotografía {index} no es JPEG, PNG, WebP ni "
                    "HEIC/HEIF según su contenido real.",
                )
            if not malware_scanner.scan(content):
                return problem(
                    415,
                    "Contenido no permitido",
                    f"La fotografía {index} no superó el análisis de "
                    "seguridad.",
                )
            prepared.append((index, content, sniffed))
        return prepared

    def store_photos(
        prefix: str,
        prepared: list[tuple[int, bytes, str]],
        saved_keys: list[str],
    ) -> list[StoredPhoto]:
        """Guarda original y derivado sin metadatos con claves opacas.

        Las fotos jamás obtienen URL pública automática; en particular
        la evidencia de fallecimiento queda solo como expediente privado.
        """
        stored: list[StoredPhoto] = []
        for index, content, sniffed in prepared:
            sanitized = strip_metadata(content, sniffed)
            photo_id = uuid4()
            original_key = f"{prefix}/original/{photo_id}.bin"
            derived_key = (
                f"{prefix}/derived/{photo_id}.{sanitized.extension}"
            )
            object_storage.save(original_key, content)
            saved_keys.append(original_key)
            object_storage.save(derived_key, sanitized.data)
            saved_keys.append(derived_key)
            stored.append(
                StoredPhoto(
                    id=photo_id,
                    position=index,
                    storage_key=original_key,
                    derived_storage_key=derived_key,
                    content_type=sanitized.content_type,
                    size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    exif_removed=True,
                    malware_scan="clean",
                )
            )
        return stored

    @application.get(
        "/internal/v1/humanitarian-directory/search",
        response_model=HumanitarianDirectorySearchResponse,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def search_humanitarian_directory(
        data: Annotated[DisasterRepository, Depends(get_repository)],
        kind: Annotated[HumanitarianDirectoryKind, Query()],
        q: Annotated[str, Query(min_length=2, max_length=100)],
        person_status: Annotated[
            PublicPersonStatus | None, Query(alias="personStatus")
        ] = None,
        verification_status: Annotated[
            VerificationStatus | None, Query(alias="verificationStatus")
        ] = None,
        availability_status: Annotated[
            AidLocationAvailability | None,
            Query(alias="availabilityStatus"),
        ] = None,
        open_now: Annotated[bool | None, Query(alias="openNow")] = None,
        department: Annotated[
            str | None, Query(min_length=2, max_length=100)
        ] = None,
        min_rating: Annotated[
            float | None, Query(alias="minRating", ge=1, le=5)
        ] = None,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if len(q.strip()) < 2:
            return problem(
                422,
                "Consulta inválida",
                "La consulta requiere entre 2 y 100 caracteres.",
            )
        aid_filters = (
            verification_status is not None
            or availability_status is not None
            or open_now is not None
            or min_rating is not None
        )
        if kind == "missing_person":
            if aid_filters:
                return problem(
                    422,
                    "Filtros inválidos",
                    "Los filtros de lugares no aplican a personas.",
                )
            items, total = await data.search_directory_missing_persons(
                q,
                person_status,
                department,
                limit,
                offset,
            )
        elif kind in ("community_meal", "temporary_shelter"):
            # CHG-044: solo la proyección pública activa y vigente;
            # los filtros de personas y lugares no aplican aquí.
            if aid_filters or person_status is not None:
                return problem(
                    422,
                    "Filtros inválidos",
                    "Los filtros de personas o lugares no aplican a "
                    "ofertas comunitarias.",
                )
            items, total = await data.search_directory_aid_offers(
                kind,
                q,
                department,
                limit,
                offset,
            )
        else:
            if person_status is not None:
                return problem(
                    422,
                    "Filtros inválidos",
                    "El filtro de estado de persona no aplica a lugares.",
                )
            items, total = await data.search_directory_aid_locations(
                kind,
                q,
                verification_status,
                availability_status,
                open_now,
                department,
                min_rating,
                limit,
                offset,
            )
        return HumanitarianDirectorySearchResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            query=q,
            kind=kind,
            generated_at=datetime.now(UTC),
        )

    @application.post(
        "/internal/v1/missing-persons/{person_id}/status-reports",
        status_code=202,
        response_model=CommunityContributionReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def create_person_status_report(
        person_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        idempotency_key = validate_idempotency_key(request)
        if isinstance(idempotency_key, JSONResponse):
            return idempotency_key
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_kind, account_id = actor
        oversize = check_declared_length(request)
        if oversize is not None:
            return oversize

        # 404 uniforme: inexistente y no publicable son indistinguibles
        # para no filtrar la existencia de expedientes privados.
        try:
            publishable = await data.person_is_publishable(person_id)
        except asyncpg.PostgresError:
            return problem(
                503,
                "Servicio de evidencia no disponible",
                "No fue posible validar la persona; la novedad no fue "
                "registrada.",
            )
        if not publishable:
            return problem(
                404,
                "Persona no disponible",
                "La persona no existe o no es publicable.",
            )

        try:
            form = await request.form()
        except Exception:
            return problem(
                422,
                "Formulario inválido",
                "No fue posible interpretar el envío multipart.",
            )

        raw_payload = await read_payload_part(form)
        if isinstance(raw_payload, JSONResponse):
            return raw_payload
        try:
            payload = MissingPersonStatusReportInput.model_validate_json(
                raw_payload
            )
        except ValidationError as error:
            return invalid_fields_problem(error)

        occurred_at = payload.occurred_at
        if occurred_at is not None:
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=UTC)
            if occurred_at > datetime.now(UTC):
                return problem(
                    422,
                    "Fecha inválida",
                    "La fecha de la novedad no puede ser futura.",
                )

        prepared = await prepare_photo_parts(
            form,
            1,
            resolved_settings.max_photos,
            "La novedad requiere entre una y cinco fotografías.",
        )
        if isinstance(prepared, JSONResponse):
            return prepared

        report_id = uuid4()
        saved_keys: list[str] = []

        def cleanup() -> None:
            for key in saved_keys:
                object_storage.delete(key)

        try:
            stored_photos = store_photos(
                f"person-status-reports/{report_id}",
                prepared,
                saved_keys,
            )
        except PhotoProcessingError:
            cleanup()
            return problem(
                415,
                "Fotografía no procesable",
                "Alguna fotografía no pudo validarse como imagen segura.",
            )
        except StorageUnavailableError:
            cleanup()
            return problem(
                503,
                "Almacenamiento no disponible",
                "No fue posible resguardar la evidencia; la novedad no "
                "fue registrada.",
            )

        stored_report = StoredStatusReport(
            id=report_id,
            person_id=person_id,
            idempotency_key=idempotency_key,
            claimed_outcome=payload.claimed_outcome,
            evidence_description_encrypted=encrypt(
                payload.evidence_description
            ),
            occurred_at=occurred_at,
            location_description_encrypted=encrypt(
                payload.location_description
            ),
            actor_kind=actor_kind,
            account_id=account_id,
        )
        try:
            receipt, created = await data.create_person_status_report(
                stored_report, stored_photos
            )
        except asyncpg.PostgresError:
            cleanup()
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar la novedad; ningún dato quedó "
                "publicado.",
            )
        if not created:
            # Reintento idempotente: los archivos de este intento sobran.
            cleanup()
        return receipt

    @application.post(
        "/internal/v1/aid-locations/{location_id}/ratings",
        status_code=202,
        response_model=CommunityContributionReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def create_aid_location_rating(
        location_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        idempotency_key = validate_idempotency_key(request)
        if isinstance(idempotency_key, JSONResponse):
            return idempotency_key
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_kind, account_id = actor
        oversize = check_declared_length(request)
        if oversize is not None:
            return oversize

        try:
            publishable = await data.aid_location_is_publishable(
                location_id
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Servicio de valoraciones no disponible",
                "No fue posible validar el lugar; la valoración no fue "
                "registrada.",
            )
        if not publishable:
            return problem(
                404,
                "Lugar no disponible",
                "El lugar no existe o no es publicable.",
            )

        try:
            form = await request.form()
        except Exception:
            return problem(
                422,
                "Formulario inválido",
                "No fue posible interpretar el envío multipart.",
            )

        raw_payload = await read_payload_part(form)
        if isinstance(raw_payload, JSONResponse):
            return raw_payload
        try:
            payload = AidLocationRatingInput.model_validate_json(
                raw_payload
            )
        except ValidationError as error:
            return invalid_fields_problem(error)

        prepared = await prepare_photo_parts(
            form,
            0,
            MAX_RATING_PHOTOS,
            "La valoración admite hasta tres fotografías.",
        )
        if isinstance(prepared, JSONResponse):
            return prepared

        rating_id = uuid4()
        saved_keys: list[str] = []

        def cleanup() -> None:
            for key in saved_keys:
                object_storage.delete(key)

        try:
            stored_photos = store_photos(
                f"aid-location-ratings/{rating_id}",
                prepared,
                saved_keys,
            )
        except PhotoProcessingError:
            cleanup()
            return problem(
                415,
                "Fotografía no procesable",
                "Alguna fotografía no pudo validarse como imagen segura.",
            )
        except StorageUnavailableError:
            cleanup()
            return problem(
                503,
                "Almacenamiento no disponible",
                "No fue posible resguardar la evidencia; la valoración "
                "no fue registrada.",
            )

        stored_rating = StoredRating(
            id=rating_id,
            location_id=location_id,
            idempotency_key=idempotency_key,
            rating=payload.rating,
            evidence_description_encrypted=encrypt(
                payload.evidence_description
            ),
            actor_kind=actor_kind,
            account_id=account_id,
        )
        try:
            receipt, created = await data.create_aid_location_rating(
                stored_rating, stored_photos
            )
        except asyncpg.PostgresError:
            cleanup()
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar la valoración; ningún dato "
                "quedó publicado.",
            )
        if not created:
            cleanup()
        return receipt

    # CHG-035 — Reporte ciudadano de edificio sin verificar.

    @application.post(
        "/internal/v1/unverified-building-reports",
        status_code=201,
        response_model=UnverifiedBuildingReportReceipt,
        response_model_by_alias=True,
        tags=["BuildingReports"],
    )
    async def create_unverified_building_report(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        idempotency_key = validate_idempotency_key(request)
        if isinstance(idempotency_key, JSONResponse):
            return idempotency_key
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        _actor_kind, account_id = actor
        oversize = check_declared_length(request)
        if oversize is not None:
            return oversize

        try:
            form = await request.form()
        except Exception:
            return problem(
                422,
                "Formulario inválido",
                "No fue posible interpretar el envío multipart.",
            )

        raw_payload = await read_payload_part(form)
        if isinstance(raw_payload, JSONResponse):
            return raw_payload
        try:
            payload = UnverifiedBuildingReportInput.model_validate_json(
                raw_payload
            )
        except ValidationError as error:
            return invalid_fields_problem(error)

        if payload.observed_date > datetime.now(UTC).date():
            return problem(
                422,
                "Fecha inválida",
                "La fecha de observación no puede ser futura.",
            )

        prepared = await prepare_photo_parts(
            form,
            1,
            resolved_settings.max_photos,
            "El reporte requiere entre una y cinco fotografías.",
        )
        if isinstance(prepared, JSONResponse):
            return prepared

        received_at = datetime.now(UTC)
        report_id = uuid4()
        saved_keys: list[str] = []

        def cleanup() -> None:
            for key in saved_keys:
                object_storage.delete(key)

        try:
            # Originales en cuarentena y derivados sin EXIF, con claves
            # opacas; nunca nombres originales ni URL pública.
            stored_files = store_photos(
                f"unverified-building-reports/{report_id}",
                prepared,
                saved_keys,
            )
        except PhotoProcessingError:
            cleanup()
            return problem(
                415,
                "Fotografía no procesable",
                "Alguna fotografía no pudo validarse como imagen segura.",
            )
        except StorageUnavailableError:
            cleanup()
            return problem(
                503,
                "Almacenamiento no disponible",
                "No fue posible resguardar la evidencia; el reporte no "
                "fue registrado.",
            )

        def encrypt_number(value: float | None) -> bytes | None:
            return None if value is None else encrypt(repr(value))

        stored_report = StoredBuildingReport(
            id=report_id,
            public_tracking_code=generate_public_tracking_code(
                received_at
            ),
            # La llave jamás se persiste en claro (CHG-035).
            idempotency_key_hash=hashlib.sha256(
                idempotency_key.encode()
            ).hexdigest(),
            building_reference=payload.building_reference,
            building_type=payload.building_type,
            department=payload.department,
            municipality=payload.municipality,
            sector=payload.sector,
            location_reference_protected=encrypt(
                payload.location_reference
            ),
            address_protected=encrypt(payload.address),
            latitude_protected=encrypt_number(payload.latitude),
            longitude_protected=encrypt_number(payload.longitude),
            related_disaster_id=payload.related_disaster_id,
            observed_date=payload.observed_date,
            observed_time=payload.observed_time,
            search_status=payload.search_status,
            occupancy_report=payload.occupancy_report,
            pending_reasons=list(payload.pending_reasons),
            observed_conditions=list(payload.observed_conditions),
            observation_description_protected=encrypt(
                payload.observation_description
            ),
            reporter_name_protected=encrypt(payload.reporter_name),
            reporter_role_protected=encrypt(payload.reporter_role),
            reporter_organization_protected=encrypt(
                payload.reporter_organization
            ),
            reporter_phone_protected=encrypt(payload.reporter_phone),
            reporter_email_protected=encrypt(payload.reporter_email),
            official_report_number_protected=encrypt(
                payload.official_report_number
            ),
            truth_confirmed_at=received_at,
            photo_authorization_confirmed_at=received_at,
            review_acknowledged_at=received_at,
            legal_text_version=BUILDING_REPORT_LEGAL_TEXT_VERSION,
            actor_account_id=account_id,
            reporter_snapshot_latitude_protected=encrypt_number(
                payload.reporter_latitude
            ),
            reporter_snapshot_longitude_protected=encrypt_number(
                payload.reporter_longitude
            ),
        )

        try:
            receipt, created = (
                await data.create_unverified_building_report(
                    stored_report, stored_files
                )
            )
        except asyncpg.ForeignKeyViolationError:
            cleanup()
            return problem(
                422,
                "Datos inválidos",
                "Revisa los campos: relatedDisasterId.",
            )
        except asyncpg.PostgresError:
            cleanup()
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar el reporte; ningún dato "
                "quedó publicado.",
            )
        if not created:
            # Reintento idempotente: los archivos de este intento sobran.
            cleanup()
        return receipt

    # CHG-044 — Ofertas comunitarias de comida y alojamiento. El
    # gateway resuelve la cuenta y la declara por encabezados internos;
    # aquí se revalida como defensa en profundidad y jamás se acepta un
    # accountId del cliente en ruta, query ni JSON.

    def require_offer_account(
        request: Request,
    ) -> UUID | JSONResponse:
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_kind, account_id = actor
        if actor_kind != "authenticated" or account_id is None:
            return problem(
                401,
                "Sesión requerida",
                "Las ofertas exigen una cuenta autenticada resuelta "
                "por el gateway.",
            )
        return account_id

    def owner_summary_model(row: dict) -> AidOfferOwnerSummary:
        title = decrypt_aid_text(row["title_encrypted"]) or ""
        return AidOfferOwnerSummary(
            id=row["id"],
            tracking_code=row["tracking_code"],
            kind=row["kind"],
            title=(title.strip() or "Oferta comunitaria")[:160],
            moderation_status=row["moderation_status"],
            availability_status=row["availability_status"],
            available_units=row["available_units"],
            capacity_unit=row["capacity_unit"],
            available_from=row["available_from"],
            available_until=row["available_until"],
            received_at=row["received_at"],
            updated_at=row["updated_at"],
            version=row["version"],
        )

    @application.post(
        "/internal/v1/aid-offers",
        status_code=202,
        response_model=AidOfferReceipt,
        response_model_by_alias=True,
        tags=["AidOffers"],
    )
    async def create_aid_offer(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        idempotency_key = validate_idempotency_key(request)
        if isinstance(idempotency_key, JSONResponse):
            return idempotency_key
        account_id = require_offer_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        if aid_offer_fernet is None:
            return aid_offer_key_unavailable()

        body = await request.body()
        try:
            payload = AID_OFFER_INPUT_ADAPTER.validate_json(body)
        except ValidationError as error:
            return invalid_fields_problem(error)

        received_at = datetime.now(UTC)
        if payload.available_until <= received_at:
            return problem(
                422,
                "Vigencia inválida",
                "availableUntil debe estar en el futuro.",
            )

        def encrypt_aid_number(value: float | None) -> bytes | None:
            return None if value is None else encrypt_aid(repr(value))

        offer = StoredAidOffer(
            id=uuid4(),
            tracking_code=offer_rules.generate_tracking_code(
                received_at
            ),
            kind=payload.kind,
            account_id=account_id,
            # La llave jamás se persiste ni registra en claro.
            idempotency_key_hash=offer_rules.idempotency_key_hash(
                idempotency_key
            ),
            request_fingerprint=offer_rules.request_fingerprint(body),
            related_disaster_id=payload.related_disaster_id,
            title_encrypted=encrypt_aid(payload.title),
            description_encrypted=encrypt_aid(payload.description),
            area_reference_encrypted=encrypt_aid(
                payload.area_reference
            ),
            exact_address_encrypted=encrypt_aid(payload.exact_address),
            latitude_encrypted=encrypt_aid_number(payload.latitude),
            longitude_encrypted=encrypt_aid_number(payload.longitude),
            contact_name_encrypted=encrypt_aid(payload.contact_name),
            contact_phone_encrypted=encrypt_aid(payload.contact_phone),
            contact_email_encrypted=encrypt_aid(payload.contact_email),
            department=payload.department,
            municipality=payload.municipality,
            available_from=payload.available_from,
            available_until=payload.available_until,
            consent_recorded_at=received_at,
            legal_text_version=(
                offer_rules.AID_OFFER_LEGAL_TEXT_VERSION
            ),
        )
        meal: StoredMealOfferDetails | None = None
        shelter: StoredShelterOfferDetails | None = None
        if payload.kind == "community_meal":
            meal = StoredMealOfferDetails(
                servings_available=payload.servings_available,
                distribution_mode=payload.distribution_mode,
                meal_description_encrypted=encrypt_aid(
                    payload.meal_description
                ),
                allergen_information_encrypted=encrypt_aid(
                    payload.allergen_information
                ),
            )
        else:
            shelter = StoredShelterOfferDetails(
                spaces_available=payload.spaces_available,
                shared_space=payload.shared_space,
                accepts_pets=payload.accepts_pets,
                accessibility_notes_encrypted=encrypt_aid(
                    payload.accessibility_notes
                ),
            )

        try:
            receipt, _created = await data.create_aid_offer(
                offer, meal, shelter
            )
        except AidOfferIdempotencyConflictError:
            return problem(
                409,
                "Idempotencia incompatible",
                "La Idempotency-Key ya se usó con un contenido "
                "distinto.",
            )
        except asyncpg.ForeignKeyViolationError:
            return problem(
                422,
                "Datos inválidos",
                "Revisa los campos: relatedDisasterId.",
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar la oferta; ningún dato "
                "quedó guardado.",
            )
        return receipt

    @application.get(
        "/internal/v1/aid-offers",
        response_model=AidOfferOwnerPage,
        response_model_by_alias=True,
        tags=["AidOffers"],
    )
    async def list_owner_aid_offers(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
        kind: Annotated[AidOfferKind | None, Query()] = None,
        moderation_status: Annotated[
            AidOfferModerationStatus | None,
            Query(alias="moderationStatus"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        account_id = require_offer_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        if limit not in (10, 25, 50):
            return problem(
                422,
                "Paginación inválida",
                "El tamaño de página debe ser 10, 25 o 50.",
            )
        rows, total = await data.list_owner_aid_offers(
            account_id, kind, moderation_status, limit, offset
        )
        return AidOfferOwnerPage(
            items=[owner_summary_model(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
            generated_at=datetime.now(UTC),
        )

    @application.patch(
        "/internal/v1/aid-offers/{offer_id}",
        response_model=AidOfferOwnerSummary,
        response_model_by_alias=True,
        tags=["AidOffers"],
    )
    async def update_owner_aid_offer(
        offer_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = require_offer_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        try:
            payload = AidOfferOwnerUpdateInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        try:
            outcome, row = await data.update_owner_aid_offer(
                account_id,
                offer_id,
                payload.version,
                payload.availability_status,
                payload.available_units,
                payload.available_from,
                payload.available_until,
            )
        except offer_rules.OwnerTransitionError as error:
            return problem(409, "Transición inválida", str(error))
        except offer_rules.OwnerUpdateInvalidError as error:
            return problem(422, "Actualización inválida", str(error))
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible actualizar la oferta.",
            )
        if outcome == "not_found":
            # Oferta ajena e inexistente son indistinguibles.
            return problem(
                404,
                "Oferta no disponible",
                "La oferta no existe o no pertenece a la cuenta.",
            )
        if outcome == "version_conflict":
            return problem(
                409,
                "Conflicto de versión",
                "La oferta cambió; recarga y reintenta con la versión "
                "vigente.",
            )
        return owner_summary_model(row)

    @application.post(
        "/internal/v1/aid-offers/expirations",
        tags=["AidOffers"],
    )
    async def expire_aid_offers(
        data: Annotated[DisasterRepository, Depends(get_repository)],
        batch_size: Annotated[
            int, Query(alias="batchSize", ge=1, le=500)
        ] = 100,
    ):
        # Proceso idempotente por lotes (FOR UPDATE SKIP LOCKED): una
        # segunda ejecución no duplica auditoría ni versión.
        try:
            expired = await data.expire_aid_offers(batch_size)
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible procesar las expiraciones.",
            )
        return JSONResponse(content={"expired": expired})

    # CHG-066 — Presencia de visitantes con consentimiento explícito.
    # Solo usuarios REGISTRADOS reportan en vivo; la lectura es
    # EXCLUSIVA de la consola super_admin.

    @application.post(
        "/internal/v1/presence",
        status_code=202,
        response_model=VisitorPresenceReceipt,
        response_model_by_alias=True,
        tags=["Presence"],
    )
    async def report_visitor_presence(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        # CHG-066: la ubicación EN VIVO es solo de usuarios registrados;
        # los anónimos solo dejan la instantánea adjunta a sus reportes.
        account_id = require_offer_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        try:
            payload = VisitorPresenceInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        try:
            await data.upsert_visitor_presence(
                payload.presence_id,
                account_id,
                payload.latitude,
                payload.longitude,
                payload.accuracy_meters,
                payload.platform,
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar la presencia.",
            )
        return VisitorPresenceReceipt(status="accepted")

    PRESENCE_WINDOW_MINUTES = 30

    @application.get(
        "/internal/v1/admin/visitor-presence",
        response_model=AdminVisitorPresencePage,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_list_visitor_presence(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
        limit: Annotated[int, Query(ge=1, le=200)] = 200,
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        rows, total = await data.list_visitor_presence(
            PRESENCE_WINDOW_MINUTES, limit
        )
        return AdminVisitorPresencePage(
            items=[
                AdminVisitorPresence(
                    presence_id=row["presence_id"],
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                    accuracy_meters=row["accuracy_meters"],
                    platform=row["platform"],
                    authenticated=row["account_id"] is not None,
                    first_seen_at=row["first_seen_at"],
                    updated_at=row["updated_at"],
                )
                for row in rows
            ],
            total=total,
            window_minutes=PRESENCE_WINDOW_MINUTES,
            generated_at=datetime.now(UTC),
        )

    # CHG-036 — Consola de superadministración (rutas internas).
    # El gateway es quien autentica la cookie; aquí se revalida el rol
    # recibido por encabezados internos como defensa en profundidad.

    def decrypt_text(value) -> str | None:
        if value is None:
            return None
        try:
            return fernet.decrypt(bytes(value)).decode()
        except Exception:
            # Ciframiento ilegible (p. ej. datos semilla): jamás romper
            # el detalle ni filtrar bytes crudos.
            return "[contenido protegido no legible]"

    def decrypt_aid_text(value) -> str | None:
        # CHG-044: clave exclusiva de ofertas, solo para la consola.
        if value is None:
            return None
        if aid_offer_fernet is None:
            return "[clave de ofertas no disponible]"
        try:
            return aid_offer_fernet.decrypt(bytes(value)).decode()
        except Exception:
            return "[contenido protegido no legible]"

    def admin_actor(
        request: Request,
    ) -> tuple[UUID, str] | JSONResponse:
        role = request.headers.get("x-actor-role", "").strip()
        if role != "super_admin":
            return problem(
                403,
                "Rol insuficiente",
                "La operación exige rol super_admin.",
            )
        try:
            account_id = UUID(
                request.headers.get("x-actor-account-id", "").strip()
            )
        except ValueError:
            return problem(
                403, "Actor inválido", "Actor administrativo ausente."
            )
        display_raw = request.headers.get("x-actor-display", "").strip()
        try:
            display_name = base64.b64decode(
                display_raw.encode()
            ).decode() or "Superadministración"
        except Exception:
            display_name = "Superadministración"
        return account_id, display_name[:161]

    def summary_model(row: dict) -> AdminSubmissionSummary:
        return AdminSubmissionSummary(
            id=row["id"],
            kind=row["kind"],
            tracking_code=row["tracking_code"],
            title=row["title"],
            location_label=row["location_label"],
            status=row["admin_status"],
            source_label=row["source_label"],
            evidence_count=min(row["evidence_count"], 20),
            received_at=row["received_at"],
            updated_at=row["updated_at"],
            version=row["version"],
        )

    def build_admin_fields(kind: str, row: dict) -> list[AdminField]:
        editable_map = admin_rules.EDITABLE_FIELDS.get(kind, {})
        fields: list[AdminField] = []
        for key, label, column, classification, transform, input_kind in (
            ADMIN_FIELD_SPECS[kind]
        ):
            value = row.get(column)
            if transform == "decrypt":
                value = decrypt_text(value)
            elif transform == "decrypt_aid":
                value = decrypt_aid_text(value)
            elif transform == "list" and value is not None:
                value = ", ".join(value)
            display = "" if value is None else str(value)
            editable = key in editable_map
            fields.append(
                AdminField(
                    key=key,
                    label=label,
                    display_value=display[:4000],
                    edit_value=display[:4000] if editable else None,
                    classification=classification,
                    editable=editable,
                    input_kind=(
                        editable_map[key].input_kind
                        if editable
                        else input_kind
                    ),
                    options=[],
                )
            )
        return fields

    def evidence_models(evidence_rows: list[dict]) -> list[AdminEvidence]:
        items = []
        for row in evidence_rows[:20]:
            items.append(
                AdminEvidence(
                    id=row["id"],
                    media_type=row["content_type"],
                    size_bytes=row["size_bytes"],
                    scan_status=(
                        "safe"
                        if row["malware_scan"] == "clean"
                        else "rejected"
                    ),
                    created_at=row["created_at"],
                )
            )
        return items

    async def build_submission_detail(
        data: DisasterRepository, submission_id: UUID
    ) -> AdminSubmissionDetail | None:
        summary_row = await data.admin_get_submission_summary(
            submission_id
        )
        found = await data.admin_get_submission(submission_id)
        if summary_row is None or found is None:
            return None
        kind, row, evidence_rows = found
        summary = summary_model(summary_row)
        return AdminSubmissionDetail(
            **summary.model_dump(by_alias=False),
            fields=build_admin_fields(kind, row),
            evidence=evidence_models(evidence_rows),
            available_actions=admin_rules.available_actions(
                summary_row["domain_status"],
                summary_row["needs_information"],
                summary_row["archived_at"],
            ),
        )

    def version_conflict() -> JSONResponse:
        return problem(
            409,
            "Conflicto de versión",
            "El expediente cambió; recarga y reintenta con la "
            "versión vigente.",
        )

    def submission_not_found() -> JSONResponse:
        return problem(
            404,
            "Expediente no disponible",
            "El expediente no existe o no es visible.",
        )

    @application.get(
        "/internal/v1/admin/submissions-overview",
        tags=["Administration"],
    )
    async def admin_submissions_overview(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        overview = await data.admin_submissions_overview()
        by_status: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        oldest_pending = None
        for row in overview["counts"]:
            by_status[row["admin_status"]] = (
                by_status.get(row["admin_status"], 0) + row["quantity"]
            )
            if row["admin_status"] in (
                "under_review", "needs_information"
            ):
                by_kind[row["kind"]] = (
                    by_kind.get(row["kind"], 0) + row["quantity"]
                )
                if oldest_pending is None or (
                    row["oldest"] < oldest_pending
                ):
                    oldest_pending = row["oldest"]
        return JSONResponse(
            content={
                "underReview": by_status.get("under_review", 0),
                "needsInformation": by_status.get(
                    "needs_information", 0
                ),
                "acceptedToday": overview["accepted_today"],
                "archived": by_status.get("archived", 0),
                "oldestPendingAt": (
                    oldest_pending.isoformat()
                    if oldest_pending is not None
                    else None
                ),
                "byKind": [
                    {"kind": kind, "count": count}
                    for kind, count in sorted(by_kind.items())
                ],
                "recentActivity": [
                    {
                        "id": str(item["id"]),
                        "action": item["action"],
                        "resourceKind": item["resource_kind"],
                        "occurredAt": item["occurred_at"].isoformat(),
                        "result": item["result"],
                    }
                    for item in overview["recent_activity"]
                ],
                "generatedAt": datetime.now(UTC).isoformat(),
            }
        )

    @application.get(
        "/internal/v1/admin/submissions",
        response_model=AdminSubmissionPage,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_list_submissions(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
        q: Annotated[
            str | None, Query(min_length=2, max_length=100)
        ] = None,
        kind: Annotated[AdminSubmissionKind | None, Query()] = None,
        status: Annotated[AdminModerationStatus | None, Query()] = None,
        received_from: Annotated[
            datetime | None, Query(alias="receivedFrom")
        ] = None,
        received_to: Annotated[
            datetime | None, Query(alias="receivedTo")
        ] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        if limit not in (10, 25, 50):
            return problem(
                422,
                "Tamaño de página inválido",
                "El tamaño de página debe ser 10, 25 o 50.",
            )
        rows, total = await data.admin_list_submissions(
            q, kind, status, received_from, received_to, limit, offset
        )
        return AdminSubmissionPage(
            items=[summary_model(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
            generated_at=datetime.now(UTC),
        )

    @application.get(
        "/internal/v1/admin/submissions/{submission_id}",
        response_model=AdminSubmissionDetail,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_get_submission(
        submission_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        detail = await build_submission_detail(data, submission_id)
        if detail is None:
            return submission_not_found()
        return detail

    async def apply_submission_mutation(
        request: Request,
        data: DisasterRepository,
        submission_id: UUID,
        expected_version: int,
        action: str,
        reason: str,
        allowed_action: str,
        columns: dict[str, object] | None = None,
    ):
        """Valida transición y aplica la mutación auditada."""
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display = actor
        summary_row = await data.admin_get_submission_summary(
            submission_id
        )
        if summary_row is None:
            return submission_not_found()
        if action != "edit":
            legal = admin_rules.available_actions(
                summary_row["domain_status"],
                summary_row["needs_information"],
                summary_row["archived_at"],
            )
            if allowed_action not in legal:
                return problem(
                    409,
                    "Transición inválida",
                    "La acción no es válida desde el estado actual.",
                )
        elif summary_row["archived_at"] is not None:
            return problem(
                409,
                "Transición inválida",
                "Un expediente archivado no se edita; restáuralo antes.",
            )
        # CHG-044: aceptar (y por tanto publicar) una oferta permanece
        # BLOQUEADO hasta resolver DEC-020 y DEC-021.
        if action == "accept" and summary_row["kind"] in (
            "community_meal_offer",
            "temporary_shelter_offer",
        ):
            return problem(
                409,
                "Aceptación bloqueada",
                "La aceptación de ofertas comunitarias está bloqueada "
                "hasta resolver DEC-020 y DEC-021.",
            )
        outcome, audit_event_id, new_version = (
            await data.admin_mutate_submission(
                summary_row["kind"],
                submission_id,
                expected_version,
                action,
                actor_account_id,
                actor_display,
                encrypt(reason),
                columns=columns,
                correlation_id=uuid4(),
            )
        )
        if outcome == "not_found":
            return submission_not_found()
        if outcome == "conflict":
            return version_conflict()
        # CHG-054: los envíos hechos con cuenta reciben la novedad por
        # correo. Mejor esfuerzo tras confirmar la transacción: un fallo
        # del aviso jamás revierte ni bloquea la decisión.
        status_label = notifications.STATUS_LABELS.get(action)
        account_id = summary_row.get("account_id")
        if status_label and account_id is not None:
            try:
                await report_notifier.notify_report_status(
                    account_id,
                    notifications.REPORT_LABELS.get(
                        summary_row["kind"], "Reporte"
                    ),
                    summary_row["tracking_code"],
                    status_label,
                )
            except Exception:
                pass
        return audit_event_id, new_version

    async def mutation_receipt(
        data: DisasterRepository,
        submission_id: UUID,
        audit_event_id: UUID,
    ) -> AdminMutationReceipt:
        summary_row = await data.admin_get_submission_summary(
            submission_id
        )
        return AdminMutationReceipt(
            id=submission_id,
            status=summary_row["admin_status"],
            version=summary_row["version"],
            audit_event_id=audit_event_id,
            updated_at=summary_row["updated_at"],
        )

    @application.patch(
        "/internal/v1/admin/submissions/{submission_id}",
        response_model=AdminSubmissionDetail,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_edit_submission(
        submission_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        try:
            payload = AdminSubmissionEditInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        summary_row = await data.admin_get_submission_summary(
            submission_id
        )
        if summary_row is None:
            return submission_not_found()
        changes = {
            change.field: change.value for change in payload.changes
        }
        if len(changes) != len(payload.changes):
            return problem(
                422, "Cambios inválidos", "Hay campos repetidos."
            )
        try:
            columns = admin_rules.validate_changes(
                summary_row["kind"], changes
            )
        except admin_rules.AdminEditError as error:
            return problem(422, "Cambios inválidos", str(error))
        result = await apply_submission_mutation(
            request,
            data,
            submission_id,
            payload.expected_version,
            "edit",
            payload.reason,
            "edit",
            columns=columns,
        )
        if isinstance(result, JSONResponse):
            return result
        return await build_submission_detail(data, submission_id)

    @application.post(
        "/internal/v1/admin/submissions/{submission_id}/decisions",
        response_model=AdminMutationReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_decide_submission(
        submission_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        try:
            payload = AdminDecisionInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        result = await apply_submission_mutation(
            request,
            data,
            submission_id,
            payload.expected_version,
            payload.action,
            payload.reason,
            payload.action,
        )
        if isinstance(result, JSONResponse):
            return result
        audit_event_id, _version = result
        return await mutation_receipt(
            data, submission_id, audit_event_id
        )

    @application.delete(
        "/internal/v1/admin/submissions/{submission_id}",
        response_model=AdminMutationReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_archive_submission(
        submission_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        try:
            payload = AdminVersionedReasonInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        result = await apply_submission_mutation(
            request,
            data,
            submission_id,
            payload.expected_version,
            "archive",
            payload.reason,
            "archive",
        )
        if isinstance(result, JSONResponse):
            return result
        audit_event_id, _version = result
        return await mutation_receipt(
            data, submission_id, audit_event_id
        )

    @application.post(
        "/internal/v1/admin/submissions/{submission_id}/restore",
        response_model=AdminMutationReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_restore_submission(
        submission_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        try:
            payload = AdminVersionedReasonInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        result = await apply_submission_mutation(
            request,
            data,
            submission_id,
            payload.expected_version,
            "restore",
            payload.reason,
            "restore",
        )
        if isinstance(result, JSONResponse):
            return result
        audit_event_id, _version = result
        return await mutation_receipt(
            data, submission_id, audit_event_id
        )

    @application.post(
        "/internal/v1/admin/submissions/{submission_id}"
        "/evidence/{evidence_id}/access-grants",
        status_code=201,
        response_model=AdminEvidenceAccessGrant,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_grant_evidence_access(
        submission_id: UUID,
        evidence_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display = actor
        evidence = await data.admin_get_evidence(
            submission_id, evidence_id
        )
        if evidence is None:
            return submission_not_found()
        if evidence["malware_scan"] != "clean":
            await data.admin_write_audit(
                actor_account_id,
                actor_display,
                "evidence_access_granted",
                "evidence",
                evidence_id,
                "denied",
            )
            return problem(
                404,
                "Evidencia no disponible",
                "La evidencia no superó el análisis de seguridad.",
            )
        ttl = min(
            resolved_settings.evidence_grant_ttl_seconds,
            admin_rules.EVIDENCE_GRANT_MAX_TTL_SECONDS,
        )
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
        token = admin_rules.make_evidence_grant_token(
            resolved_settings.report_encryption_key,
            submission_id,
            evidence_id,
            actor_account_id,
            expires_at,
        )
        audit_event_id = await data.admin_write_audit(
            actor_account_id,
            actor_display,
            "evidence_access_granted",
            "evidence",
            evidence_id,
            "success",
        )
        return AdminEvidenceAccessGrant(
            # Ruta mediada por el gateway; sin nombre original y con
            # vencimiento ≤ 300 s. Apunta al derivado sin EXIF.
            url=f"/api/v1/admin/evidence-access/{token}",
            expires_at=expires_at,
            audit_event_id=audit_event_id,
        )

    @application.get(
        "/internal/v1/admin/evidence-access/{token}",
        tags=["Administration"],
    )
    async def admin_serve_evidence(
        token: str,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display = actor
        grant = admin_rules.parse_evidence_grant_token(
            resolved_settings.report_encryption_key, token
        )
        if grant is None or grant.actor_account_id != actor_account_id:
            return problem(
                404,
                "Acceso no disponible",
                "El acceso es inválido, venció o no corresponde al "
                "actor.",
            )
        evidence = await data.admin_get_evidence(
            grant.submission_id, grant.evidence_id
        )
        if evidence is None or evidence["malware_scan"] != "clean":
            return submission_not_found()
        try:
            content = object_storage.load(evidence["derived_key"])
        except StorageUnavailableError:
            content = None
        result = "success" if content is not None else "failed"
        await data.admin_write_audit(
            actor_account_id,
            actor_display,
            "evidence_access_served",
            "evidence",
            grant.evidence_id,
            result,
        )
        if content is None:
            return problem(
                503,
                "Evidencia no disponible",
                "No fue posible leer la evidencia en este momento.",
            )
        from fastapi.responses import Response as RawResponse

        return RawResponse(
            content=content,
            media_type=evidence["content_type"],
            headers={"Cache-Control": "no-store, private"},
        )

    @application.get(
        "/internal/v1/admin/audit-events",
        response_model=AdminAuditPage,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_list_audit_events(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
        q: Annotated[
            str | None, Query(min_length=2, max_length=100)
        ] = None,
        action: Annotated[str | None, Query(max_length=80)] = None,
        result: Annotated[
            Literal["success", "denied", "failed"] | None, Query()
        ] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        if limit not in (10, 25, 50):
            return problem(
                422,
                "Tamaño de página inválido",
                "El tamaño de página debe ser 10, 25 o 50.",
            )
        rows, total = await data.admin_list_audit_events(
            q, action, result, limit, offset
        )
        return AdminAuditPage(
            items=[
                AdminAuditEvent(
                    id=row["id"],
                    actor_account_id=row["actor_account_id"],
                    actor_display_name=row["actor_display_name"],
                    action=row["action"],
                    resource_kind=row["resource_kind"],
                    resource_id=row["resource_id"],
                    result=row["result"],
                    reason_summary=(
                        decrypt_text(row["reason_protected"])
                        if row["reason_protected"] is not None
                        else None
                    ),
                    occurred_at=row["occurred_at"],
                )
                for row in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
            generated_at=datetime.now(UTC),
        )

    return application


app = create_app()
