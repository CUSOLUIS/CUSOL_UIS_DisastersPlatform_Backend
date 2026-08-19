from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


VerificationStatus = Literal[
    "unverified", "under_review", "verified", "rejected"
]
SourceType = Literal[
    "official", "citizen", "sensor", "integration", "ai_inference"
]


def to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class SourceReference(ApiModel):
    name: str = Field(min_length=1)
    source_type: SourceType
    url: str | None = None


class DisasterEvent(ApiModel):
    id: UUID
    title: str = Field(min_length=1)
    description: str | None = None
    disaster_type: str = Field(min_length=1)
    severity: str | None = None
    verification_status: VerificationStatus
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    occurred_at: datetime | None = None
    updated_at: datetime
    source: SourceReference


class DisasterEventList(ApiModel):
    items: list[DisasterEvent]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


HumanStatus = Literal[
    "missing", "reported_deceased", "confirmed_alive", "confirmed_deceased"
]


class HumanImpactSummary(ApiModel):
    missing: int = Field(ge=0)
    reported_deceased: int = Field(ge=0)
    confirmed_alive: int = Field(ge=0)
    confirmed_deceased: int = Field(ge=0)


class PersonRecord(ApiModel):
    id: UUID
    display_name: str = Field(min_length=1)
    status: HumanStatus
    location: str = Field(min_length=1)
    related_event: str = Field(min_length=1)
    source: SourceReference
    created_at: datetime


class HumanImpactOverview(ApiModel):
    summary: HumanImpactSummary
    recent_people: list[PersonRecord] = Field(max_length=50)
    generated_at: datetime


class PeopleRecordPage(ApiModel):
    """Página de registros públicos (CHG-018): solo PersonRecord."""

    items: list[PersonRecord] = Field(max_length=50)
    total: int = Field(ge=0)
    limit: Literal[10, 25, 50]
    offset: int = Field(ge=0)
    generated_at: datetime


OperationalMapCategory = Literal[
    "missing_person",
    "collection_center",
    # CHG-049 — puntos de recolección y ofertas comunitarias.
    "collection_point",
    "rubble_reviewed",
    "rubble_pending",
    "building_pending",
    "community_meal",
    "temporary_shelter",
    # CHG-069 — alertas ciudadanas de voluntariado.
    "volunteers_needed",
    # CHG-153 — logística humanitaria.
    "receiver_center",
    "distribution_point",
    # CHG-162 — hogares en malas condiciones.
    "damaged_home",
]
CoordinatePrecision = Literal["exact", "approximate", "municipality"]
DataClassification = Literal["demonstrative", "operational"]


# CHG-015 — Capa geográfica de situación humana (espejo del contrato).
HumanMapPrecision = Literal["approximate", "municipality"]


class HumanMapStatusCounts(ApiModel):
    missing: int = Field(ge=0)
    reported_deceased: int = Field(ge=0)
    confirmed_alive: int = Field(ge=0)
    confirmed_deceased: int = Field(ge=0)


class HumanMapBounds(ApiModel):
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)


class HumanMapCluster(ApiModel):
    kind: Literal["cluster"] = "cluster"
    id: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    count: int = Field(ge=2)
    status_counts: HumanMapStatusCounts
    bounds: HumanMapBounds


class HumanMapPoint(ApiModel):
    kind: Literal["point"] = "point"
    id: UUID
    status: HumanStatus
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    coordinate_precision: HumanMapPrecision
    verification_status: VerificationStatus
    source: SourceReference
    updated_at: datetime


class HumanMapOverview(ApiModel):
    features: list[HumanMapCluster | HumanMapPoint] = Field(max_length=500)
    total_matched: int = Field(ge=0)
    total_mapped: int = Field(ge=0)
    unmapped_count: int = Field(ge=0)
    # CHG-099: desglose por estado de quienes no se pueden dibujar.
    unmapped_status_counts: HumanMapStatusCounts
    returned_features: int = Field(ge=0)
    next_cursor: str | None = None
    generated_at: datetime
    data_classification: DataClassification


class OperationalMapPoint(ApiModel):
    id: UUID
    category: OperationalMapCategory
    title: str = Field(min_length=1)
    location_label: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    coordinate_precision: CoordinatePrecision
    verification_status: VerificationStatus
    related_disaster_id: UUID | None = None
    description: str | None = None
    source: SourceReference
    updated_at: datetime
    # CHG-166: promedio de estrellas de los comentarios del punto.
    comment_rating_average: float | None = Field(default=None, ge=1, le=5)
    comment_rating_count: int = Field(default=0, ge=0)


class OperationalMapSummary(ApiModel):
    missing_person: int = Field(ge=0)
    collection_center: int = Field(ge=0)
    rubble_reviewed: int = Field(ge=0)
    rubble_pending: int = Field(ge=0)
    # CHG-010: default 0 solo tolera upstream anterior durante el
    # despliegue coordinado; la respuesta del gateway siempre lo emite.
    building_pending: int = Field(ge=0, default=0)
    # CHG-049: nuevas categorías comunitarias, mismo criterio.
    collection_point: int = Field(ge=0, default=0)
    community_meal: int = Field(ge=0, default=0)
    temporary_shelter: int = Field(ge=0, default=0)
    # CHG-069: alertas de voluntariado activas.
    volunteers_needed: int = Field(ge=0, default=0)
    # CHG-153: logística (contadores del backend).
    receiver_center: int = Field(ge=0, default=0)
    distribution_point: int = Field(ge=0, default=0)
    # CHG-162: hogares en malas condiciones.
    damaged_home: int = Field(ge=0, default=0)


