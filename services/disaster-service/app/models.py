import re
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .person_options import DOCUMENT_TYPES, NATIONALITIES, SEXES
from .text_quality import TextQualityError, validate_community_text


VerificationStatus = Literal[
    "unverified", "under_review", "verified", "rejected"
]
SourceType = Literal[
    "official", "citizen", "sensor", "integration", "ai_inference"
]


# CHG-100 — Teléfonos de contacto. El límite anterior (40 caracteres,
# sin formato) dejaba pasar números de 25 dígitos que nadie puede
# marcar. El patrón admite los separadores con los que la gente
# escribe ("+57 (300) 123-4567") y acota los dígitos a E.164: entre 7
# y 15, sin cero inicial.
# CHG-115 — La edad se deriva de la fecha de nacimiento cuando esta
# viene; el tope es el mismo del modelo y del CHECK de la base.
MAX_APPROXIMATE_AGE = 120


def age_from_birth_date(birth_date: date, today: date) -> int:
    """Edad cumplida: no se cumplen años hasta que llega el día."""
    cumplido = (today.month, today.day) >= (
        birth_date.month,
        birth_date.day,
    )
    return today.year - birth_date.year - (0 if cumplido else 1)


PHONE_PATTERN = r"^\+?[\s().-]*[1-9](?:[\s().-]*\d){6,14}$"
PHONE_MAX_LENGTH = 25


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
    # CHG-153 — logística humanitaria: acopio receptor y distribución
    # (recolección y acopio local reutilizan collection_point /
    # collection_center de arriba).
    "receiver_center",
    "distribution_point",
    # CHG-162 — hogares en malas condiciones («Mi casita partida»).
    "damaged_home",
]
CoordinatePrecision = Literal["exact", "approximate", "municipality"]
DataClassification = Literal["demonstrative", "operational"]


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
    # CHG-166: promedio de estrellas de los comentarios del punto
    # logístico (None/0 en las demás categorías); el popup del mapa lo
    # muestra encima del resumen.
    comment_rating_average: float | None = Field(default=None, ge=1, le=5)
    comment_rating_count: int = Field(default=0, ge=0)


class OperationalMapSummary(ApiModel):
    missing_person: int = Field(ge=0)
    collection_center: int = Field(ge=0)
    rubble_reviewed: int = Field(ge=0)
    rubble_pending: int = Field(ge=0)
    # CHG-010: parte regular de toda respuesta del backend actualizado.
    building_pending: int = Field(ge=0)
    # CHG-049: nuevas categorías comunitarias del mapa.
    collection_point: int = Field(default=0, ge=0)
    community_meal: int = Field(default=0, ge=0)
    temporary_shelter: int = Field(default=0, ge=0)
    # CHG-069: alertas de voluntariado activas.
    volunteers_needed: int = Field(default=0, ge=0)
    # CHG-153: logística humanitaria (los contadores vienen del backend).
    receiver_center: int = Field(default=0, ge=0)
    distribution_point: int = Field(default=0, ge=0)
    # CHG-162: hogares en malas condiciones.
    damaged_home: int = Field(default=0, ge=0)


class OperationalMapOverview(ApiModel):
    summary: OperationalMapSummary
    items: list[OperationalMapPoint] = Field(max_length=500)
    generated_at: datetime
    data_classification: DataClassification


# CHG-015 — Capa geográfica de situación humana. DEC-007: la precisión
# pública nunca es "exact".
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
    """Punto público anónimo: sin identidad ni coordenada exacta."""

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
    # CHG-099: desglose por estado de quienes no se pueden dibujar. La
    # capa suma lo mapeado + esto para mostrar el total por estado y
    # coincidir con las cifras de la portada.
    unmapped_status_counts: HumanMapStatusCounts
    returned_features: int = Field(ge=0)
    next_cursor: str | None = None
    generated_at: datetime
    data_classification: DataClassification


class MissingPersonPublicRecord(ApiModel):
    """Proyección pública moderada: solo campos autorizados (FEATURE-004)."""

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


# CHG-094 — Contexto del desplazamiento y categoría de fotografías.
PersonTransportMode = Literal[
    "on_foot",
    "bicycle",
    "public_transport",
    "private_vehicle",
    "other",
]
# CHG-106 — Primer año que cubre la plataforma: una desaparición
# registrada aquí no puede ser anterior a su puesta en operación. El
# calendario del formulario ya no ofrece años previos; esta regla
# impide que un envío armado a mano los cuele.
PLATFORM_FIRST_YEAR = 2026


PersonPhotoCategory = Literal[
    "recent_face",
    "full_body",
    "distinctive_mark",
    "other",
]


