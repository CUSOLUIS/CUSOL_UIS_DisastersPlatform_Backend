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


class HealthStatus(BaseModel):
    status: Literal["ok"]
    service: str