class OperationalMapOverview(ApiModel):
    summary: OperationalMapSummary
    items: list[OperationalMapPoint] = Field(max_length=500)
    generated_at: datetime
    data_classification: DataClassification


class MissingPersonPublicRecord(ApiModel):
    id: UUID
    public_case_code: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    aliases: list[str] = Field(max_length=10)
    approximate_age: int | None = Field(default=None, ge=0, le=120)
    last_seen_at: datetime
    last_seen_area: str = Field(min_length=1)
    municipality: str = Field(min_length=1)
    department: str = Field(min_length=1)
    clothing_description: str | None = None
    physical_description: str | None = None
    distinctive_marks: str | None = None
    public_photo_url: str | None = None
    map_point_id: UUID | None = None
    updated_at: datetime
    data_classification: DataClassification


# CHG-092 — Autocompletado creable de "Evento relacionado".
class DisasterEventSuggestion(ApiModel):
    id: UUID
    title: str = Field(min_length=1)
    disaster_type: str = Field(min_length=1)
    verification_status: VerificationStatus
    occurred_at: datetime | None = None
    similarity: float = Field(ge=0.0, le=1.0)


class DisasterEventAutocompleteResponse(ApiModel):
    items: list[DisasterEventSuggestion] = Field(max_length=10)
    query: str = Field(min_length=2, max_length=160)
    generated_at: datetime


class MissingPersonSearchResponse(ApiModel):
    items: list[MissingPersonPublicRecord] = Field(max_length=20)
    total: int = Field(ge=0)
    query: str


class MissingPersonReportReceipt(ApiModel):
    # CHG-075: el caso público se crea junto con el reporte, así que
    # la constancia informa `published`.
    id: UUID
    public_case_code: str = Field(min_length=1)
    status: Literal["published"]
    received_at: datetime


# CHG-022 — Autenticación (espejo del contrato; el token de sesión nunca
# integra estas respuestas públicas).
class AccountRegistrationReceipt(ApiModel):
    request_id: UUID
    status: Literal["email_verification_required"]
    email_masked: str = Field(min_length=3)
    verification_expires_at: datetime
    assigned_role: Literal["user"]
    created_at: datetime


class AuthenticatedAccount(ApiModel):
    # CHG-077: `is_health_sector` viaja desde identity para que el
    # gateway declare la bandera al registrar novedades.
    id: UUID
    display_name: str = Field(min_length=1, max_length=161)
    email: str = Field(max_length=254)
    assigned_role: Literal["user", "moderator", "super_admin"]
    status: Literal["active"]
    session_expires_at: datetime
    is_health_sector: bool = False
    # CHG-083: teléfono del perfil para precargar formularios.
    phone: str | None = None


class SessionEnvelope(ApiModel):
    """Respuesta interna del identity-service; el gateway convierte el
    token en cookie `cusol_session` y nunca lo reenvía en el cuerpo."""

    account: AuthenticatedAccount
    session_token: str
    session_expires_at: datetime


class EmailVerificationEnvelope(ApiModel):
    """CHG-051: respuesta interna de la verificación; incluye la sesión
    de bienvenida que el gateway convierte en cookie."""

    status: Literal["active"]
    verified_at: datetime
    account: AuthenticatedAccount
    session_token: str
    session_expires_at: datetime


class EmailVerificationReceipt(ApiModel):
    """Respuesta pública: la cuenta queda activa y con sesión iniciada
    (cookie); el token de sesión jamás viaja en el cuerpo."""

    status: Literal["active"]
    verified_at: datetime
    account: AuthenticatedAccount


# CHG-034 — Directorio humanitario y aportes (espejo del contrato).
# CHG-044 amplía el directorio con ofertas comunitarias.
HumanitarianDirectoryKind = Literal[
    "missing_person",
    "collection_center",
    "collection_point",
    "community_meal",
    "temporary_shelter",
]
PublicPersonStatus = Literal["missing", "found", "deceased"]
AidLocationAvailability = Literal["active", "inactive", "unknown"]
AidSupplyCategory = Literal[
    "water", "food", "medicine", "clothing", "tools", "shelter", "other"
]


class MissingPersonDirectoryCard(ApiModel):
    kind: Literal["missing_person"] = "missing_person"
    id: UUID
    public_case_code: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    status: PublicPersonStatus
    approximate_age: int | None = Field(default=None, ge=0, le=120)
    last_seen_at: datetime
    last_seen_area: str = Field(min_length=1)
    municipality: str = Field(min_length=1)
    department: str = Field(min_length=1)
    public_photo_url: str | None = None
    source: SourceReference
    updated_at: datetime
    data_classification: DataClassification