class MissingPersonReportInput(ApiModel):
    """Payload privado del reporte; nunca se serializa en respuestas."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    first_names: str = Field(min_length=1, max_length=120)
    last_names: str = Field(min_length=1, max_length=120)
    aliases: str | None = Field(default=None, max_length=200)
    birth_date: date | None = None
    approximate_age: int | None = Field(default=None, ge=0, le=120)
    gender_identity: str | None = Field(default=None, max_length=80)
    nationality: str | None = Field(default=None, max_length=80)
    document_type: str | None = Field(default=None, max_length=40)
    document_number: str | None = Field(default=None, max_length=80)
    height_cm: int | None = Field(default=None, ge=30, le=250)
    build: str | None = Field(default=None, max_length=80)
    skin_tone: str | None = Field(default=None, max_length=80)
    hair_description: str | None = Field(default=None, max_length=200)
    eye_description: str | None = Field(default=None, max_length=120)
    distinctive_marks: str | None = Field(default=None, max_length=1000)
    medical_information: str | None = Field(default=None, max_length=1000)
    # CHG-094 — Identificación física detallada.
    tattoo_description: str | None = Field(default=None, max_length=1000)
    scars_description: str | None = Field(default=None, max_length=1000)
    prosthetics_description: str | None = Field(
        default=None, max_length=1000
    )
    piercings_and_moles: str | None = Field(default=None, max_length=1000)
    # CHG-094 — Alertas médicas (se guardan cifradas).
    mental_health_condition: str | None = Field(
        default=None, max_length=1000
    )
    vital_medication: str | None = Field(default=None, max_length=1000)
    severe_allergies: str | None = Field(default=None, max_length=1000)
    # CHG-094 — Contexto del desplazamiento.
    belongings_description: str | None = Field(
        default=None, max_length=1000
    )
    transport_mode: PersonTransportMode | None = None
    vehicle_details: str | None = Field(default=None, max_length=300)
    companions_description: str | None = Field(
        default=None, max_length=1000
    )
    last_seen_date: date
    last_seen_time: str | None = Field(
        default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$"
    )
    # CHG-015: coordenadas del último avistamiento; llegan en pareja o
    # no llegan. Desde CHG-081 proyectan el punto del caso en el mapa
    # operativo al publicarse.
    last_seen_latitude: float | None = Field(default=None, ge=-90, le=90)
    last_seen_longitude: float | None = Field(
        default=None, ge=-180, le=180
    )
    department: str = Field(min_length=1, max_length=100)
    municipality: str = Field(min_length=1, max_length=100)
    last_seen_area: str = Field(min_length=1, max_length=300)
    # CHG-113: quien denuncia una desaparición muchas veces no sabe
    # con qué ropa salió la persona. Exigirlo llenaba la columna de
    # "no sé" — texto que además entra en el índice de búsqueda de
    # casos, así que degradaba la búsqueda para todos.
    clothing_description: str | None = Field(
        default=None, max_length=1000
    )
    circumstances: str = Field(min_length=1, max_length=2000)
    additional_description: str | None = Field(default=None, max_length=2000)
    reporter_name: str = Field(min_length=1, max_length=160)
    reporter_relationship: str = Field(min_length=1, max_length=100)
    reporter_phone: str | None = Field(
        default=None, max_length=PHONE_MAX_LENGTH, pattern=PHONE_PATTERN
    )
    reporter_email: str | None = Field(default=None, max_length=254)
    official_report_number: str | None = Field(default=None, max_length=100)
    # CHG-094 — Entidad donde se interpuso la denuncia.
    official_authority_name: str | None = Field(
        default=None, max_length=160
    )
    # CHG-094 — Consentimiento del reportante para compartir su
    # contacto. El dato sigue cifrado; esto registra la autorización y
    # la ve el equipo de revisión. NO publica nada por sí solo.
    is_reporter_phone_public: bool = False
    is_reporter_email_public: bool = False
    # CHG-094 — Categoría por fotografía, alineada por posición con las
    # partes `photos` del multipart.
    photo_categories: list[PersonPhotoCategory] = Field(
        default_factory=list, max_length=3
    )
    # CHG-066: instantánea opcional de la ubicación del reportante al
    # enviar; llega en pareja, se cifra y solo la ve super_admin.
    reporter_latitude: float | None = Field(default=None, ge=-90, le=90)
    reporter_longitude: float | None = Field(
        default=None, ge=-180, le=180
    )
    truth_confirmed: Literal[True]
    photo_authorization_confirmed: Literal[True]
    # CHG-075: la publicación es inmediata y ya no se exige aceptar
    # una revisión previa; se acepta el campo por compatibilidad con
    # clientes antiguos y se ignora su valor.
    review_acknowledged: bool = True

    # CHG-113: un cliente que mande la vestimenta en blanco está
    # diciendo lo mismo que uno que la omite. Se guarda como ausencia y
    # no como cadena vacía: la ficha pública distingue "sin dato" de un
    # texto vacío, y la búsqueda de casos no indexa nada.
    @model_validator(mode="after")
    def _blank_clothing_is_absent(self):
        if self.clothing_description is not None:
            limpio = self.clothing_description.strip()
            self.clothing_description = limpio or None
        return self

    # CHG-106: la última visualización no puede ser anterior al primer
    # año que cubre la plataforma.
    @model_validator(mode="after")
    def _last_seen_within_coverage(self):
        if self.last_seen_date.year < PLATFORM_FIRST_YEAR:
            raise ValueError(
                "lastSeenDate no puede ser anterior a "
                f"{PLATFORM_FIRST_YEAR}, el primer año que cubre la "
                "plataforma"
            )
        return self

    # CHG-094: la categoría de fotografía se alinea por posición con
    # las partes `photos`; el conteo real se valida al leer el
    # multipart (aquí solo el tope del modelo).
    @model_validator(mode="after")
    def _vehicle_needs_transport(self):
        if (self.vehicle_details or "").strip() and self.transport_mode not in {
            "private_vehicle",
            "other",
        }:
            raise ValueError(
                "vehicleDetails solo aplica con transportMode "
                "'private_vehicle' u 'other'"
            )
        return self

    # CHG-073: listas cerradas y fecha de nacimiento plausible; el
    # mismo criterio vive en el frontend y en la base (migración 020).
    @model_validator(mode="after")
    def _person_field_options(self):
        if (
            self.gender_identity is not None
            and self.gender_identity not in SEXES
        ):
            raise ValueError("genderIdentity debe ser Hombre o Mujer")
        if (
            self.document_type is not None
            and self.document_type not in DOCUMENT_TYPES
        ):
            raise ValueError(
                "documentType debe ser uno de los tipos permitidos"
            )
        if (
            self.nationality is not None
            and self.nationality not in NATIONALITIES
        ):
            raise ValueError(
                "nationality debe elegirse de la lista de nacionalidades"
            )
        if self.birth_date is not None:
            if self.birth_date < date(1900, 1, 1):
                raise ValueError(
                    "birthDate no puede ser anterior a 1900"
                )
            if self.birth_date > date.today():
                raise ValueError("birthDate no puede estar en el futuro")
        return self

    # CHG-115: dos campos que describen el mismo hecho se
    # contradecían (`2004-11-23` junto a `12`). Con fecha de
    # nacimiento la edad es derivada: el formulario web ya la calcula,
    # pero el APK instalado y cualquier otro cliente siguen mandándola
    # a mano, así que aquí se recalcula y se descarta la recibida.
    # Sin fecha de nacimiento se respeta tal cual: es el caso normal,
    # quien reporta rara vez conoce la fecha exacta.
    @model_validator(mode="after")
    def _age_follows_birth_date(self):
        if self.birth_date is None:
            return self
        edad = age_from_birth_date(self.birth_date, date.today())
        if edad > MAX_APPROXIMATE_AGE:
            raise ValueError(
                "birthDate implica una edad mayor a "
                f"{MAX_APPROXIMATE_AGE} años"
            )
        self.approximate_age = edad
        return self

    @model_validator(mode="after")
    def _last_seen_coordinates_pair(self):
        latitude_missing = self.last_seen_latitude is None
        longitude_missing = self.last_seen_longitude is None
        if latitude_missing != longitude_missing:
            raise ValueError(
                "lastSeenLatitude y lastSeenLongitude deben enviarse "
                "juntas o ambas ausentes"
            )
        return self

    @model_validator(mode="after")
    def _reporter_snapshot_pair(self):
        if (self.reporter_latitude is None) != (
            self.reporter_longitude is None
        ):
            raise ValueError(
                "reporterLatitude y reporterLongitude deben enviarse "
                "juntas o ambas ausentes"
            )
        return self


class MissingPersonReportReceipt(ApiModel):
    # CHG-075: el caso público se crea junto con el reporte, así que
    # la constancia informa `published`.
    id: UUID
    public_case_code: str = Field(min_length=1)
    status: Literal["published"]
    received_at: datetime


# CHG-034 — Directorio humanitario y aportes con evidencia.
# CHG-044 amplía el directorio con ofertas comunitarias.
HumanitarianDirectoryKind = Literal[
    "missing_person",
    "collection_center",
    "collection_point",
    "community_meal",
    "temporary_shelter",
    # CHG-153 — logística: acopio receptor y distribución.
    "receiver_center",
    "distribution_point",
]
PublicPersonStatus = Literal["missing", "found", "deceased"]
AidLocationAvailability = Literal["active", "inactive", "unknown"]

# CHG-153 — los cuatro tipos logísticos y sus dependencias.
AidLocationKind = Literal[
    "collection_center",  # acopio local (independiente)
    "collection_point",  # recolección (depende de acopio local)
    "receiver_center",  # acopio receptor (independiente)
    "distribution_point",  # distribución (depende de acopio receptor)
]
AidLocationOperationalStatus = Literal[
    "open",  # ABIERTO
    "closed",  # CERRADO
    "at_capacity",  # CAPACIDAD_COMPLETA
    "under_observation",  # EN_OBSERVACION (umbral de denuncias)
    "inactive",  # INACTIVO
]
# CHG-161 (F2) — Tipos cuya alta exige una cuenta: el acopio local y el
# punto de distribución asumen responsabilidad permanente sobre insumos
# ajenos, así que necesitan una persona responsable identificable. Es el
# refuerzo server-side del portón que el formulario ya pinta
# (`AID_LOCATION_REQUIRES_SESSION` en el frontend); recolección y acopio
# receptor siguen admitiendo alta anónima.
AID_LOCATION_KINDS_REQUIRING_ACCOUNT: frozenset[str] = frozenset(
    {"collection_center", "distribution_point"}
)

# Punto dependiente -> tipo del centro padre que exige.
AID_LOCATION_PARENT_KIND: dict[str, str] = {
    "collection_point": "collection_center",
    "distribution_point": "receiver_center",
}
# Umbral de denuncias válidas que pone un acopio EN_OBSERVACION (§12).
AID_LOCATION_REPORT_THRESHOLD = 10
# CHG-165 — Umbral de denuncias vivas que DESHABILITA un Centro de
# Acopio Local (inactive + disabled_at): sale del mapa público sin
# borrar nada; solo super_admin lo reactiva (reinicia el ciclo).
AID_LOCATION_DISABLE_THRESHOLD = 20
AidSupplyCategory = Literal[
    "water", "food", "medicine", "clothing", "tools", "shelter", "other"
]
ContributionActorKind = Literal["anonymous", "authenticated"]


class MissingPersonDirectoryCard(ApiModel):
    """Tarjeta pública de persona: solo la proyección moderada."""

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
    """Tarjeta pública de lugar de ayuda; el promedio usa únicamente
    valoraciones aceptadas."""

    # CHG-153: los cuatro tipos logísticos.
    kind: AidLocationKind
    id: UUID
    name: str = Field(min_length=1, max_length=180)
    location_label: str = Field(min_length=1, max_length=300)
    municipality: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=100)
    # CHG-153: coordenadas (null si el lugar no las tiene) y estado
    # operativo amigable; el centro asociado cuando es dependiente.
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


# CHG-153 — Alta de un punto logístico. La dependencia (centro asociado)
# y el territorio se validan TAMBIÉN en el servicio (no se confía en el
# cliente). Independientes (acopio local/receptor) NO llevan parent;
# dependientes (recolección/distribución) lo EXIGEN.
class AidLocationInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    kind: AidLocationKind
    name: str = Field(min_length=2, max_length=180)
    address: str = Field(min_length=3, max_length=300)
    municipality: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=100)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    description: str | None = Field(default=None, max_length=1000)
    schedule: str | None = Field(default=None, max_length=200)
    contact: str | None = Field(default=None, max_length=200)
    parent_id: UUID | None = None
    accepted_supplies: list[AidSupplyCategory] = Field(
        default_factory=list, max_length=12
    )
    operational_status: AidLocationOperationalStatus = "open"

    @model_validator(mode="after")
    def _coordinates_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "latitude y longitude deben enviarse juntas o ambas "
                "ausentes"
            )
        return self

    @model_validator(mode="after")
    def _dependency_by_kind(self):
        needs_parent = self.kind in AID_LOCATION_PARENT_KIND
        if needs_parent and self.parent_id is None:
            raise ValueError(
                "este tipo exige un centro asociado (parentId)"
            )
        if not needs_parent and self.parent_id is not None:
            raise ValueError(
                "este tipo es independiente: no lleva centro asociado"
            )
        return self


class AidLocationReceipt(ApiModel):
    id: UUID
    kind: AidLocationKind
    operational_status: AidLocationOperationalStatus
    created_at: datetime


# CHG-153 — Candidatos a centro asociado para el alta de un punto
# dependiente: solo del tipo padre correcto, publicados, no inactivos y
# de la misma ciudad. El formulario los ofrece; el servicio revalida.
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


# CHG-165 — Motivos del selector de denuncia (§10): el usuario dice
# por qué denuncia además de describir la situación.
AidLocationReportCategory = Literal[
    "no_existe",
    "informacion_falsa",
    "funcionamiento_irregular",
    "seguridad",
    "otro",
]


# CHG-153 — Denuncia sobre un lugar de ayuda (§11-13). Dual anónimo/
# autenticado; la descripción se cifra (solo super_admin). El umbral de
# 10 denunciantes distintos pone el acopio EN_OBSERVACION; CHG-165 suma
# el motivo por categorías y el umbral de 20 que deshabilita.
class AidLocationReportInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    # CHG-165: motivo obligatorio del selector; la descripción libre
    # conserva el campo `reason` histórico (3-1000, cifrada).
    category: AidLocationReportCategory
    reason: str = Field(min_length=3, max_length=1000)


class AidLocationReportReceipt(ApiModel):
    location_id: UUID
    reports_count: int = Field(ge=1)
    under_observation: bool
    # CHG-165: true cuando esta denuncia dejó (o encontró) el centro
    # deshabilitado por alcanzar el umbral de 20.
    disabled: bool = False


# CHG-176 — Constancia de la denuncia a una oferta de comida. Misma
# forma que la del acopio, con su propio identificador: la oferta no
# tiene estado operativo, así que «deshabilitada» significa que deja de
# publicarse.
class FoodOfferReportReceipt(ApiModel):
    food_offer_id: UUID
    reports_count: int = Field(ge=1)
    under_observation: bool
    disabled: bool = False


class FoodOfferDeleteReceipt(ApiModel):
    deleted: int = Field(ge=0)


# CHG-205 — Constancia de la denuncia a una oferta de alojamiento.
# Gemela de la de comida: el alojamiento tampoco tiene estado operativo,
# así que «deshabilitada» significa que deja de publicarse.
class ShelterOfferReportReceipt(ApiModel):
    shelter_offer_id: UUID
    reports_count: int = Field(ge=1)
    under_observation: bool
    disabled: bool = False


class ShelterOfferDeleteReceipt(ApiModel):
    deleted: int = Field(ge=0)


# CHG-180 — Constancia de la denuncia a una solicitud de ayuda. Misma
# forma que la del acopio y la de la oferta, con su propio
# identificador: la solicitud es efímera, así que «deshabilitada»
# significa que deja de publicarse, no que se pueda reactivar.
class HelpRequestReportReceipt(ApiModel):
    help_request_id: UUID
    reports_count: int = Field(ge=1)
    under_observation: bool
    disabled: bool = False


# CHG-165 — Comentario público de un Centro de Acopio Local (§4-8).
# account_id NULL = anónimo; el nombre visible se congela al publicar y
# nunca incluye correo/teléfono/datos privados.
class AidLocationCommentInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    content: str = Field(min_length=5, max_length=1000)
    # CHG-166: calificación 1-5 obligatoria en comentarios nuevos.
    rating: int = Field(ge=1, le=5)

    # Mismo criterio antibasura que el resto de textos comunitarios;
    # umbral bajo porque un comentario útil puede ser muy breve.
    @model_validator(mode="after")
    def _content_is_readable(self):
        try:
            validate_community_text(
                self.content, "content", min_distinct_words=2
            )
        except TextQualityError as error:
            raise ValueError(str(error)) from error
        return self


class AidLocationComment(ApiModel):
    id: UUID
    # NULL → el frontend muestra «Anónimo»; nunca se almacena texto
    # "anonymous" en lugar de la relación nula (§7).
    author_display_name: str | None = None
    actor_kind: ContributionActorKind
    content: str = Field(min_length=1, max_length=1000)
    # CHG-166: None en comentarios anteriores a la mejora.
    rating: int | None = Field(default=None, ge=1, le=5)
    created_at: datetime


class AidLocationCommentsResponse(ApiModel):
    items: list[AidLocationComment]
    total: int = Field(ge=0)
    # CHG-166: promedio (1 decimal) y cuántos comentarios calificaron.
    rating_average: float | None = Field(default=None, ge=1, le=5)
    rating_count: int = Field(default=0, ge=0)


# CHG-165 — Consola super_admin: verificación y reactivación de
# Centros de Acopio Local (§15, §21-24). Estado operativo y estado de
# verificación son independientes (§25).
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


class AdminAidLocationVerificationDecision(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=1000)


class AdminAidLocationActionReceipt(ApiModel):
    id: UUID
    verification_status: VerificationStatus
    operational_status: AidLocationOperationalStatus
    disabled_at: datetime | None = None
    active_reports_count: int = Field(ge=0)


# CHG-167 — Borrado admin de un comentario (definitivo y auditado).
class AidLocationCommentDeleteReceipt(ApiModel):
    deleted: int = Field(ge=0)


# CHG-170 — Borrado admin del acopio completo desde su ficha.
class AdminAidLocationDeleteReceipt(ApiModel):
    deleted: int = Field(ge=0)


# CHG-044 — Ofertas comunitarias de comida y alojamiento (FEATURE-010).
AidOfferKind = Literal["community_meal", "temporary_shelter"]
AidOfferAvailability = Literal[
    "scheduled", "active", "paused", "fulfilled", "withdrawn", "expired"
]
AidOfferModerationStatus = Literal[
    "under_review", "needs_information", "accepted", "rejected", "archived"
]
AidOfferCapacityUnit = Literal["servings", "spaces"]
MealDistributionMode = Literal["pickup", "delivery", "both"]


class _AidOfferCommonInput(ApiModel):
    """Base privada de una oferta; nunca se serializa en respuestas."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    title: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=20, max_length=1200)
    department: str = Field(min_length=1, max_length=100)
    municipality: str = Field(min_length=1, max_length=100)
    area_reference: str = Field(min_length=3, max_length=300)
    exact_address: str | None = Field(
        default=None, min_length=3, max_length=300
    )
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    related_disaster_id: UUID | None = None
    available_from: datetime
    available_until: datetime
    contact_name: str = Field(min_length=2, max_length=160)
    contact_phone: str | None = Field(
        default=None, max_length=PHONE_MAX_LENGTH, pattern=PHONE_PATTERN
    )
    contact_email: str | None = Field(default=None, max_length=254)
    truth_confirmed: Literal[True]
    contact_consent: Literal[True]
    review_acknowledged: Literal[True]
    public_summary_consent: Literal[True]

    @model_validator(mode="after")
    def _coordinates_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "latitude y longitude deben enviarse juntas o ambas "
                "ausentes"
            )
        return self

    @model_validator(mode="after")
    def _contact_required(self):
        phone = (self.contact_phone or "").strip()
        email = (self.contact_email or "").strip()
        if not phone and not email:
            raise ValueError("se requiere contactPhone o contactEmail")
        return self

    @model_validator(mode="after")
    def _window_valid(self):
        if self.available_until <= self.available_from:
            raise ValueError(
                "availableUntil debe ser posterior a availableFrom"
            )
        return self


