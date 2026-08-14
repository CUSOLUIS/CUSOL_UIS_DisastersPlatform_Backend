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
    "rubble_reviewed",
    "rubble_pending",
    "building_pending",
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


class OperationalMapSummary(ApiModel):
    missing_person: int = Field(ge=0)
    collection_center: int = Field(ge=0)
    rubble_reviewed: int = Field(ge=0)
    rubble_pending: int = Field(ge=0)
    # CHG-010: default 0 solo tolera upstream anterior durante el
    # despliegue coordinado; la respuesta del gateway siempre lo emite.
    building_pending: int = Field(ge=0, default=0)


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


class MissingPersonSearchResponse(ApiModel):
    items: list[MissingPersonPublicRecord] = Field(max_length=20)
    total: int = Field(ge=0)
    query: str


class MissingPersonReportReceipt(ApiModel):
    id: UUID
    public_case_code: str = Field(min_length=1)
    status: Literal["under_review"]
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


class EmailVerificationReceipt(ApiModel):
    status: Literal["active"]
    verified_at: datetime


class AuthenticatedAccount(ApiModel):
    id: UUID
    display_name: str = Field(min_length=1, max_length=161)
    email: str = Field(max_length=254)
    assigned_role: Literal["user", "moderator", "super_admin"]
    status: Literal["active"]
    session_expires_at: datetime


class SessionEnvelope(ApiModel):
    """Respuesta interna del identity-service; el gateway convierte el
    token en cookie `cusol_session` y nunca lo reenvía en el cuerpo."""

    account: AuthenticatedAccount
    session_token: str
    session_expires_at: datetime


# CHG-034 — Directorio humanitario y aportes (espejo del contrato).
HumanitarianDirectoryKind = Literal[
    "missing_person", "collection_center", "collection_point"
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


class AidLocationDirectoryCard(ApiModel):
    kind: Literal["collection_center", "collection_point"]
    id: UUID
    name: str = Field(min_length=1, max_length=180)
    location_label: str = Field(min_length=1, max_length=300)
    municipality: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=100)
    verification_status: VerificationStatus
    availability_status: AidLocationAvailability
    open_now: bool | None = None
    accepted_supplies: list[AidSupplyCategory] = Field(max_length=12)
    average_rating: float | None = Field(default=None, ge=1, le=5)
    ratings_count: int = Field(ge=0)
    source: SourceReference
    updated_at: datetime
    data_classification: DataClassification


class HumanitarianDirectorySearchResponse(ApiModel):
    items: list[MissingPersonDirectoryCard | AidLocationDirectoryCard] = (
        Field(max_length=20)
    )
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=20)
    offset: int = Field(ge=0)
    query: str = Field(min_length=2, max_length=100)
    kind: HumanitarianDirectoryKind
    generated_at: datetime


class CommunityContributionReceipt(ApiModel):
    id: UUID
    status: Literal["under_review"]
    actor_kind: Literal["anonymous", "authenticated"]
    received_at: datetime


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
]
AdminModerationStatus = Literal[
    "under_review", "needs_information", "accepted", "rejected", "archived"
]
AdminActionName = Literal[
    "accept", "reject", "request_changes", "archive", "restore"
]
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
    by_kind: list[AdminCountByKind] = Field(max_length=6)
    recent_activity: list[AdminActivitySummary] = Field(max_length=10)
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
    resource_id: UUID
    result: AdminAuditResult
    reason_summary: str | None = Field(default=None, max_length=500)
    occurred_at: datetime


class AdminAuditPage(ApiModel):
    items: list[AdminAuditEvent] = Field(max_length=50)
    total: int = Field(ge=0)
    limit: Literal[10, 25, 50]
    offset: int = Field(ge=0)
    generated_at: datetime


class HealthStatus(BaseModel):
    status: Literal["ok"]
    service: str