# CHG-153 — logística humanitaria (espejo del contrato).
AidLocationKind = Literal[
    "collection_center",
    "collection_point",
    "receiver_center",
    "distribution_point",
]
AidLocationOperationalStatus = Literal[
    "open", "closed", "at_capacity", "under_observation", "inactive"
]


class AidLocationDirectoryCard(ApiModel):
    kind: AidLocationKind
    id: UUID
    name: str = Field(min_length=1, max_length=180)
    location_label: str = Field(min_length=1, max_length=300)
    municipality: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=100)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    operational_status: AidLocationOperationalStatus = "open"
    parent_id: UUID | None = None
    verification_status: VerificationStatus
    availability_status: AidLocationAvailability
    open_now: bool | None = None
    accepted_supplies: list[AidSupplyCategory] = Field(max_length=12)
    average_rating: float | None = Field(default=None, ge=1, le=5)
    ratings_count: int = Field(ge=0)
    source: SourceReference
    updated_at: datetime
    data_classification: DataClassification


# CHG-153 — Recibos de alta y de denuncia (espejo del contrato).
class AidLocationReceipt(ApiModel):
    id: UUID
    kind: AidLocationKind
    operational_status: AidLocationOperationalStatus
    created_at: datetime


class AidLocationReportReceipt(ApiModel):
    location_id: UUID
    reports_count: int = Field(ge=1)
    under_observation: bool
    # CHG-165: el centro quedó (o ya estaba) deshabilitado por el
    # umbral de 20 denuncias.
    disabled: bool = False


# CHG-165 — Comentarios públicos de un Centro de Acopio Local
# (espejo del contrato). author_display_name NULL = «Anónimo».
class AidLocationComment(ApiModel):
    id: UUID
    author_display_name: str | None = None
    actor_kind: Literal["anonymous", "authenticated"]
    content: str = Field(min_length=1, max_length=1000)
    # CHG-166: None en comentarios previos a la mejora.
    rating: int | None = Field(default=None, ge=1, le=5)
    created_at: datetime


class AidLocationCommentsResponse(ApiModel):
    items: list[AidLocationComment]
    total: int = Field(ge=0)
    # CHG-166: promedio (1 decimal) y cuántos calificaron.
    rating_average: float | None = Field(default=None, ge=1, le=5)
    rating_count: int = Field(default=0, ge=0)


# CHG-165 — Consola super_admin: verificación/reactivación de acopios
# locales (espejo del contrato).
class AdminAidLocationSummary(ApiModel):
    id: UUID
    kind: AidLocationKind
    name: str
    location_label: str
    municipality: str
    department: str
    latitude: float | None = None
    longitude: float | None = None
    description: str | None = None
    schedule: str | None = None
    contact: str | None = None
    created_at: datetime
    created_by_account_id: UUID | None = None
    verification_status: VerificationStatus
    operational_status: AidLocationOperationalStatus
    disabled_at: datetime | None = None
    verified_at: datetime | None = None
    active_reports_count: int = Field(ge=0)


class AdminAidLocationVerificationsResponse(ApiModel):
    pending: list[AdminAidLocationSummary]
    disabled: list[AdminAidLocationSummary]


class AdminAidLocationActionReceipt(ApiModel):
    id: UUID
    verification_status: VerificationStatus
    operational_status: AidLocationOperationalStatus
    disabled_at: datetime | None = None
    active_reports_count: int = Field(ge=0)


# CHG-167 — Borrado admin de un comentario (definitivo y auditado).
# CHG-176 — Comunidad de las ofertas de comida (espejo).
class FoodOfferReportReceipt(ApiModel):
    food_offer_id: UUID
    reports_count: int = Field(ge=1)
    under_observation: bool
    disabled: bool = False


class FoodOfferDeleteReceipt(ApiModel):
    deleted: int = Field(ge=0)


class AidLocationCommentDeleteReceipt(ApiModel):
    deleted: int = Field(ge=0)


# CHG-170 — Borrado admin del acopio completo desde su ficha.
class AdminAidLocationDeleteReceipt(ApiModel):
    deleted: int = Field(ge=0)


# CHG-153 — Candidatos a centro asociado (espejo del contrato).
class AidLocationParentCandidate(ApiModel):
    id: UUID
    name: str = Field(min_length=1, max_length=180)
    address: str = Field(min_length=1, max_length=300)
    municipality: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=100)
    operational_status: AidLocationOperationalStatus


class AidLocationParentCandidatesResponse(ApiModel):
    items: list[AidLocationParentCandidate]
    total: int = Field(ge=0)


# CHG-044 — Ofertas comunitarias (espejo del contrato).
AidOfferKind = Literal["community_meal", "temporary_shelter"]
AidOfferAvailability = Literal[
    "scheduled", "active", "paused", "fulfilled", "withdrawn", "expired"
]
AidOfferModerationStatus = Literal[
    "under_review", "needs_information", "accepted", "rejected", "archived"
]
AidOfferCapacityUnit = Literal["servings", "spaces"]
MealDistributionMode = Literal["pickup", "delivery", "both"]