class CommunityMealOfferInput(_AidOfferCommonInput):
    kind: Literal["community_meal"]
    servings_available: int = Field(ge=1, le=100_000)
    distribution_mode: MealDistributionMode
    meal_description: str = Field(min_length=5, max_length=500)
    allergen_information: str | None = Field(default=None, max_length=500)
    food_safety_confirmed: Literal[True]


class TemporaryShelterOfferInput(_AidOfferCommonInput):
    kind: Literal["temporary_shelter"]
    spaces_available: int = Field(ge=1, le=1_000)
    shared_space: bool
    accepts_pets: bool | None = None
    accessibility_notes: str | None = Field(default=None, max_length=500)
    shelter_safety_confirmed: Literal[True]


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


class AidOfferOwnerUpdateInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    version: int = Field(ge=1)
    availability_status: (
        Literal["scheduled", "active", "paused", "fulfilled", "withdrawn"]
        | None
    ) = None
    available_units: int | None = Field(default=None, ge=0, le=100_000)
    available_from: datetime | None = None
    available_until: datetime | None = None

    @model_validator(mode="after")
    def _something_to_update(self):
        if (
            self.availability_status is None
            and self.available_units is None
            and self.available_from is None
            and self.available_until is None
        ):
            raise ValueError(
                "la actualización requiere al menos un campo además de "
                "version"
            )
        return self


class CommunityMealOfferDirectoryCard(ApiModel):
    """Tarjeta pública de comida: solo la proyección saneada."""

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
    """Tarjeta pública de alojamiento: solo la proyección saneada."""

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


# CHG-091 — Sugerencias en tiempo real para prevenir duplicados.
# La sugerencia es la tarjeta pública del directorio más la similitud
# calculada por pg_trgm, para que el frontend la proyecte directo a
# sus componentes existentes.
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


class MissingPersonStatusReportInput(ApiModel):
    """Novedad privada sobre una persona; nunca se serializa en
    respuestas ni cambia el estado público al crearse."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    claimed_outcome: Literal["found", "deceased"]
    evidence_description: str = Field(min_length=30, max_length=2000)
    occurred_at: datetime | None = None
    location_description: str | None = Field(default=None, max_length=300)
    truth_confirmed: Literal[True]
    review_acknowledged: Literal[True]

    # CHG-107: 30 caracteres los cumplen treinta letras iguales. Esta
    # regla no juzga el contenido —eso es del equipo de revisión— sino
    # que descarta lo que evidentemente no es una descripción escrita.
    @model_validator(mode="after")
    def _evidence_is_readable(self):
        try:
            validate_community_text(
                self.evidence_description, "evidenceDescription"
            )
        except TextQualityError as error:
            raise ValueError(str(error)) from error
        return self


class AidLocationRatingInput(ApiModel):
    """Valoración privada de un lugar; queda pendiente de revisión."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    rating: int = Field(ge=1, le=5)
    evidence_description: str = Field(min_length=20, max_length=1200)
    truth_confirmed: Literal[True]
    review_acknowledged: Literal[True]


class CommunityContributionReceipt(ApiModel):
    id: UUID
    status: Literal["under_review"]
    actor_kind: ContributionActorKind
    received_at: datetime


# CHG-077 — Proyección pública de las novedades de una persona: qué
# dijeron quienes la vieron, sin identidad del reportante ni fotos.
PersonNoveltyReporterKind = Literal[
    "anonymous", "authenticated", "health_sector"
]


class PersonStatusReportPublic(ApiModel):
    id: UUID
    claimed_outcome: Literal["found", "deceased"]
    evidence_description: str
    location_description: str | None = None
    occurred_at: datetime | None = None
    received_at: datetime
    reporter_kind: PersonNoveltyReporterKind
    moderation_status: Literal["under_review", "accepted"]


class PersonStatusReportsPage(ApiModel):
    person_id: UUID
    public_status: PublicPersonStatus
    items: list[PersonStatusReportPublic] = Field(max_length=100)
    total: int = Field(ge=0)


# CHG-035 — Reporte ciudadano de edificio sin verificar.
BuildingReportType = Literal[
    "residential",
    "commercial",
    "institutional",
    "industrial",
    "mixed_use",
    "other",
]
BuildingSearchStatus = Literal[
    "not_started", "interrupted", "incomplete", "unknown"
]
BuildingOccupancyReport = Literal[
    "unknown", "possibly_occupied", "reported_empty"
]
BuildingPendingReason = Literal[
    "access_blocked",
    "structural_risk_observed",
    "debris",
    "fire_or_smoke",
    "flooding",
    "no_response_team",
    "communication_loss",
    "evacuation",
    "other",
]
BuildingObservedCondition = Literal[
    "obstructed_access",
    "visible_debris",
    "fire_or_smoke",
    "flooding",
    "visible_cracks",
    "partial_collapse_observed",
    "total_collapse_observed",
    "other",
]