class AidOfferReceipt(ApiModel):
    id: UUID
    tracking_code: str = Field(min_length=1, max_length=40)
    kind: AidOfferKind
    moderation_status: Literal["under_review"]
    availability_status: Literal["scheduled"]
    received_at: datetime
    version: int = Field(ge=1)


class AidOfferOwnerSummary(ApiModel):
    id: UUID
    tracking_code: str = Field(min_length=1, max_length=40)
    kind: AidOfferKind
    title: str = Field(min_length=1, max_length=160)
    moderation_status: AidOfferModerationStatus
    availability_status: AidOfferAvailability
    available_units: int = Field(ge=0, le=100_000)
    capacity_unit: AidOfferCapacityUnit
    available_from: datetime
    available_until: datetime
    received_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class AidOfferOwnerPage(ApiModel):
    items: list[AidOfferOwnerSummary] = Field(max_length=50)
    total: int = Field(ge=0)
    limit: Literal[10, 25, 50]
    offset: int = Field(ge=0)
    generated_at: datetime


class CommunityMealOfferDirectoryCard(ApiModel):
    kind: Literal["community_meal"] = "community_meal"
    id: UUID
    public_offer_code: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=1, max_length=1200)
    area_reference: str = Field(min_length=1, max_length=300)
    municipality: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=100)
    availability_status: Literal["active"]
    available_from: datetime
    available_until: datetime
    servings_available: int = Field(ge=1, le=100_000)
    distribution_mode: MealDistributionMode
    meal_description: str = Field(min_length=1, max_length=500)
    allergen_information: str | None = Field(default=None, max_length=500)
    verification_status: Literal["verified"]
    source: SourceReference
    updated_at: datetime
    data_classification: DataClassification


class TemporaryShelterOfferDirectoryCard(ApiModel):
    kind: Literal["temporary_shelter"] = "temporary_shelter"
    id: UUID
    public_offer_code: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=1, max_length=1200)
    area_reference: str = Field(min_length=1, max_length=300)
    municipality: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=100)
    availability_status: Literal["active"]
    available_from: datetime
    available_until: datetime
    spaces_available: int = Field(ge=1, le=1_000)
    shared_space: bool
    accepts_pets: bool | None = None
    accessibility_notes: str | None = Field(default=None, max_length=500)
    verification_status: Literal["verified"]
    source: SourceReference
    updated_at: datetime
    data_classification: DataClassification


# CHG-091 — Sugerencias en tiempo real para prevenir duplicados: la
# tarjeta del directorio más la similitud trigram calculada en la base.
class PersonSuggestion(MissingPersonDirectoryCard):
    similarity: float = Field(ge=0.0, le=1.0)


class PersonAutocompleteResponse(ApiModel):
    items: list[PersonSuggestion] = Field(max_length=10)
    query: str = Field(min_length=2, max_length=100)
    generated_at: datetime


class PersonDuplicateCheckResponse(ApiModel):
    items: list[PersonSuggestion] = Field(max_length=10)
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(max_length=120)
    generated_at: datetime


class HumanitarianDirectorySearchResponse(ApiModel):
    items: list[
        MissingPersonDirectoryCard
        | AidLocationDirectoryCard
        | CommunityMealOfferDirectoryCard
        | TemporaryShelterOfferDirectoryCard
    ] = Field(max_length=20)
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=20)
    offset: int = Field(ge=0)
    query: str = Field(min_length=2, max_length=100)
    kind: HumanitarianDirectoryKind
    generated_at: datetime


# CHG-082 — Huella de cambios para el refresco en vivo de la portada.
class ChangeSignal(ApiModel):
    signal: str = Field(min_length=1, max_length=64)
    generated_at: datetime


class CommunityContributionReceipt(ApiModel):
    id: UUID
    status: Literal["under_review"]
    actor_kind: Literal["anonymous", "authenticated"]
    received_at: datetime


# CHG-077 — Novedades visibles de una persona (espejo del contrato).
class PersonStatusReportPublic(ApiModel):
    id: UUID
    claimed_outcome: Literal["found", "deceased"]
    evidence_description: str
    location_description: str | None = None
    occurred_at: datetime | None = None
    received_at: datetime
    reporter_kind: Literal["anonymous", "authenticated", "health_sector"]
    moderation_status: Literal["under_review", "accepted"]


class PersonStatusReportsPage(ApiModel):
    person_id: UUID
    public_status: PublicPersonStatus
    items: list[PersonStatusReportPublic] = Field(max_length=100)
    total: int = Field(ge=0)


# CHG-035 — Reporte de edificio sin verificar (espejo del contrato).
class UnverifiedBuildingReportReceipt(ApiModel):
    id: UUID
    public_tracking_code: str = Field(min_length=1, max_length=40)
    status: Literal["under_review"]
    received_at: datetime


# CHG-036 — Consola de superadministración (espejo del contrato).
AccountRole = Literal["user", "moderator", "super_admin"]
AdminAccountStatus = Literal[
    "pending_verification", "active", "suspended"
]
AdminSubmissionKind = Literal[
    "missing_person_report",
    "unverified_building_report",
    "person_status_report",
    "aid_location_rating",
    "collection_center_registration",
    "collection_point_registration",
    # CHG-044 — ofertas comunitarias.
    "community_meal_offer",
    "temporary_shelter_offer",
]
AdminModerationStatus = Literal[
    "under_review", "needs_information", "accepted", "rejected", "archived"
]
AdminActionName = Literal[
    "accept", "reject", "request_changes", "archive", "restore",
    # CHG-159 — borrado definitivo.
    "delete",
]
AdminSubmissionTheme = Literal["personas", "infraestructura", "ayuda"]
AdminAuditResult = Literal["success", "denied", "failed"]


class AdminCountByKind(ApiModel):
    kind: AdminSubmissionKind
    count: int = Field(ge=0)


class AdminActivitySummary(ApiModel):
    id: UUID
    action: str = Field(min_length=1, max_length=80)
    resource_kind: str = Field(min_length=1, max_length=80)
    occurred_at: datetime
    result: AdminAuditResult


class AdminOverview(ApiModel):
    under_review: int = Field(ge=0)
    needs_information: int = Field(ge=0)
    accepted_today: int = Field(ge=0)
    archived: int = Field(ge=0)
    active_accounts: int = Field(ge=0)
    suspended_accounts: int = Field(ge=0)
    oldest_pending_at: datetime | None = None
    # CHG-044: de 6 a 8 con las dos ofertas comunitarias.
    by_kind: list[AdminCountByKind] = Field(max_length=8)
    recent_activity: list[AdminActivitySummary] = Field(max_length=10)
    generated_at: datetime


# CHG-126 — Métricas del sistema para la consola admin. Memoria, CPU
# y carga son del host; el disco es el del sistema de archivos del
# contenedor (respaldado por el disco del host); la red es la del
# namespace del gateway.
class SystemMetricsSample(ApiModel):
    sampled_at: datetime
    cpu_percent: float = Field(ge=0, le=100)
    # CHG-140: None cuando el host no expone sensores térmicos (VPS
    # virtualizadas); la consola degrada a "N/D".
    cpu_temperature_celsius: float | None = None
    load_1m: float = Field(ge=0)
    load_5m: float = Field(ge=0)
    load_15m: float = Field(ge=0)
    memory_total_bytes: int = Field(ge=0)
    memory_used_bytes: int = Field(ge=0)
    memory_available_bytes: int = Field(ge=0)
    swap_total_bytes: int = Field(ge=0)
    swap_used_bytes: int = Field(ge=0)
    disk_total_bytes: int = Field(ge=0)
    disk_used_bytes: int = Field(ge=0)
    disk_free_bytes: int = Field(ge=0)
    network_rx_bytes_per_second: float = Field(ge=0)
    network_tx_bytes_per_second: float = Field(ge=0)
    uptime_seconds: float = Field(ge=0)


class AdminSystemMetrics(ApiModel):
    interval_seconds: float = Field(gt=0)
    latest: SystemMetricsSample
    series: list[SystemMetricsSample] = Field(max_length=1000)
    generated_at: datetime


class AdminSubmissionSummary(ApiModel):
    id: UUID
    kind: AdminSubmissionKind
    tracking_code: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    location_label: str | None = Field(default=None, max_length=200)
    status: AdminModerationStatus
    source_label: str = Field(min_length=1, max_length=120)
    evidence_count: int = Field(ge=0, le=20)
    received_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class AdminSubmissionPage(ApiModel):
    items: list[AdminSubmissionSummary] = Field(max_length=50)
    total: int = Field(ge=0)
    limit: Literal[10, 25, 50]
    offset: int = Field(ge=0)
    generated_at: datetime


class AdminField(ApiModel):
    key: str = Field(pattern=r"^[a-z][A-Za-z0-9]{0,79}$")
    label: str = Field(min_length=1, max_length=120)
    display_value: str = Field(max_length=4000)
    edit_value: str | None = Field(default=None, max_length=4000)
    classification: Literal["public", "private", "protected"]
    editable: bool
    input_kind: Literal[
        "text", "multiline", "date", "time", "number", "email", "select"
    ] = "text"
    options: list[str] = Field(default_factory=list, max_length=30)


class AdminEvidence(ApiModel):
    id: UUID
    media_type: str = Field(max_length=100)
    size_bytes: int = Field(ge=0)
    scan_status: Literal["safe", "pending", "rejected"]
    created_at: datetime


class AdminSubmissionDetail(AdminSubmissionSummary):
    fields: list[AdminField] = Field(max_length=100)
    evidence: list[AdminEvidence] = Field(max_length=20)
    available_actions: list[AdminActionName]


class AdminMutationReceipt(ApiModel):
    id: UUID
    status: AdminModerationStatus
    version: int = Field(ge=1)
    audit_event_id: UUID
    updated_at: datetime


class AdminSubmissionDeleteReceipt(ApiModel):
    # CHG-159 — recibo del borrado definitivo.
    id: UUID
    audit_event_id: UUID
    deleted_at: datetime


# CHG-154 — Gestión admin de registros de personas (espejo del
# contrato): ocultamiento reversible y edición acotada.
AdminPeopleVisibility = Literal["visible", "hidden", "all"]