class UnverifiedBuildingReportInput(ApiModel):
    """Expediente privado; nunca se serializa en respuestas ni publica
    un punto del mapa al crearse."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    building_reference: str = Field(min_length=2, max_length=180)
    building_type: BuildingReportType
    department: str = Field(min_length=1, max_length=100)
    municipality: str = Field(min_length=1, max_length=100)
    sector: str = Field(min_length=1, max_length=160)
    location_reference: str = Field(min_length=1, max_length=300)
    address: str | None = Field(default=None, max_length=300)
    # Coordenadas exactas privadas: llegan en pareja o no llegan.
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    related_disaster_id: UUID | None = None
    # CHG-092: nombre del evento cuando el usuario escribe en vez de
    # seleccionar; el servicio deduplica o crea. Excluyente con el id.
    related_event_name: str | None = Field(
        default=None, min_length=3, max_length=160
    )
    observed_date: date
    observed_time: str | None = Field(
        default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$"
    )
    search_status: BuildingSearchStatus
    occupancy_report: BuildingOccupancyReport
    pending_reasons: list[BuildingPendingReason] = Field(
        min_length=1, max_length=9
    )
    # CHG-093: detalle obligatorio cuando el motivo es "other".
    pending_reason_detail: str | None = Field(
        default=None, min_length=3, max_length=300
    )
    observed_conditions: list[BuildingObservedCondition] = Field(
        default_factory=list, max_length=8
    )
    observation_description: str = Field(min_length=20, max_length=2000)
    reporter_name: str = Field(min_length=1, max_length=160)
    reporter_role: str = Field(min_length=1, max_length=100)
    reporter_organization: str | None = Field(default=None, max_length=160)
    reporter_phone: str | None = Field(
        default=None, max_length=PHONE_MAX_LENGTH, pattern=PHONE_PATTERN
    )
    reporter_email: str | None = Field(default=None, max_length=254)
    official_report_number: str | None = Field(default=None, max_length=100)
    # CHG-066: instantánea opcional de la ubicación del reportante al
    # enviar; llega en pareja, se cifra y solo la ve super_admin.
    reporter_latitude: float | None = Field(default=None, ge=-90, le=90)
    reporter_longitude: float | None = Field(
        default=None, ge=-180, le=180
    )
    truth_confirmed: Literal[True]
    photo_authorization_confirmed: Literal[True]
    review_acknowledged: Literal[True]

    @model_validator(mode="after")
    def _coordinates_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "latitude y longitude deben enviarse juntas o ambas "
                "ausentes"
            )
        return self

    # CHG-093: el detalle acompaña al motivo "other" en ambos sentidos
    # — obligatorio si está y rechazado si llega sin él.
    @model_validator(mode="after")
    def _other_reason_detail(self):
        has_other = "other" in self.pending_reasons
        has_detail = bool((self.pending_reason_detail or "").strip())
        if has_other and not has_detail:
            raise ValueError(
                "pendingReasonDetail es obligatorio cuando los motivos "
                "incluyen 'other'"
            )
        if has_detail and not has_other:
            raise ValueError(
                "pendingReasonDetail solo aplica cuando los motivos "
                "incluyen 'other'"
            )
        return self

    # CHG-092: o se seleccionó un evento existente (id) o se escribió
    # un nombre para resolver/crear — nunca ambos.
    @model_validator(mode="after")
    def _related_event_exclusive(self):
        if (
            self.related_disaster_id is not None
            and self.related_event_name is not None
        ):
            raise ValueError(
                "relatedDisasterId y relatedEventName son excluyentes"
            )
        return self

    @model_validator(mode="after")
    def _reporter_snapshot_pair(self):
        if (self.reporter_latitude is None) != (
            self.reporter_longitude is None
        ):
            raise ValueError(
                "reporterLatitude y reporterLongitude deben enviarse "
                "juntas o ambas ausentes"
            )
        return self

    @model_validator(mode="after")
    def _contact_required(self):
        phone = (self.reporter_phone or "").strip()
        email = (self.reporter_email or "").strip()
        if not phone and not email:
            raise ValueError(
                "se requiere reporterPhone o reporterEmail"
            )
        return self

    @model_validator(mode="after")
    def _unique_selections(self):
        if len(set(self.pending_reasons)) != len(self.pending_reasons):
            raise ValueError("pendingReasons no admite repetidos")
        if len(set(self.observed_conditions)) != len(
            self.observed_conditions
        ):
            raise ValueError("observedConditions no admite repetidos")
        return self


class UnverifiedBuildingReportReceipt(ApiModel):
    id: UUID
    public_tracking_code: str = Field(min_length=1, max_length=40)
    status: Literal["under_review"]
    received_at: datetime


# CHG-036 — Consola de superadministración (espejo del contrato).
AdminSubmissionKind = Literal[
    "missing_person_report",
    "unverified_building_report",
    # CHG-162 (F2) — «Mi casita partida» en el tema Infraestructura.
    "damaged_home_report",
    "person_status_report",
    "aid_location_rating",
    "collection_center_registration",
    "collection_point_registration",
    # CHG-044 — ofertas comunitarias en la misma bandeja.
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
AdminFieldClassification = Literal["public", "private", "protected"]
AdminAuditResult = Literal["success", "denied", "failed"]


class AdminSubmissionSummary(ApiModel):
    """Resumen de bandeja: sin PII (títulos genéricos y zona municipal)."""

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
    classification: AdminFieldClassification
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


class AdminFieldChange(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    field: str = Field(pattern=r"^[a-z][A-Za-z0-9]{0,79}$")
    value: str | None = Field(default=None, max_length=4000)


class AdminSubmissionEditInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=500)
    changes: list[AdminFieldChange] = Field(min_length=1, max_length=50)


class AdminDecisionInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    expected_version: int = Field(ge=1)
    action: Literal["accept", "reject", "request_changes"]
    reason: str = Field(min_length=10, max_length=500)


class AdminVersionedReasonInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=500)


class AdminMutationReceipt(ApiModel):
    id: UUID
    status: AdminModerationStatus
    version: int = Field(ge=1)
    audit_event_id: UUID
    updated_at: datetime


class AdminSubmissionDeleteReceipt(ApiModel):
    # CHG-159 — recibo del borrado definitivo: la fila ya no existe,
    # así que no hay estado ni versión que devolver.
    id: UUID
    audit_event_id: UUID
    deleted_at: datetime


# CHG-154 — Gestión admin de registros de personas: ocultamiento
# reversible (nada se borra) y edición acotada. `hidden_at` presente ⇔
# invisible en toda la plataforma, a la espera del futuro apartado de
# borrado definitivo.
AdminPeopleVisibility = Literal["visible", "hidden", "all"]


class AdminPersonRecord(ApiModel):
    id: UUID
    display_name: str = Field(min_length=1)
    status: HumanStatus
    location: str = Field(min_length=1)
    related_event: str = Field(min_length=1)
    latitude: float | None = None
    longitude: float | None = None
    # Con caso ciudadano vinculado el estado lo derivan las novedades;
    # la consola no lo edita a mano.
    has_linked_case: bool
    source: SourceReference
    created_at: datetime
    updated_at: datetime
    hidden_at: datetime | None = None
    hidden_by: str | None = None


class AdminPeoplePage(ApiModel):
    items: list[AdminPersonRecord]
    total: int = Field(ge=0)


class AdminPersonUpdateInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    display_name: str | None = Field(
        default=None, min_length=2, max_length=160
    )
    location: str | None = Field(default=None, min_length=2, max_length=300)
    related_event: str | None = Field(
        default=None, min_length=2, max_length=200
    )
    status: HumanStatus | None = None


class AdminEvidenceAccessGrant(ApiModel):
    url: str = Field(min_length=1)
    expires_at: datetime
    audit_event_id: UUID


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


# CHG-066 — Presencia de visitantes (consentimiento explícito; solo
# lectura por la consola super_admin).
VisitorPlatform = Literal["web", "android", "ios"]


class VisitorPresenceInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    presence_id: UUID
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_meters: float | None = Field(default=None, ge=0, le=100_000)
    platform: VisitorPlatform = "web"


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


# CHG-069 — "Mi espacio": reportes propios con novedades de terceros y
# alertas ciudadanas de voluntariado.
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


# CHG-125 — «Necesitamos ayuda»: solicitud pública de emergencia con
# vigencia en horas. Sin datos de contacto: descripción, dirección y
# coordenadas son públicas por diseño (DEC-125-12).
HELP_REQUEST_MIN_HOURS = 1
# CHG-130: la vigencia se expresa en horas (1-72) o días (1-30); el
# cliente convierte días a horas y el tope del contrato es 30 días.
HELP_REQUEST_MAX_HOURS = 720
# CHG-146: la descripción de una solicitud de ayuda es breve por
# naturaleza («Necesito ayuda urgente aquí», «Atrapados bajo
# escombros»). El reporte de persona pide 5 palabras distintas; aquí 3
# basta para descartar basura sin rechazar emergencias reales.
HELP_REQUEST_MIN_DISTINCT_WORDS = 3


class HelpRequestInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    description: str = Field(min_length=10, max_length=1000)
    address: str = Field(min_length=5, max_length=300)
    # CHG-127: la dirección escrita basta; las coordenadas son
    # opcionales, pero si viajan van las dos (DEC-127-01).
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    duration_hours: int = Field(
        ge=HELP_REQUEST_MIN_HOURS, le=HELP_REQUEST_MAX_HOURS
    )
    # CHG-131: radio de aviso en la app instalada, en km a la redonda
    # del punto; exige coordenadas.
    notification_radius_km: int | None = Field(default=None, ge=1, le=100)
    # CHG-137: clientes con bundle anterior a CHG-136 aún adjuntan la
    # instantánea del reportante. Se ACEPTA y se DESCARTA: jamás se
    # almacena, se registra ni se usa (la solicitud es pública por
    # diseño, DEC-125-12). Sin esto, `extra="forbid"` tumbaba el envío
    # de esos clientes con un 422 por campos que el formulario no pide.
    reporter_latitude: float | None = Field(
        default=None, ge=-90, le=90, exclude=True
    )
    reporter_longitude: float | None = Field(
        default=None, ge=-180, le=180, exclude=True
    )

    @model_validator(mode="after")
    def _coordinates_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "latitude y longitude deben enviarse juntas o ambas "
                "ausentes"
            )
        return self

    @model_validator(mode="after")
    def _radius_needs_coordinates(self):
        if self.notification_radius_km is not None and self.latitude is None:
            raise ValueError(
                "notificationRadiusKm exige latitude y longitude: sin "
                "punto no hay distancias que medir"
            )
        return self

    # CHG-107: descarta descripciones que evidentemente no son texto
    # escrito (repetición, palabras pegadas, vocabulario nulo).
    # CHG-146: umbral de palabras distintas menor — la solicitud de
    # ayuda es breve por naturaleza.
    @model_validator(mode="after")
    def _description_is_readable(self):
        try:
            validate_community_text(
                self.description,
                "description",
                min_distinct_words=HELP_REQUEST_MIN_DISTINCT_WORDS,
            )
        except TextQualityError as error:
            raise ValueError(str(error)) from error
        return self


# CHG-148 — Voluntario anónimo que se ofrece a atender una solicitud
# desde el mapa. Solo datos personales del PROPIO voluntario (no de
# terceros); privados, solo super_admin (DEC-148-01). El público solo
# ve el contador que aumenta. La foto viaja en el multipart.
class HelpRequestVolunteerInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    name: str = Field(min_length=1, max_length=160)
    phone: str | None = Field(
        default=None, max_length=PHONE_MAX_LENGTH, pattern=PHONE_PATTERN
    )
    email: str | None = Field(default=None, max_length=254)
    # CHG-193: el formulario nuevo advierte que nombre, teléfono y foto
    # se comparten con quien pidió la ayuda. Falso por defecto para que
    # un cliente viejo —o un envío sin la advertencia— no comparta nada.
    shares_contact: bool = False

    @model_validator(mode="after")
    def _name_is_readable(self):
        # El nombre es texto real; no repetición ni cadena pegada. No se
        # exigen varias palabras (un nombre puede ser uno solo).
        try:
            validate_community_text(self.name, "name", min_distinct_words=1)
        except TextQualityError as error:
            raise ValueError(str(error)) from error
        return self


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
    # CHG-190: true solo para la cuenta que creó la solicitud; sin
    # sesión siempre false. No expone la identidad de nadie más.
    created_by_me: bool = False
    photo_url: str | None = None
    # CHG-180: la puntuación viaja con la solicitud (el mapa la fusiona
    # en cliente), igual que en las ofertas de comida.
    comment_rating_average: float | None = Field(default=None, ge=1, le=5)
    comment_rating_count: int = Field(default=0, ge=0)


class HelpRequestPage(ApiModel):
    items: list[ActiveHelpRequest] = Field(max_length=50)
    total: int = Field(ge=0)
    generated_at: datetime


# CHG-193 — Quien pidió ayuda ve quién la atiende. Cada fila dice si esa
# persona consintió compartir sus datos: sin consentimiento viaja el
# hecho de que atiende y nada más (las anteriores a CHG-193 se
# registraron bajo otra promesa y nunca se identifican). El correo sigue
# siendo privado del super_admin en todos los casos.
class HelpRequestAttender(ApiModel):
    id: UUID
    kind: Literal["account", "volunteer"]
    joined_at: datetime
    shares_contact: bool
    name: str | None = None
    phone: str | None = None
    photo_url: str | None = None


class HelpRequestAttendersPage(ApiModel):
    items: list[HelpRequestAttender]
    total: int = Field(ge=0)
    generated_at: datetime


class HelpRequestAttendInput(ApiModel):
    """Lo que el navegador manda al atender. `sharesIdentity` es el
    aviso aceptado: al atender, tu nombre y tu teléfono se comparten con
    quien pidió la ayuda."""

    shares_identity: bool = False
    # Solo el gateway los rellena, desde la sesión: el navegador no
    # decide con qué nombre figura nadie.
    name: str | None = Field(default=None, max_length=161)
    phone: str | None = Field(default=None, max_length=30)


class HelpRequestAttendReceipt(ApiModel):
    id: UUID
    attenders_count: int = Field(ge=1)
    attending: bool


# CHG-163 — «Ofrecer comida»: oferta comunitaria de alimentos con las
# reglas de «Necesitamos ayuda» (CHG-125/127/130/131): creación
# anónima o con cuenta, vigencia server-side 1-720 horas, coordenadas
# opcionales en par y radio de aviso que exige punto. Sin datos de
# contacto: descripción, dirección y coordenadas son públicas por
# diseño (mostrar dónde hay comida es el propósito).
FOOD_OFFER_MIN_HOURS = HELP_REQUEST_MIN_HOURS
FOOD_OFFER_MAX_HOURS = HELP_REQUEST_MAX_HOURS
# La descripción de una oferta es breve por naturaleza («Sancocho
# comunitario para 40 personas»): mismo umbral que las solicitudes.
FOOD_OFFER_MIN_DISTINCT_WORDS = HELP_REQUEST_MIN_DISTINCT_WORDS


class FoodOfferInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    description: str = Field(min_length=10, max_length=1000)
    address: str = Field(min_length=5, max_length=300)
    # La dirección escrita basta; las coordenadas son opcionales, pero
    # si viajan van las dos (regla DEC-127-01).
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    duration_hours: int = Field(
        ge=FOOD_OFFER_MIN_HOURS, le=FOOD_OFFER_MAX_HOURS
    )
    # Radio de aviso en la app instalada, en km a la redonda del punto;
    # exige coordenadas (regla DEC-131-01).
    notification_radius_km: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def _coordinates_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "latitude y longitude deben enviarse juntas o ambas "
                "ausentes"
            )
        return self

    @model_validator(mode="after")
    def _radius_needs_coordinates(self):
        if self.notification_radius_km is not None and self.latitude is None:
            raise ValueError(
                "notificationRadiusKm exige latitude y longitude: sin "
                "punto no hay distancias que medir"
            )
        return self

    @model_validator(mode="after")
    def _description_is_readable(self):
        try:
            validate_community_text(
                self.description,
                "description",
                min_distinct_words=FOOD_OFFER_MIN_DISTINCT_WORDS,
            )
        except TextQualityError as error:
            raise ValueError(str(error)) from error
        return self


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


# CHG-205 — «Ofrecer alojamiento temporal»: gemela de la oferta de
# comida. Mismos límites de texto y vigencia; se le suman los datos que
# solo tienen sentido en una casa que se abre.
SHELTER_OFFER_MIN_HOURS = FOOD_OFFER_MIN_HOURS
SHELTER_OFFER_MAX_HOURS = FOOD_OFFER_MAX_HOURS
SHELTER_OFFER_MIN_DISTINCT_WORDS = FOOD_OFFER_MIN_DISTINCT_WORDS
SHELTER_OFFER_MIN_SPACES = 1
SHELTER_OFFER_MAX_SPACES = 1000
SHELTER_OFFER_MAX_ACCESSIBILITY_NOTES = 500


class ShelterOfferInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    description: str = Field(min_length=10, max_length=1000)
    address: str = Field(min_length=5, max_length=300)
    # Igual que en la oferta de comida: la dirección escrita basta y las
    # coordenadas viajan juntas o no viajan.
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    duration_hours: int = Field(
        ge=SHELTER_OFFER_MIN_HOURS, le=SHELTER_OFFER_MAX_HOURS
    )
    notification_radius_km: int | None = Field(default=None, ge=1, le=100)
    # Cuántas personas pueden dormir.
    spaces_available: int = Field(
        ge=SHELTER_OFFER_MIN_SPACES, le=SHELTER_OFFER_MAX_SPACES
    )
    # Si el espacio se comparte. Obligatorio a propósito: no tener
    # respuesta es peor que cualquiera de las dos.
    shared_space: bool
    accepts_pets: bool = False
    accessibility_notes: str | None = Field(
        default=None, max_length=SHELTER_OFFER_MAX_ACCESSIBILITY_NOTES
    )

    @model_validator(mode="after")
    def _coordinates_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "latitude y longitude deben enviarse juntas o ambas "
                "ausentes"
            )
        return self

    @model_validator(mode="after")
    def _radius_needs_coordinates(self):
        if self.notification_radius_km is not None and self.latitude is None:
            raise ValueError(
                "notificationRadiusKm exige latitude y longitude: sin "
                "punto no hay distancias que medir"
            )
        return self

    @model_validator(mode="after")
    def _description_is_readable(self):
        try:
            validate_community_text(
                self.description,
                "description",
                min_distinct_words=SHELTER_OFFER_MIN_DISTINCT_WORDS,
            )
        except TextQualityError as error:
            raise ValueError(str(error)) from error
        return self

    @model_validator(mode="after")
    def _accessibility_notes_not_blank(self):
        # Una nota en blanco es ruido: se trata como ausencia.
        if self.accessibility_notes is not None:
            cleaned = self.accessibility_notes.strip()
            self.accessibility_notes = cleaned or None
        return self


class ShelterOfferReceipt(ApiModel):
    id: UUID
    public_code: str
    status: Literal["active"]
    received_at: datetime
    expires_at: datetime


class ActiveShelterOffer(ApiModel):
    id: UUID
    description: str
    address: str
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    notification_radius_km: int | None = Field(default=None, ge=1, le=100)
    spaces_available: int = Field(
        ge=SHELTER_OFFER_MIN_SPACES, le=SHELTER_OFFER_MAX_SPACES
    )
    shared_space: bool
    accepts_pets: bool = False
    accessibility_notes: str | None = None
    created_at: datetime
    expires_at: datetime
    # Como en la oferta de comida (CHG-176): el mapa fusiona en cliente,
    # así que la puntuación viaja con la oferta.
    comment_rating_average: float | None = Field(default=None, ge=1, le=5)
    comment_rating_count: int = Field(default=0, ge=0)


class ShelterOfferPage(ApiModel):
    items: list[ActiveShelterOffer] = Field(max_length=50)
    total: int = Field(ge=0)
    generated_at: datetime


# CHG-138 — Gestión de solicitudes desde la superadministración: se ve
# TODO (activas y expiradas) y se puede borrar una a una o vaciar.
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


# CHG-148 — Voluntario anónimo visto por el super_admin: PII descifrada,
# nunca sale de la consola. La foto se sirve por un endpoint admin.
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


class MyReportNovelty(ApiModel):
    """Novedad que OTRA persona aportó sobre un caso propio."""

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


# CHG-082 — Huella de cambios de la portada para el refresco en vivo.
class ChangeSignal(ApiModel):
    signal: str = Field(min_length=1, max_length=64)
    generated_at: datetime


class HealthStatus(BaseModel):
    status: Literal["ok"]
    service: str


class ServiceVersion(BaseModel):
    # CHG-111: "unknown" cuando la imagen se construyó sin
    # GIT_REVISION; nunca se inventa una revisión.
    service: str
    revision: str


# CHG-161 — «La mulera» y «La lanchera»: transporte de insumos con
# trazabilidad entre un acopio local (origen) y un receptor (destino).
TransportKind = Literal["mule", "boat"]
TransportStatus = Literal[
    "registered", "in_transit", "arrived", "cancelled"
]


# CHG-171 §27: el conductor debe quedar identificado — subconjunto
# adulto del catálogo CHG-073 (sin «Sin documento» ni documentos de
# menores de edad).
TransportDriverDocumentType = Literal[
    "Cédula de ciudadanía",
    "Cédula de extranjería",
    "Pasaporte",
    "Permiso por protección temporal (PPT)",
]

# CHG-173 §: catálogo cerrado de embarcaciones fluviales.
TransportVesselType = Literal[
    "Lancha",
    "Chalupa",
    "Bongo",
    "Planchón",
    "Ferri",
]

# CHG-171 §32-33: placa normalizada (mayúsculas, sin espacios/guiones).
_PLATE_PATTERN = re.compile(r"^[A-Z0-9]{5,10}$")
# CHG-173: la matrícula fluvial no sigue el formato de placa terrestre
# (p. ej. «CP-05-1234»), así que se normaliza igual pero con un rango
# más ancho.
_VESSEL_REGISTRATION_PATTERN = re.compile(r"^[A-Z0-9]{4,15}$")


def _normalize_plate(value: str) -> str:
    plate = re.sub(r"[\s-]+", "", value.strip().upper())
    if not _PLATE_PATTERN.fullmatch(plate):
        raise ValueError(
            "La placa debe tener entre 5 y 10 letras o números."
        )
    return plate


def _normalize_vessel_registration(value: str) -> str:
    registration = re.sub(r"[\s-]+", "", value.strip().upper())
    if not _VESSEL_REGISTRATION_PATTERN.fullmatch(registration):
        raise ValueError(
            "La matrícula de la embarcación debe tener entre 4 y 15 "
            "letras o números."
        )
    return registration


class HumanitarianTransportInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    kind: TransportKind
    origin_municipality: str = Field(min_length=1, max_length=100)
    destination_municipality: str = Field(min_length=1, max_length=100)
    origin_location_id: UUID
    destination_location_id: UUID
    supplies_summary: str | None = Field(default=None, max_length=1000)
    # CHG-171 §26-29: conductor obligatorio en registros nuevos (los
    # históricos quedan NULL, §62). Quien registra ES el conductor.
    driver_full_name: str = Field(min_length=3, max_length=160)
    driver_document_type: TransportDriverDocumentType
    # §28: texto, nunca numérico (conserva ceros y letras).
    driver_document_number: str = Field(min_length=3, max_length=20)
    driver_phone: str = Field(
        max_length=PHONE_MAX_LENGTH, pattern=PHONE_PATTERN
    )
    # CHG-171 §31-35: identificación del vehículo terrestre. CHG-173:
    # opcionales en el esquema porque solo la mulera las trae; el
    # validador de abajo las exige (y prohíbe las de la lancha) según
    # el tipo, para que ningún registro mezcle los dos mundos.
    tractor_plate: str | None = Field(
        default=None, min_length=5, max_length=12
    )
    trailer_plate: str | None = Field(
        default=None, min_length=5, max_length=12
    )
    # CHG-173: identidad propia de la embarcación (solo la lanchera).
    vessel_registration: str | None = Field(
        default=None, min_length=4, max_length=20
    )
    vessel_name: str | None = Field(
        default=None, min_length=3, max_length=120
    )
    vessel_type: TransportVesselType | None = None
    # Compartido por los dos medios, con etiqueta propia en cada uno.
    vehicle_visible_characteristics: str = Field(
        min_length=5, max_length=500
    )

    @field_validator("driver_full_name", "driver_document_number")
    @classmethod
    def _trim_driver_field(cls, value: str) -> str:
        trimmed = value.strip()
        if len(trimmed) < 3:
            raise ValueError("El dato del conductor está incompleto.")
        return trimmed

    @field_validator("tractor_plate", "trailer_plate")
    @classmethod
    def _plate(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_plate(value)

    @field_validator("vessel_registration")
    @classmethod
    def _vessel_registration(cls, value: str | None) -> str | None:
        return (
            None if value is None else _normalize_vessel_registration(value)
        )

    @field_validator("vessel_name")
    @classmethod
    def _vessel_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if len(trimmed) < 3:
            raise ValueError(
                "Escribe el nombre de la embarcación."
            )
        return trimmed

    # CHG-173: el contrato es condicional por tipo. La mulera va con
    # sus dos placas; la lanchera, con matrícula, nombre y tipo de
    # embarcación. Enviar los campos del otro medio es un error de
    # cliente, no un dato que se ignora en silencio.
    @model_validator(mode="after")
    def _vehicle_identity_matches_kind(
        self,
    ) -> "HumanitarianTransportInput":
        if self.kind == "mule":
            missing = [
                name
                for name, value in (
                    ("tractorPlate", self.tractor_plate),
                    ("trailerPlate", self.trailer_plate),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    "La mulera exige la placa del tractocamión y la del "
                    "tráiler."
                )
            if (
                self.vessel_registration is not None
                or self.vessel_name is not None
                or self.vessel_type is not None
            ):
                raise ValueError(
                    "La mulera no lleva datos de embarcación."
                )
        else:
            if (
                self.vessel_registration is None
                or self.vessel_name is None
                or self.vessel_type is None
            ):
                raise ValueError(
                    "La lanchera exige la matrícula, el nombre y el tipo "
                    "de la embarcación."
                )
            if (
                self.tractor_plate is not None
                or self.trailer_plate is not None
            ):
                raise ValueError(
                    "La lanchera no lleva placas de tractocamión ni de "
                    "tráiler."
                )
        return self


class HumanitarianTransportReceipt(ApiModel):
    id: UUID
    kind: TransportKind
    status: TransportStatus
    origin_location_id: UUID
    destination_location_id: UUID
    created_at: datetime


# CHG-171 §50: catálogo de ciudades (sembrado + municipios reales de
# acopios publicados).
class TransportCity(ApiModel):
    name: str
    department: str


class TransportCitiesResponse(ApiModel):
    items: list[TransportCity]
    total: int = Field(ge=0)


# CHG-171 (GPS) — Hitos y posiciones del viaje del conductor.
class TransportPositionInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


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
    # CHG-173: identificación visible de la embarcación; como las
    # placas, es pública (lo del conductor nunca lo es).
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
    """Solicitud vista por el centro responsable (§10-§12).

    Es una vista administrativa autorizada, así que incluye los datos
    del conductor; jamás se reutiliza en vistas públicas (§13, §63).
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