class AdminPersonRecord(ApiModel):
    id: UUID
    display_name: str = Field(min_length=1)
    status: HumanStatus
    location: str = Field(min_length=1)
    related_event: str = Field(min_length=1)
    latitude: float | None = None
    longitude: float | None = None
    has_linked_case: bool
    source: SourceReference
    created_at: datetime
    updated_at: datetime
    hidden_at: datetime | None = None
    hidden_by: str | None = None


class AdminPeoplePage(ApiModel):
    items: list[AdminPersonRecord]
    total: int = Field(ge=0)


class AdminEvidenceAccessGrant(ApiModel):
    url: str = Field(min_length=1)
    expires_at: datetime
    audit_event_id: UUID


class AdminAccountSummary(ApiModel):
    id: UUID
    display_name: str = Field(min_length=1, max_length=161)
    email: str = Field(max_length=254)
    assigned_role: AccountRole
    status: AdminAccountStatus
    active_sessions: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class AdminAccountDetail(AdminAccountSummary):
    department: str = Field(min_length=1, max_length=100)
    municipality: str = Field(min_length=1, max_length=100)
    requested_account_type: Literal[
        "citizen", "volunteer", "organization_representative"
    ]
    organization_name: str | None = Field(default=None, max_length=160)
    organization_role: str | None = Field(default=None, max_length=120)


class AdminAccountPage(ApiModel):
    items: list[AdminAccountSummary] = Field(max_length=50)
    total: int = Field(ge=0)
    limit: Literal[10, 25, 50]
    offset: int = Field(ge=0)
    generated_at: datetime


class AdminAuditEvent(ApiModel):
    id: UUID
    actor_account_id: UUID
    actor_display_name: str = Field(min_length=1, max_length=161)
    action: str = Field(min_length=1, max_length=80)
    resource_kind: str = Field(min_length=1, max_length=80)
    # CHG-139: los actos globales (vaciado de solicitudes CHG-138,
    # reinicio de plataforma) no señalan un recurso concreto.
    resource_id: UUID | None = None
    result: AdminAuditResult
    reason_summary: str | None = Field(default=None, max_length=500)
    occurred_at: datetime


class AdminAuditPage(ApiModel):
    items: list[AdminAuditEvent] = Field(max_length=50)
    total: int = Field(ge=0)
    limit: Literal[10, 25, 50]
    offset: int = Field(ge=0)
    generated_at: datetime


# CHG-066 — Presencia de visitantes (espejo del contrato).
VisitorPlatform = Literal["web", "android", "ios"]


class VisitorPresenceReceipt(ApiModel):
    status: Literal["accepted"]


class AdminVisitorPresence(ApiModel):
    presence_id: UUID
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_meters: float | None = Field(default=None, ge=0)
    platform: VisitorPlatform
    authenticated: bool
    first_seen_at: datetime
    updated_at: datetime


class AdminVisitorPresencePage(ApiModel):
    items: list[AdminVisitorPresence] = Field(max_length=200)
    total: int = Field(ge=0)
    window_minutes: int = Field(ge=1)
    generated_at: datetime


# CHG-069 — "Mi espacio" (espejo del contrato del disaster-service).
class VolunteerAlertInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    description: str = Field(min_length=10, max_length=1000)
    address: str = Field(min_length=5, max_length=300)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class VolunteerAlert(ApiModel):
    id: UUID
    description: str
    address: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    status: Literal["active", "resolved"]
    created_at: datetime
    updated_at: datetime


class VolunteerAlertPage(ApiModel):
    items: list[VolunteerAlert] = Field(max_length=100)
    total: int = Field(ge=0)
    generated_at: datetime


# CHG-125 — «Necesitamos ayuda» (espejo del disaster-service).
class HelpRequestReceipt(ApiModel):
    id: UUID
    public_code: str
    status: Literal["active"]
    received_at: datetime
    expires_at: datetime


class ActiveHelpRequest(ApiModel):
    id: UUID
    description: str
    address: str
    # CHG-127: null cuando la solicitud llegó solo con dirección.
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    # CHG-131: radio de aviso en km; null si la solicitud no lo definió.
    notification_radius_km: int | None = Field(default=None, ge=1, le=100)
    created_at: datetime
    expires_at: datetime
    attenders_count: int = Field(ge=0)
    attended_by_me: bool
    photo_url: str | None = None


class HelpRequestPage(ApiModel):
    items: list[ActiveHelpRequest] = Field(max_length=50)
    total: int = Field(ge=0)
    generated_at: datetime


class HelpRequestAttendReceipt(ApiModel):
    id: UUID
    attenders_count: int = Field(ge=1)
    attending: bool


# CHG-163 — «Ofrecer comida» (espejo del disaster-service).
class FoodOfferReceipt(ApiModel):
    id: UUID
    public_code: str
    status: Literal["active"]
    received_at: datetime
    expires_at: datetime


class ActiveFoodOffer(ApiModel):
    id: UUID
    description: str
    address: str
    # Null cuando la oferta llegó solo con dirección escrita.
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    notification_radius_km: int | None = Field(default=None, ge=1, le=100)
    created_at: datetime
    expires_at: datetime
    # CHG-176: la puntuación viaja con la oferta porque el mapa la
    # fusiona en cliente, igual que el resto de sus datos.
    comment_rating_average: float | None = Field(default=None, ge=1, le=5)
    comment_rating_count: int = Field(default=0, ge=0)