# §68: resumen transaccional — estado de los DOS centros y la acción
# disponible. El código solo viaja hacia el centro local responsable.
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
    # §52: true cuando ya existía el proceso (doble clic).
    reused: bool = False


# Vista de la Mulera: su transporte y el estado de la aceptación, SIN
# el código —lo entrega el Centro Local por fuera (§32)—.
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


class TransportRequestDecisionInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    decision: Literal["accept", "decline"]


class RouteCodeInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    code: str = Field(min_length=4, max_length=40)


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


# CHG-162 — «Mi casita partida»: informe de hogar en malas condiciones.
# CHG-182 — Medios para recibir ayuda directa. Catálogo cerrado: la
# plataforma no verifica el dato ni intermedia la transferencia, así que
# al menos acota qué se puede escribir.
DamagedHomeDonationChannel = Literal[
    "Nequi", "Daviplata", "Bancolombia", "Movii", "Otro"
]


# CHG-201 — DEC-201-01: el vídeo de la casita solo puede apuntar a
# TikTok, y por https. Sin esta lista cerrada el campo sería un canal
# para publicar cualquier enlace junto a un medio para recibir dinero
# (CHG-182), que es la mitad que le falta a una estafa.
DAMAGED_HOME_VIDEO_HOSTS = frozenset(
    {
        "tiktok.com",
        "www.tiktok.com",
        "m.tiktok.com",
        "vm.tiktok.com",
        "vt.tiktok.com",
    }
)