class FoodOfferPage(ApiModel):
    items: list[ActiveFoodOffer] = Field(max_length=50)
    total: int = Field(ge=0)
    generated_at: datetime


# CHG-138 — Gestión admin de solicitudes (espejo del disaster-service).
class AdminHelpRequest(ApiModel):
    id: UUID
    public_code: str
    description: str
    address: str
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    notification_radius_km: int | None = Field(default=None, ge=1, le=100)
    created_at: datetime
    expires_at: datetime
    expired: bool
    attenders_count: int = Field(ge=0)
    # CHG-148: voluntarios anónimos con datos privados que ver.
    volunteers_count: int = Field(default=0, ge=0)
    has_photo: bool


class AdminHelpRequestPage(ApiModel):
    items: list[AdminHelpRequest] = Field(max_length=200)
    total: int = Field(ge=0)
    generated_at: datetime


# CHG-148 — Voluntario anónimo visto por el super_admin (espejo).
class AdminHelpRequestVolunteer(ApiModel):
    id: UUID
    name: str
    phone: str | None = None
    email: str | None = None
    has_photo: bool
    created_at: datetime


class AdminHelpRequestVolunteerPage(ApiModel):
    items: list[AdminHelpRequestVolunteer] = Field(max_length=1000)
    total: int = Field(ge=0)
    generated_at: datetime


class AdminHelpRequestDeleteReceipt(ApiModel):
    deleted: int = Field(ge=0)


# CHG-139 — Reinicio absoluto de la plataforma.
PLATFORM_RESET_CONFIRMATION = "REINICIAR TODO"


class AdminPlatformResetInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    # La frase exacta que la persona debe escribir en la consola.
    confirm: Literal["REINICIAR TODO"]


class AdminPlatformResetReceipt(ApiModel):
    tables_cleared: int = Field(ge=0)
    accounts_deleted: int = Field(ge=0)
    generated_at: datetime


class MyReportNovelty(ApiModel):
    claimed_outcome: Literal["found", "deceased"]
    moderation_status: AdminModerationStatus
    received_at: datetime


class MyReportSummary(ApiModel):
    id: UUID
    kind: Literal["missing_person_report", "unverified_building_report"]
    reference_code: str
    title: str
    status: str
    received_at: datetime
    novelties: list[MyReportNovelty] = Field(
        default_factory=list, max_length=50
    )


class MyReportsPage(ApiModel):
    items: list[MyReportSummary] = Field(max_length=200)
    total: int = Field(ge=0)
    generated_at: datetime


class HealthStatus(BaseModel):
    status: Literal["ok"]
    service: str


class ServiceVersion(BaseModel):
    service: str
    revision: str


class PlatformVersion(BaseModel):
    # CHG-111: qué código está sirviendo de verdad. `revision` es
    # "unknown" si la imagen se construyó sin GIT_REVISION, y `upstream`
    # es null si el servicio de desastres no contesta: la verificación
    # del gateway no puede depender de que el otro esté vivo.
    service: str
    revision: str
    upstream: ServiceVersion | None = None


class GeocodeCandidate(BaseModel):
    # CHG-147: candidata de la búsqueda directa por el proxy del
    # gateway; el label es el display_name completo de Nominatim.
    label: str
    latitude: float
    longitude: float


class GeocodeCandidateList(BaseModel):
    candidates: list[GeocodeCandidate]


class GeocodeResolvedAddress(ApiModel):
    # CHG-147: dirección aproximada del punto (geocodificación
    # inversa); municipio y departamento pueden faltar en zonas
    # rurales y el cliente lo tolera.
    label: str
    # CHG-156: dirección corta (vía, barrio, comuna) sin la cola
    # administrativa, para el campo Dirección de los formularios.
    address_line: str | None = None
    municipality: str | None = None
    department: str | None = None


# CHG-161 — transporte humanitario («La mulera» / «La lanchera»).
TransportKind = Literal["mule", "boat"]
TransportStatus = Literal[
    "registered", "in_transit", "arrived", "cancelled"
]


class HumanitarianTransportReceipt(ApiModel):
    id: UUID
    kind: TransportKind
    status: TransportStatus
    origin_location_id: UUID
    destination_location_id: UUID
    created_at: datetime


# CHG-171 §50 — Catálogo de ciudades de La Mulera.
class TransportCity(ApiModel):
    name: str
    department: str


class TransportCitiesResponse(ApiModel):
    items: list[TransportCity]
    total: int = Field(ge=0)


# CHG-171 (GPS) — Hitos y posiciones del viaje del conductor.
class TransportJourneyReceipt(ApiModel):
    id: UUID
    status: TransportStatus
    departed_at: datetime | None = None
    arrived_at: datetime | None = None
    last_position_at: datetime | None = None


class TransportTrailPoint(ApiModel):
    latitude: float
    longitude: float
    recorded_at: datetime