def normalized_tiktok_url(value: str | None) -> str | None:
    """Devuelve la URL limpia, o lanza ValueError si no es de TikTok.

    Acepta None y la cadena vacía como «no hay vídeo».
    """
    if value is None:
        return None
    limpio = value.strip()
    if not limpio:
        return None
    partes = urlsplit(limpio)
    if partes.scheme != "https":
        raise ValueError("el enlace del vídeo debe empezar por https://")
    # `hostname` ya viene en minúsculas y sin puerto ni credenciales;
    # comparar contra netloc dejaría pasar «tiktok.com@malicioso».
    if partes.hostname not in DAMAGED_HOME_VIDEO_HOSTS:
        raise ValueError(
            "el enlace del vídeo debe ser de TikTok "
            "(tiktok.com, vm.tiktok.com o vt.tiktok.com)"
        )
    if partes.username or partes.password:
        raise ValueError("el enlace del vídeo no puede llevar credenciales")
    return limpio


class DamagedHomeReportInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    description: str = Field(min_length=10, max_length=1000)
    department: str = Field(min_length=1, max_length=100)
    municipality: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=3, max_length=300)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    # CHG-182: cuántas personas viven en la casa. Obligatorio en las
    # publicaciones nuevas: es lo que dimensiona la ayuda.
    household_size: int = Field(ge=1, le=60)
    # CHG-182: medio para recibir ayuda directa. Opcional, pero si se
    # declara el canal hay que decir a dónde se transfiere (y al revés).
    donation_channel: DamagedHomeDonationChannel | None = None
    donation_reference: str | None = Field(
        default=None, min_length=4, max_length=60
    )
    # CHG-201: enlace opcional a un vídeo de TikTok (DEC-201-01).
    video_url: str | None = Field(default=None, max_length=300)

    @field_validator("video_url")
    @classmethod
    def _video_is_tiktok(cls, value: str | None) -> str | None:
        return normalized_tiktok_url(value)

    @model_validator(mode="after")
    def _coordinates_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "latitude y longitude deben enviarse juntas o ambas "
                "ausentes"
            )
        return self

    @model_validator(mode="after")
    def _donation_pair(self):
        canal = self.donation_channel
        referencia = (
            self.donation_reference.strip()
            if self.donation_reference
            else None
        )
        if (canal is None) != (referencia is None):
            raise ValueError(
                "donationChannel y donationReference deben enviarse "
                "juntos o ambos ausentes"
            )
        return self


class DamagedHomeReportReceipt(ApiModel):
    id: UUID
    # CHG-182: código público para citar la casita en avisos y correos.
    public_code: str | None = Field(default=None, max_length=40)
    created_at: datetime