# Ficha pública del viaje para el mapa: SIN datos del conductor (§30).
class ActiveTransport(ApiModel):
    id: UUID
    kind: TransportKind
    status: TransportStatus
    origin_name: str
    origin_municipality: str
    origin_latitude: float | None = None
    origin_longitude: float | None = None
    destination_name: str
    destination_municipality: str
    destination_latitude: float | None = None
    destination_longitude: float | None = None
    supplies_summary: str | None = None
    tractor_plate: str | None = None
    trailer_plate: str | None = None
    # CHG-173: identificación visible de la embarcación (la lanchera
    # no lleva placas; lo del conductor sigue sin publicarse).
    vessel_registration: str | None = None
    vessel_name: str | None = None
    vessel_type: str | None = None
    vehicle_visible_characteristics: str | None = None
    departed_at: datetime | None = None
    arrived_at: datetime | None = None
    last_latitude: float | None = None
    last_longitude: float | None = None
    last_position_at: datetime | None = None
    created_at: datetime
    trail: list[TransportTrailPoint] = Field(default_factory=list)


class ActiveTransportsResponse(ApiModel):
    items: list[ActiveTransport]
    total: int = Field(ge=0)


# CHG-174 — Aceptación inicial de ruta Centro de Acopio Local ↔ Mulera.
TransportRequestStatus = Literal["pending", "accepted", "declined"]
TransportCenterRole = Literal["local", "reception"]
RouteAcceptanceStatus = Literal["code_issued", "accepted"]


class TransportCenterRequest(ApiModel):
    """Solicitud del centro: vista administrativa autorizada (§12-§13).

    Los datos del conductor llegan descifrados desde disaster-service y
    solo se sirven a quien es responsable del centro.
    """

    id: UUID
    transport_id: UUID
    center_id: UUID
    center_role: TransportCenterRole
    status: TransportRequestStatus
    requested_at: datetime
    decided_at: datetime | None = None
    center_name: str
    center_municipality: str
    transport_kind: TransportKind
    origin_center_name: str
    destination_center_name: str
    origin_municipality: str
    destination_municipality: str
    supplies_summary: str | None = None
    transport_created_at: datetime
    driver_full_name: str | None = None
    driver_document_type: str | None = None
    driver_document_number: str | None = None
    driver_phone: str | None = None
    tractor_plate: str | None = None
    trailer_plate: str | None = None
    vessel_registration: str | None = None
    vessel_name: str | None = None
    vessel_type: str | None = None
    vehicle_visible_characteristics: str | None = None


class TransportCenterRequestsResponse(ApiModel):
    items: list[TransportCenterRequest]
    total: int = Field(ge=0)


class TransportRequestDecisionReceipt(ApiModel):
    id: UUID
    transport_id: UUID
    center_id: UUID
    status: TransportRequestStatus
    decided_at: datetime | None = None


class TransportRouteState(ApiModel):
    transport_id: UUID
    transport_kind: TransportKind
    transport_created_at: datetime
    origin_center_name: str
    destination_center_name: str
    origin_municipality: str
    destination_municipality: str
    local_status: TransportRequestStatus | None = None
    reception_status: TransportRequestStatus | None = None
    route_status: RouteAcceptanceStatus | None = None
    confirmation_code: str | None = None
    local_accepted_at: datetime | None = None
    mule_code_validated_at: datetime | None = None
    mule_accepted_at: datetime | None = None
    # CHG-175 — Etapa 2, con su propio código y sus propios hitos. La
    # ruta global solo se considera aceptada con las DOS completas.
    reception_confirmation_code: str | None = None
    reception_started_at: datetime | None = None
    reception_mule_code_validated_at: datetime | None = None
    reception_mule_accepted_at: datetime | None = None
    route_accepted_at: datetime | None = None
    is_local_steward: bool = False
    is_reception_steward: bool = False


class TransportRouteStatesResponse(ApiModel):
    items: list[TransportRouteState]
    total: int = Field(ge=0)


class TransportRouteCodeReceipt(ApiModel):
    transport_id: UUID
    confirmation_code: str
    status: RouteAcceptanceStatus
    reused: bool = False


class MyTransport(ApiModel):
    transport_id: UUID
    transport_kind: TransportKind
    transport_created_at: datetime
    origin_center_name: str
    destination_center_name: str
    origin_municipality: str
    destination_municipality: str
    local_status: TransportRequestStatus | None = None
    reception_status: TransportRequestStatus | None = None
    route_status: RouteAcceptanceStatus | None = None
    mule_code_validated_at: datetime | None = None
    mule_accepted_at: datetime | None = None


class MyTransportsResponse(ApiModel):
    items: list[MyTransport]
    total: int = Field(ge=0)


class RouteCodeValidationReceipt(ApiModel):
    transport_id: UUID
    validated: bool
    origin_center_name: str
    destination_center_name: str


class RouteAcceptanceReceipt(ApiModel):
    transport_id: UUID
    status: RouteAcceptanceStatus
    mule_accepted_at: datetime | None = None
    # CHG-175 §45: sello del estado global, presente solo cuando las DOS
    # relaciones quedaron completas.
    route_accepted_at: datetime | None = None


# CHG-162 — «Mi casita partida».
class DamagedHomeReportReceipt(ApiModel):
    id: UUID
    created_at: datetime