# CHG-182 — Casita publicada, tal como la ve el mapa y su ficha.
class ActiveDamagedHome(ApiModel):
    id: UUID
    public_code: str | None = None
    description: str
    department: str
    municipality: str
    address: str
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    household_size: int | None = Field(default=None, ge=1, le=60)
    donation_channel: DamagedHomeDonationChannel | None = None
    donation_reference: str | None = Field(default=None, max_length=60)
    # CHG-201: vídeo de TikTok, si la familia lo dejó. Público, como las
    # fotos: lo ve cualquiera que abra la ficha, con cuenta o sin ella.
    video_url: str | None = Field(default=None, max_length=300)
    created_at: datetime
    updated_at: datetime
    # Rutas relativas de las fotografías públicas (derivadas, sin EXIF).
    photo_urls: list[str] = Field(default_factory=list, max_length=5)
    comment_rating_average: float | None = Field(default=None, ge=1, le=5)
    comment_rating_count: int = Field(default=0, ge=0)


class DamagedHomePage(ApiModel):
    items: list[ActiveDamagedHome] = Field(max_length=50)
    total: int = Field(ge=0)
    generated_at: datetime


# CHG-182 — Casita propia en «Mi espacio», con los comentarios que su
# dueña todavía no ha leído.
class MyDamagedHome(ActiveDamagedHome):
    published: bool
    unread_comments: int = Field(default=0, ge=0)
    comments_count: int = Field(default=0, ge=0)


class MyDamagedHomesResponse(ApiModel):
    items: list[MyDamagedHome] = Field(max_length=100)
    total: int = Field(ge=0)
    unread_total: int = Field(default=0, ge=0)


class DamagedHomeComplaintReceipt(ApiModel):
    damaged_home_id: UUID
    reports_count: int = Field(ge=1)
    under_observation: bool
    disabled: bool = False


class DamagedHomeDeleteReceipt(ApiModel):
    deleted: int = Field(ge=0)


# ---------------------------------------------------------------------------
# CHG-208 — Monitoreo sísmico y red privada de emergencia.
# ---------------------------------------------------------------------------

SeismicSeverityLevel = Literal["STRONG", "MODERATE", "LIGHT"]
SeismicAlertStatus = Literal["ACTIVE", "SAFE_CONFIRMED", "EXPIRED"]
EmergencyContactStatus = Literal[
    "PENDING", "ACCEPTED", "REJECTED", "REVOKED"
]


class SeismicZoneView(ApiModel):
    id: UUID
    severity_level: SeismicSeverityLevel
    # 'SGC_INSTRUMENTAL', 'PROVISIONAL_ESTIMATE' o 'SIMULATED': el
    # origen viaja siempre para que la interfaz jamás presente una
    # estimación propia como dato oficial (spec §22).
    source: Literal[
        "SGC_INSTRUMENTAL", "PROVISIONAL_ESTIMATE", "SIMULATED"
    ]
    title: str
    description: str
    geometry: dict


class SeismicEventView(ApiModel):
    id: UUID
    source: Literal["SGC", "SIMULATED"]
    source_event_id: str
    magnitude: float = Field(ge=-2, le=10)
    depth_km: float | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    origin_time_utc: datetime
    processing_status: Literal[
        "SEISMIC_DATA_PRELIMINARY", "SEISMIC_DATA_INSTRUMENTAL"
    ]
    is_simulated: bool = False
    simulated_banner: str | None = None
    description: str | None = None
    pending_instrumental_notice: str | None = None
    zones: list[SeismicZoneView] = Field(default_factory=list)


class SeismicEventsResponse(ApiModel):
    events: list[SeismicEventView] = Field(max_length=20)
    generated_at: datetime


class SeismicAffectedMarker(ApiModel):
    # Redondeada (~1,1 km) para el público; también para los marcadores
    # identificados — la coordenada exacta solo viaja en el panel
    # autorizado y auditado (spec §42/§50).
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    severity_level: SeismicSeverityLevel
    status: SeismicAlertStatus
    identified: bool = False
    display_name: str | None = None
    alert_id: UUID | None = None
    is_self: bool = False


class SeismicAffectedResponse(ApiModel):
    event_id: UUID
    markers: list[SeismicAffectedMarker] = Field(max_length=500)
    generated_at: datetime


class SeismicSettingsView(ApiModel):
    enabled: bool
    max_contacts: int = Field(default=5, ge=1, le=5)


class SeismicSettingsUpdate(ApiModel):
    enabled: bool
    # La pone el gateway desde la sesión (patrón CHG-193); el navegador
    # nunca decide con qué nombre figura nadie.
    display_name: str | None = Field(default=None, max_length=161)


class EmergencyContactInput(ApiModel):
    first_names: str = Field(min_length=1, max_length=80)
    last_names: str = Field(min_length=1, max_length=80)
    document_type: str = Field(min_length=1, max_length=60)
    document_number: str = Field(min_length=3, max_length=30)
    phone: str = Field(
        min_length=7,
        max_length=PHONE_MAX_LENGTH,
        pattern=PHONE_PATTERN,
    )

    @field_validator("document_type")
    @classmethod
    def documento_permitido(cls, value: str) -> str:
        if value not in DOCUMENT_TYPES:
            raise ValueError(
                "documentType debe ser uno de los tipos permitidos"
            )
        return value


class EmergencyContactView(ApiModel):
    id: UUID
    status: EmergencyContactStatus
    display_name: str
    linked: bool = False
    created_at: datetime


class EmergencyContactsResponse(ApiModel):
    contacts: list[EmergencyContactView] = Field(max_length=5)
    max_contacts: int = Field(default=5)


class EmergencyInvitationView(ApiModel):
    id: UUID
    owner_display_name: str
    created_at: datetime
    # 'document' o 'phone': con qué se reforzó la coincidencia.
    match_strength: Literal["document", "phone"] | None = None


class EmergencyInvitationsResponse(ApiModel):
    invitations: list[EmergencyInvitationView] = Field(max_length=20)


class EmergencyInvitationMatchInput(ApiModel):
    # Identidad puesta por el gateway desde la sesión.
    display_name: str = Field(min_length=1, max_length=161)
    phone: str | None = Field(default=None, max_length=PHONE_MAX_LENGTH)
    # Documento tecleado voluntariamente por la persona para reforzar
    # su coincidencia; jamás se guarda en claro.
    document_number: str | None = Field(default=None, max_length=30)


class EmergencyInvitationRespondInput(EmergencyInvitationMatchInput):
    accept: bool


class MySeismicAlertView(ApiModel):
    id: UUID
    event_id: UUID
    status: SeismicAlertStatus
    severity_level: SeismicSeverityLevel
    magnitude: float
    requires_confirmation: bool
    is_simulated: bool
    created_at: datetime


class MySeismicAlertsResponse(ApiModel):
    alerts: list[MySeismicAlertView] = Field(max_length=20)


class ConfirmSafeInput(ApiModel):
    event_id: UUID | None = None


class ConfirmSafeReceipt(ApiModel):
    confirmed: int = Field(ge=0)
    confirmed_at: datetime


class EmergencyPanelView(ApiModel):
    """Lo que ve SOLO un contacto aceptado (spec §48): nunca incluye
    el documento de identidad (spec §49)."""

    alert_id: UUID
    display_name: str
    status: SeismicAlertStatus
    magnitude: float
    origin_time_utc: datetime
    severity_level: SeismicSeverityLevel
    zone_title: str
    latitude: float
    longitude: float
    accuracy_meters: float | None = None
    located_at: datetime | None = None
    resolved_address: str | None = None
    alert_created_at: datetime
    safe_confirmed_at: datetime | None = None
    is_simulated: bool = False


class SeismicSimulationZoneInput(ApiModel):
    severity_level: SeismicSeverityLevel
    geometry: dict


class SeismicSimulationInput(ApiModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    magnitude: float = Field(ge=0, le=10)
    depth_km: float | None = Field(default=None, ge=0, le=800)
    description: str | None = Field(default=None, max_length=300)
    # Spec §67: por defecto un simulacro SOLO alcanza cuentas de
    # prueba; alcanzar cuentas reales exige decisión explícita.
    notify_real_users: bool = False
    zones: list[SeismicSimulationZoneInput] | None = Field(
        default=None, max_length=3
    )


class SeismicSimulationReceipt(ApiModel):
    event_id: UUID
    source_event_id: str
    zones_created: int = Field(ge=0)
    alerts_activated: int = Field(ge=0)
    banner: str


class SeismicTestAccountInput(ApiModel):
    account_id: UUID
    display_name: str = Field(min_length=1, max_length=161)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class SeismicTestRelationInput(ApiModel):
    owner_account_id: UUID
    contact_account_id: UUID


class SeismicTestAccountsInput(ApiModel):
    accounts: list[SeismicTestAccountInput] = Field(
        min_length=1, max_length=12
    )
    relations: list[SeismicTestRelationInput] = Field(
        default_factory=list, max_length=40
    )


class SeismicTestAccountsReceipt(ApiModel):
    accounts_configured: int = Field(ge=0)
    relations_created: int = Field(ge=0)
