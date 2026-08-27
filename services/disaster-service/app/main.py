import asyncio as _asyncio
import base64
import hashlib
import json as _json
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
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
from . import seismic as seismic_rules
from . import seismic_ingest
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
    AdminSubmissionDeleteReceipt,
    AdminSubmissionTheme,
    AdminPeoplePage,
    AdminPeopleVisibility,
    AdminPersonRecord,
    AdminPersonUpdateInput,
    AdminSubmissionDetail,
    AdminSubmissionEditInput,
    AdminSubmissionKind,
    AdminSubmissionPage,
    AdminSubmissionSummary,
    AdminVersionedReasonInput,
    AidLocationAvailability,
    AidLocationInput,
    AID_LOCATION_KINDS_REQUIRING_ACCOUNT,
    DamagedHomeReportInput,
    ActiveDamagedHome,
    DamagedHomePage,
    DamagedHomeComplaintReceipt,
    DamagedHomeDeleteReceipt,
    MyDamagedHome,
    MyDamagedHomesResponse,
    HelpRequestReportReceipt,
    DamagedHomeReportReceipt,
    ActiveTransport,
    ActiveTransportsResponse,
    HumanitarianTransportInput,
    HumanitarianTransportReceipt,
    TransportCitiesResponse,
    TransportCity,
    TransportJourneyReceipt,
    TransportPositionInput,
    # CHG-174: aceptación de ruta Centro Local ↔ Mulera.
    MyTransport,
    MyTransportsResponse,
    RouteAcceptanceReceipt,
    RouteCodeInput,
    RouteCodeValidationReceipt,
    TransportCenterRequest,
    TransportCenterRequestsResponse,
    TransportRequestDecisionInput,
    TransportRequestDecisionReceipt,
    TransportRouteCodeReceipt,
    TransportRouteState,
    TransportRouteStatesResponse,
    AidLocationParentCandidate,
    AidLocationParentCandidatesResponse,
    AidLocationRatingInput,
    AidLocationCommentDeleteReceipt,
    FoodOfferDeleteReceipt,
    FoodOfferReportReceipt,
    ShelterOfferReportReceipt,
    ShelterOfferDeleteReceipt,
    AdminAidLocationActionReceipt,
    AdminAidLocationDeleteReceipt,
    AdminAidLocationSummary,
    AdminAidLocationVerificationDecision,
    AdminAidLocationVerificationsResponse,
    AidLocationComment,
    AidLocationCommentInput,
    AidLocationCommentsResponse,
    AidLocationReceipt,
    AidLocationReportInput,
    AidLocationReportReceipt,
    ChangeSignal,
    CommunityContributionReceipt,
    DisasterEventAutocompleteResponse,
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
    PersonAutocompleteResponse,
    PersonDuplicateCheckResponse,
    PersonStatusReportPublic,
    PersonStatusReportsPage,
    PublicPersonStatus,
    ServiceVersion,
    UnverifiedBuildingReportInput,
    UnverifiedBuildingReportReceipt,
    VerificationStatus,
    AdminVisitorPresence,
    AdminVisitorPresencePage,
    MyReportNovelty,
    MyReportsPage,
    MyReportSummary,
    VisitorPresenceInput,
    VisitorPresenceReceipt,
    VolunteerAlert,
    VolunteerAlertInput,
    VolunteerAlertPage,
    ActiveHelpRequest,
    AdminHelpRequest,
    AdminHelpRequestDeleteReceipt,
    AdminHelpRequestPage,
    AdminHelpRequestVolunteer,
    AdminHelpRequestVolunteerPage,
    HelpRequestAttendInput,
    HelpRequestAttender,
    HelpRequestAttendersPage,
    HelpRequestAttendReceipt,
    HelpRequestInput,
    HelpRequestVolunteerInput,
    HelpRequestPage,
    HelpRequestReceipt,
    ActiveFoodOffer,
    FoodOfferInput,
    FoodOfferPage,
    ShelterOfferInput,
    ShelterOfferReceipt,
    ActiveShelterOffer,
    ShelterOfferPage,
    FoodOfferReceipt,
)
from .photos import (
    MalwareScanner,
    PhotoProcessingError,
    SignatureMalwareScanner,
    sniff_image_type,
    strip_metadata,
)
from .models import SourceReference
from .models import (
    ConfirmSafeInput,
    ConfirmSafeReceipt,
    EmergencyContactDirectInput,
    EmergencyContactInput,
    EmergencyContactView,
    EmergencyContactsResponse,
    EmergencyInvitationMatchInput,
    EmergencyInvitationRespondInput,
    EmergencyInvitationView,
    EmergencyInvitationsResponse,
    EmergencyPanelView,
    MySeismicAlertView,
    MySeismicAlertsResponse,
    SeismicAffectedMarker,
    SeismicAffectedResponse,
    SeismicEventView,
    SeismicEventsResponse,
    SeismicSettingsUpdate,
    SeismicSettingsView,
    SeismicSimulationInput,
    SeismicSimulationReceipt,
    SeismicTestAccountsInput,
    SeismicTestAccountsReceipt,
    SeismicZoneView,
)
from .repository import (
    AidOfferIdempotencyConflictError,
    DeceasedOutcomeFinalError,
    DisasterRepository,
    HealthVerifiedCaseError,
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


def problem(
    status_code: int,
    title: str,
    detail: str,
    fields: list[str] | None = None,
) -> JSONResponse:
    """Respuesta `application/problem+json` sin datos sensibles."""
    content = {
        "type": "about:blank",
        "title": title,
        "status": status_code,
        "detail": detail,
    }
    # CHG-114: `detail` es para la persona; `fields` es para el cliente,
    # que con las claves resalta el campo y desplaza la pantalla hasta
    # él. Sin esta lista tendría que adivinarlas parseando el texto.
    if fields:
        content["fields"] = fields
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content=content,
    )


# CHG-120 — Respuesta única del bloqueo por verificación del sector
# salud: 409 (no el 404 uniforme — la persona sigue publicada; lo
# cerrado es el canal de novedades).
def _health_verified_case_problem() -> JSONResponse:
    return problem(
        409,
        "Caso verificado por el sector salud",
        "Personal del sector salud ya reportó el desenlace de esta "
        "persona; mientras esa verificación siga vigente no se "
        "reciben nuevas novedades sobre el caso.",
    )


# CHG-122 — La máquina de estados del caso: `deceased` es terminal y
# ninguna novedad de `found` lo sobrescribe, venga del rol que venga.
# Revertirlo es tarea de la consola (rechazar/archivar la novedad de
# fallecimiento), no de otra novedad.
def _deceased_final_problem() -> JSONResponse:
    return problem(
        409,
        "El desenlace fallecido es definitivo",
        "Esta persona fue reportada como fallecida y ese desenlace no "
        "puede sobrescribirse con una novedad de encontrada. Si el "
        "reporte de fallecimiento fuera erróneo, el equipo puede "
        "invalidarlo desde la consola de revisión.",
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


def generate_help_request_code(now: datetime) -> str:
    """Código público de solicitud de ayuda (CHG-125)."""
    return f"HR-{now.year}-{secrets.token_hex(4).upper()}"


def generate_food_offer_code(now: datetime) -> str:
    """Código público de oferta de comida (CHG-163)."""
    return f"FO-{now.year}-{secrets.token_hex(4).upper()}"


def generate_shelter_offer_code(now: datetime) -> str:
    """Código público de oferta de alojamiento temporal (CHG-205)."""
    return f"AL-{now.year}-{secrets.token_hex(4).upper()}"


def generate_damaged_home_code(now: datetime) -> str:
    """Código público de «Mi casita destruida» (CHG-182)."""
    return f"CASA-{now.year}-{secrets.token_hex(4).upper()}"


def generate_reception_route_code(now: datetime) -> str:
    """Código de la etapa 2, Mulera ↔ Centro Receptor (CHG-175 §24).

    Prefijo propio: los dos códigos de un mismo transporte no se
    parecen y no son intercambiables (§25-§26).
    """
    return f"RR-{now.year}-{secrets.token_hex(4).upper()}"


def generate_route_code(now: datetime) -> str:
    """Código de registro de ruta Local ↔ Mulera (CHG-174 §28).

    Lo genera SIEMPRE el backend, no es predecible y no se reutiliza:
    su unicidad la garantiza además un índice único.
    """
    return f"RT-{now.year}-{secrets.token_hex(4).upper()}"


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
        ("officialAuthorityName", "Autoridad de la denuncia",
         "official_authority_name", "private", None, "text"),
        ("tattooDescription", "Tatuajes", "tattoo_description",
         "private", None, "multiline"),
        ("scarsDescription", "Cicatrices", "scars_description",
         "private", None, "multiline"),
        ("prostheticsDescription", "Prótesis u órtesis",
         "prosthetics_description", "private", None, "multiline"),
        ("piercingsAndMoles", "Perforaciones y lunares",
         "piercings_and_moles", "private", None, "multiline"),
        ("mentalHealthCondition", "Condición cognitiva o de salud mental",
         "mental_health_condition_encrypted", "protected", "decrypt",
         "multiline"),
        ("vitalMedication", "Medicación de dependencia vital",
         "vital_medication_encrypted", "protected", "decrypt",
         "multiline"),
        ("severeAllergies", "Alergias graves",
         "severe_allergies_encrypted", "protected", "decrypt",
         "multiline"),
        ("belongingsDescription", "Pertenencias",
         "belongings_description", "private", None, "multiline"),
        ("transportMode", "Medio de transporte", "transport_mode",
         "private", None, "text"),
        ("vehicleDetails", "Datos del vehículo",
         "vehicle_details_encrypted", "protected", "decrypt", "text"),
        ("companionsDescription", "Acompañantes",
         "companions_description", "private", None, "multiline"),
        ("reporterPhonePublic", "Autoriza compartir su teléfono",
         "reporter_phone_public", "private", None, "text"),
        ("reporterEmailPublic", "Autoriza compartir su correo",
         "reporter_email_public", "private", None, "text"),
        ("reporterLatitude", "Latitud del reportante",
         "reporter_snapshot_latitude_encrypted", "protected",
         "decrypt", "number"),
        ("reporterLongitude", "Longitud del reportante",
         "reporter_snapshot_longitude_encrypted", "protected",
         "decrypt", "number"),
    ],
    # CHG-162 (F2) — «Mi casita partida»: informe público (nace en el
    # mapa), sin campos cifrados. Va antes que los demás tipos para no
    # pisar las etiquetas compartidas de FIELD_LABELS.
    "damaged_home_report": [
        ("description", "Descripción del daño", "description",
         "public", None, "multiline"),
        ("department", "Departamento", "department", "public", None,
         "text"),
        ("municipality", "Municipio", "municipality", "public", None,
         "text"),
        ("address", "Dirección", "address", "public", None, "text"),
        ("latitude", "Latitud", "latitude", "public", None, "number"),
        ("longitude", "Longitud", "longitude", "public", None,
         "number"),
        ("visible", "Visible en el mapa", "visible", "public", None,
         "text"),
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
        ("pendingReasonDetail", "Detalle de otro motivo",
         "pending_reason_detail_protected", "protected", "decrypt",
         "multiline"),
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


# CHG-114 — Nombres legibles de los campos para los mensajes de
# rechazo. Antes el `detail` del 422 concatenaba las claves del modelo
# ("Revisa los campos: reporterPhone.") y quien reporta no tiene por
# qué saber qué es eso. Las etiquetas ya existían para la consola
# administrativa (CHG-036), así que se reutiliza esa tabla en vez de
# mantener un segundo diccionario que se desincronice.
FIELD_LABELS: dict[str, str] = {
    clave: etiqueta
    for especificaciones in ADMIN_FIELD_SPECS.values()
    for clave, etiqueta, *_ in especificaciones
}

# Campos que el formulario envía y la consola no muestra, así que no
# tenían etiqueta.
FIELD_LABELS.update(
    {
        "genderIdentity": "Sexo",
        "nationality": "Nacionalidad",
        "heightCm": "Estatura",
        "build": "Contextura",
        "skinTone": "Tono de piel",
        "hairDescription": "Cabello",
        "eyeDescription": "Ojos",
        "isReporterPhonePublic": "Autoriza compartir su teléfono",
        "isReporterEmailPublic": "Autoriza compartir su correo",
        "photoCategories": "Categorías de las fotografías",
        "truthConfirmed": "Confirmación de veracidad",
        "photoAuthorizationConfirmed": "Autorización de las fotografías",
        "reviewAcknowledged": "Confirmación de revisión",
        "relatedDisasterId": "Evento relacionado",
        "payload": "Formulario",
    }
)


def field_label(clave: str) -> str:
    """Etiqueta legible; si no hay, la clave tal cual (nunca vacía)."""
    return FIELD_LABELS.get(clave, clave)


def invalid_fields(error: ValidationError) -> list[str]:
    """Claves rechazadas, sin repetir y sin devolver valor alguno."""
    return sorted(
        {
            str(item["loc"][0]) if item["loc"] else "payload"
            for item in error.errors()
        }
    )


def invalid_fields_detail(claves: list[str]) -> str:
    return (
        "Revisa los campos: "
        + ", ".join(field_label(clave) for clave in claves)
        + "."
    )

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
    # CHG-208: adaptador del catálogo del SGC inyectable en pruebas.
    sgc_provider: seismic_rules.SgcEventProvider | None = None,
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

    # CHG-208: el poller del SGC es una tarea de fondo SUPERVISADA.
    # Sus fallos no tocan los endpoints (aislamiento, spec §4/§81) y
    # solo arranca cuando SGC_POLL_ENABLED lo pide.
    async def start_sgc_poller(application: FastAPI):
        if not resolved_settings.sgc_poll_enabled:
            return None, None
        provider = sgc_provider or seismic_ingest.HttpSgcEventProvider(
            resolved_settings.sgc_catalog_url
        )
        stop_event = _asyncio.Event()
        task = _asyncio.create_task(
            seismic_ingest.supervised_poll_loop(
                application.state.repository,
                provider,
                report_notifier,
                resolved_settings.sgc_poll_interval_seconds,
                stop_event,
            )
        )
        return task, stop_event

    async def stop_sgc_poller(task, stop_event):
        if task is None:
            return
        stop_event.set()
        try:
            await _asyncio.wait_for(task, timeout=5)
        except (TimeoutError, _asyncio.CancelledError):
            task.cancel()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if repository is not None:
            application.state.repository = repository
            poller_task, poller_stop = await start_sgc_poller(
                application
            )
            try:
                yield
            finally:
                await stop_sgc_poller(poller_task, poller_stop)
            return

        pool = await asyncpg.create_pool(
            resolved_settings.database_url,
            min_size=resolved_settings.database_pool_min_size,
            max_size=resolved_settings.database_pool_max_size,
            command_timeout=5,
        )
        application.state.repository = PostgresDisasterRepository(pool)
        poller_task, poller_stop = await start_sgc_poller(application)
        try:
            yield
        finally:
            await stop_sgc_poller(poller_task, poller_stop)
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

    # CHG-111: el gateway lee esto para publicar la revisión que corre
    # detrás de él. Vive en el espacio de salud interno, no en el
    # contrato público.
    @application.get(
        "/health/version",
        response_model=ServiceVersion,
        tags=["Platform"],
    )
    async def version() -> ServiceVersion:
        return ServiceVersion(
            service="disaster-service",
            revision=resolved_settings.git_revision,
        )

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

    # CHG-092 — Autocompletado creable de "Evento relacionado".
    @application.get(
        "/internal/v1/disaster-events/autocomplete",
        response_model=DisasterEventAutocompleteResponse,
        response_model_by_alias=True,
        tags=["Disasters"],
    )
    async def autocomplete_disaster_events(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
        q: Annotated[str, Query(min_length=2, max_length=160)],
        limit: Annotated[int, Query(ge=1, le=10)] = 5,
    ):
        if len(q.strip()) < 2:
            return problem(
                422,
                "Consulta inválida",
                "La consulta requiere al menos dos caracteres.",
            )
        items = await data.autocomplete_disaster_events(q, limit)
        return DisasterEventAutocompleteResponse(
            items=items,
            query=q,
            generated_at=datetime.now(UTC),
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
        unmapped_counts = HumanMapStatusCounts(
            missing=unmapped.get("missing", 0),
            reported_deceased=unmapped.get("reported_deceased", 0),
            confirmed_alive=unmapped.get("confirmed_alive", 0),
            confirmed_deceased=unmapped.get("confirmed_deceased", 0),
        )
        unmapped_total = sum(unmapped.values())
        return HumanMapOverview(
            features=page,
            total_matched=total_mapped + unmapped_total,
            total_mapped=total_mapped,
            unmapped_count=unmapped_total,
            unmapped_status_counts=unmapped_counts,
            returned_features=len(page),
            next_cursor=next_cursor,
            generated_at=datetime.now(UTC),
            data_classification=classification,
        )

    # CHG-082 — Huella de cambios para el refresco en vivo de la
    # portada: barata de consultar, sin datos sensibles.
    @application.get(
        "/internal/v1/platform/change-signal",
        response_model=ChangeSignal,
        response_model_by_alias=True,
        tags=["Platform"],
    )
    async def platform_change_signal(
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        try:
            signal = await data.platform_change_signal()
        except asyncpg.PostgresError:
            return problem(
                503,
                "Señal no disponible",
                "No fue posible calcular la señal de cambios.",
            )
        return ChangeSignal(
            signal=signal, generated_at=datetime.now(UTC)
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
            "volunteers_needed": 0,
            # CHG-153: logística (contadores del backend).
            "receiver_center": 0,
            "distribution_point": 0,
            # CHG-162: hogares en malas condiciones.
            "damaged_home": 0,
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
                volunteers_needed=by_category["volunteers_needed"],
                receiver_center=by_category["receiver_center"],
                distribution_point=by_category["distribution_point"],
                damaged_home=by_category["damaged_home"],
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

    # CHG-105 — Fotografía pública del caso. La ruta usa el id del
    # caso, que ya es público; la clave del objeto nunca sale al
    # cliente porque contiene el id del expediente privado.
    @application.get(
        "/internal/v1/public/missing-persons/{case_id}/photo",
        tags=["MissingPersons"],
    )
    async def serve_public_person_photo(
        case_id: UUID,
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
    ):
        photo = await data.get_public_person_photo(case_id)
        if photo is None:
            return problem(
                404,
                "Fotografía no disponible",
                "El caso no tiene una fotografía pública.",
            )
        try:
            content = object_storage.load(photo["object_key"])
        except StorageUnavailableError:
            content = None
        if content is None:
            return problem(
                503,
                "Fotografía no disponible",
                "No fue posible leer la fotografía en este momento.",
            )

        from fastapi.responses import Response as RawResponse

        return RawResponse(
            content=content,
            media_type=photo["content_type"],
            # Pública y cacheable, pero por poco tiempo: si el equipo
            # la retira, la copia vieja no debe sobrevivir mucho.
            headers={"Cache-Control": "public, max-age=300"},
        )

    # CHG-091 — Sugerencias en tiempo real para prevenir duplicados.
    @application.get(
        "/internal/v1/persons/autocomplete",
        response_model=PersonAutocompleteResponse,
        response_model_by_alias=True,
        tags=["MissingPersons"],
    )
    async def autocomplete_persons(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
        q: Annotated[str, Query(min_length=2, max_length=100)],
        limit: Annotated[int, Query(ge=1, le=10)] = 5,
    ):
        if len(q.strip()) < 2:
            return problem(
                422,
                "Consulta inválida",
                "La consulta requiere al menos dos caracteres.",
            )
        items = await data.autocomplete_persons(q, limit)
        return PersonAutocompleteResponse(
            items=items,
            query=q,
            generated_at=datetime.now(UTC),
        )

    @application.get(
        "/internal/v1/persons/check-duplicates",
        response_model=PersonDuplicateCheckResponse,
        response_model_by_alias=True,
        tags=["MissingPersons"],
    )
    async def check_person_duplicates(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
        first_name: Annotated[
            str, Query(alias="firstName", min_length=1, max_length=120)
        ],
        last_name: Annotated[
            str, Query(alias="lastName", max_length=120)
        ] = "",
        limit: Annotated[int, Query(ge=1, le=10)] = 5,
    ):
        full_name = f"{first_name.strip()} {last_name.strip()}".strip()
        if len(full_name) < 3:
            return problem(
                422,
                "Consulta inválida",
                "El nombre requiere al menos tres caracteres.",
            )
        items = await data.check_person_duplicates(full_name, limit)
        return PersonDuplicateCheckResponse(
            items=items,
            first_name=first_name,
            last_name=last_name,
            generated_at=datetime.now(UTC),
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
            claves = invalid_fields(error)
            return problem(
                422,
                "Datos inválidos",
                invalid_fields_detail(claves),
                fields=claves,
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
                "El reporte requiere entre una y tres fotografías.",
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

        # CHG-094: si se declaran categorías deben ser una por foto;
        # una lista desalineada asignaría la etiqueta equivocada.
        if payload.photo_categories and len(
            payload.photo_categories
        ) != len(prepared):
            return problem(
                422,
                "Datos inválidos",
                "photoCategories debe declarar una categoría por "
                "fotografía enviada.",
            )

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
                        # CHG-094: categoría alineada por posición con
                        # las partes `photos`. `index` es 1-based
                        # (enumerate start=1), el arreglo es 0-based.
                        category=(
                            payload.photo_categories[index - 1]
                            if 0 < index <= len(payload.photo_categories)
                            else None
                        ),
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
            # CHG-094: identificación física en claro; salud y placa
            # cifradas.
            tattoo_description=payload.tattoo_description,
            scars_description=payload.scars_description,
            prosthetics_description=payload.prosthetics_description,
            piercings_and_moles=payload.piercings_and_moles,
            mental_health_condition_encrypted=encrypt(
                payload.mental_health_condition
            ),
            vital_medication_encrypted=encrypt(payload.vital_medication),
            severe_allergies_encrypted=encrypt(payload.severe_allergies),
            belongings_description=payload.belongings_description,
            transport_mode=payload.transport_mode,
            vehicle_details_encrypted=encrypt(payload.vehicle_details),
            companions_description=payload.companions_description,
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
            official_authority_name=payload.official_authority_name,
            reporter_phone_public=payload.is_reporter_phone_public,
            reporter_email_public=payload.is_reporter_email_public,
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
        claves = invalid_fields(error)
        return problem(
            422,
            "Datos inválidos",
            invalid_fields_detail(claves),
            fields=claves,
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
        categories: list[str] | None = None,
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
                    # CHG-094: la categoría llega alineada por posición
                    # con las partes `photos`. `index` es 1-based.
                    category=(
                        categories[index - 1]
                        if categories is not None
                        and 0 < index <= len(categories)
                        else None
                    ),
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
        # CHG-122: además del sí/no publicable, el estado público
        # alimenta la máquina de estados (deceased es terminal).
        try:
            public_status = await data.person_public_status(person_id)
        except asyncpg.PostgresError:
            return problem(
                503,
                "Servicio de evidencia no disponible",
                "No fue posible validar la persona; la novedad no fue "
                "registrada.",
            )
        if public_status is None:
            return problem(
                404,
                "Persona no disponible",
                "La persona no existe o no es publicable.",
            )

        # CHG-120: el rol lo resuelve identity y lo declara el gateway;
        # el cliente final jamás controla esta bandera (CHG-077).
        actor_is_health_sector = (
            actor_kind == "authenticated"
            and request.headers.get("x-actor-health") == "true"
        )
        # CHG-120: con una novedad efectiva del sector salud el caso
        # queda verificado y no recibe más novedades de otros actores.
        # Pre-chequeo barato antes de procesar fotografías; la
        # comprobación autoritativa corre dentro de la transacción de
        # inserción.
        if not actor_is_health_sector:
            try:
                health_verified = (
                    await data.person_has_effective_health_report(
                        person_id
                    )
                )
            except asyncpg.PostgresError:
                return problem(
                    503,
                    "Servicio de evidencia no disponible",
                    "No fue posible validar la persona; la novedad no "
                    "fue registrada.",
                )
            if health_verified:
                return _health_verified_case_problem()

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

        # CHG-122: `deceased` es terminal para todos los roles, sector
        # salud incluido — "encontrada" no lo sobrescribe. Reportar
        # `deceased` sobre un caso ya fallecido confirma, no
        # contradice, y sigue permitido. Pre-chequeo barato; la
        # comprobación autoritativa corre en la transacción.
        if (
            payload.claimed_outcome == "found"
            and public_status == "deceased"
        ):
            return _deceased_final_problem()

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
            "La novedad requiere entre una y tres fotografías.",
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
            reporter_health_sector=actor_is_health_sector,
        )
        try:
            receipt, created = await data.create_person_status_report(
                stored_report, stored_photos
            )
        except HealthVerifiedCaseError:
            # CHG-120: otro envío del sector salud verificó el caso
            # entre el pre-chequeo y la inserción.
            cleanup()
            return _health_verified_case_problem()
        except DeceasedOutcomeFinalError:
            # CHG-122: el fallecimiento se registró entre el
            # pre-chequeo y la inserción.
            cleanup()
            return _deceased_final_problem()
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

    # CHG-077 — Novedades visibles al abrir la tarjeta de la persona:
    # qué dicen quienes la vieron, sin identidad del reportante ni
    # fotografías. Solo personas publicadas; 404 uniforme.
    @application.get(
        "/internal/v1/missing-persons/{person_id}/status-reports",
        response_model=PersonStatusReportsPage,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def list_person_status_reports(
        person_id: UUID,
        data: Annotated[DisasterRepository, Depends(get_repository)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        try:
            result = await data.list_person_status_reports(
                person_id, limit
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Consulta no disponible",
                "No fue posible consultar las novedades en este momento.",
            )
        if result is None:
            return problem(
                404,
                "Persona no disponible",
                "La persona no existe o no está publicada.",
            )
        public_status, rows = result
        items = [
            PersonStatusReportPublic(
                id=row["id"],
                claimed_outcome=row["claimed_outcome"],
                evidence_description=decrypt_text(
                    row["evidence_description_encrypted"]
                )
                or "",
                location_description=decrypt_text(
                    row["location_description_encrypted"]
                ),
                occurred_at=row["occurred_at"],
                received_at=row["received_at"],
                reporter_kind=(
                    "health_sector"
                    if row["reporter_health_sector"]
                    else row["actor_kind"]
                ),
                moderation_status=row["moderation_status"],
            )
            for row in rows
        ]
        return PersonStatusReportsPage(
            person_id=person_id,
            public_status=public_status,
            items=items,
            total=len(items),
        )

    # CHG-153 — Candidatos a centro asociado para el formulario de alta
    # de un punto dependiente (recolección/distribución).
    @application.get(
        "/internal/v1/aid-locations/parent-candidates",
        response_model=AidLocationParentCandidatesResponse,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def list_aid_location_parent_candidates(
        data: Annotated[DisasterRepository, Depends(get_repository)],
        kind: Annotated[str, Query(min_length=1, max_length=40)],
        municipality: Annotated[str, Query(min_length=1, max_length=100)],
    ):
        try:
            rows = await data.list_aid_location_parent_candidates(
                kind=kind, municipality=municipality
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Consulta no disponible",
                "No fue posible listar los centros asociados.",
            )
        if rows is None:
            return problem(
                422,
                "Tipo sin dependencia",
                "Este tipo de punto no exige un centro asociado.",
            )
        return AidLocationParentCandidatesResponse(
            items=[
                AidLocationParentCandidate(**row) for row in rows
            ],
            total=len(rows),
        )

    # CHG-161 — Alta de un transporte humanitario (mula o lancha).
    # Siempre autenticado: el gateway resuelve la sesión y este
    # servicio exige la cuenta en los encabezados de actor.
    @application.post(
        "/internal/v1/humanitarian-transports",
        status_code=201,
        response_model=HumanitarianTransportReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def create_humanitarian_transport(
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
        if actor_kind != "authenticated" or account_id is None:
            return problem(
                401,
                "Sesión requerida",
                "El registro de transportes exige una cuenta.",
            )
        try:
            payload = HumanitarianTransportInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        try:
            result = await data.create_humanitarian_transport(
                idempotency_key=idempotency_key,
                kind=payload.kind,
                origin_municipality=payload.origin_municipality,
                destination_municipality=(
                    payload.destination_municipality
                ),
                origin_location_id=payload.origin_location_id,
                destination_location_id=(
                    payload.destination_location_id
                ),
                supplies_summary=(
                    payload.supplies_summary.strip()
                    if payload.supplies_summary
                    and payload.supplies_summary.strip()
                    else None
                ),
                account_id=account_id,
                # CHG-171: conductor (documento y teléfono cifrados,
                # §30/§59) y vehículo.
                driver_full_name=payload.driver_full_name,
                driver_document_type=payload.driver_document_type,
                driver_document_number_encrypted=encrypt(
                    payload.driver_document_number
                ),
                driver_phone_encrypted=encrypt(payload.driver_phone),
                tractor_plate=payload.tractor_plate,
                trailer_plate=payload.trailer_plate,
                # CHG-173: la lanchera trae su propia identidad y la
                # mulera sus placas; el modelo ya garantizó que no se
                # mezclan.
                vessel_registration=payload.vessel_registration,
                vessel_name=payload.vessel_name,
                vessel_type=payload.vessel_type,
                vehicle_visible_characteristics=(
                    payload.vehicle_visible_characteristics.strip()
                ),
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar el transporte.",
            )
        if isinstance(result, str):
            messages = {
                "origin_not_found": "El centro de origen no existe.",
                "origin_wrong_kind": (
                    "El origen debe ser un centro de acopio local."
                ),
                "origin_wrong_city": (
                    "El centro de origen no está en la ciudad de "
                    "origen indicada."
                ),
                "origin_unavailable": (
                    "El centro de origen no está disponible."
                ),
                "destination_not_found": (
                    "El centro de destino no existe."
                ),
                "destination_wrong_kind": (
                    "El destino debe ser un centro de acopio receptor."
                ),
                "destination_wrong_city": (
                    "El centro de destino no está en la ciudad de "
                    "destino indicada."
                ),
                "destination_unavailable": (
                    "El centro de destino no está disponible."
                ),
            }
            return problem(
                422, "Transporte inválido", messages[result]
            )
        return HumanitarianTransportReceipt(**result)

    # CHG-171 §50 — Catálogo de ciudades para los selectores de La
    # Mulera (público; el frontend lo carga una vez y filtra local).
    @application.get(
        "/internal/v1/transport-cities",
        response_model=TransportCitiesResponse,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def list_transport_cities(
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        rows = await data.list_transport_cities()
        return TransportCitiesResponse(
            items=[TransportCity(**row) for row in rows],
            total=len(rows),
        )

    # CHG-171 (GPS) — Hitos y posiciones del viaje: solo el dueño
    # autenticado (que es el conductor, regla del formulario).
    def _journey_actor(request: Request):
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_kind, account_id = actor
        if actor_kind != "authenticated" or account_id is None:
            return problem(
                401,
                "Sesión requerida",
                "El seguimiento del viaje exige la cuenta del "
                "conductor.",
            )
        return account_id

    def _journey_problem(result) -> JSONResponse | None:
        if result is None:
            return problem(
                404,
                "Transporte no encontrado",
                "El transporte no existe.",
            )
        if result == "not_owner":
            return problem(
                403,
                "Cuenta sin permiso",
                "Solo la cuenta que registró el transporte (el "
                "conductor) puede reportar su viaje.",
            )
        if result == "wrong_status":
            return problem(
                409,
                "Estado del viaje no compatible",
                "El viaje no admite esta acción en su estado actual.",
            )
        return None

    @application.post(
        "/internal/v1/me/humanitarian-transports/{transport_id}/start",
        response_model=TransportJourneyReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def start_transport_journey(
        transport_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = _journey_actor(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        result = await data.start_transport_journey(
            transport_id=transport_id, account_id=account_id
        )
        failure = _journey_problem(result)
        if failure is not None:
            return failure
        return TransportJourneyReceipt(**result)

    @application.post(
        "/internal/v1/me/humanitarian-transports/{transport_id}/arrive",
        response_model=TransportJourneyReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def arrive_transport_journey(
        transport_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = _journey_actor(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        result = await data.arrive_transport_journey(
            transport_id=transport_id, account_id=account_id
        )
        failure = _journey_problem(result)
        if failure is not None:
            return failure
        return TransportJourneyReceipt(**result)

    @application.post(
        "/internal/v1/me/humanitarian-transports/{transport_id}"
        "/positions",
        response_model=TransportJourneyReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def record_transport_position(
        transport_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = _journey_actor(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        try:
            payload = TransportPositionInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        result = await data.record_transport_position(
            transport_id=transport_id,
            account_id=account_id,
            latitude=payload.latitude,
            longitude=payload.longitude,
        )
        failure = _journey_problem(result)
        if failure is not None:
            return failure
        return TransportJourneyReceipt(**result)

    # ------------------------------------------------------------------
    # CHG-174 — Aceptación inicial de ruta Centro Local ↔ Mulera.
    # ------------------------------------------------------------------

    def _steward_actor(request: Request):
        """Cuenta que actúa por un centro, y si además es super_admin.

        §58: la autorización real es por centro y la resuelve el
        repositorio; aquí solo se identifica al actor.
        """
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_kind, account_id = actor
        if actor_kind != "authenticated" or account_id is None:
            return problem(
                401,
                "Sesión requerida",
                "La gestión de solicitudes exige una cuenta.",
            )
        is_super_admin = (
            request.headers.get("x-actor-role", "").strip() == "super_admin"
        )
        return account_id, is_super_admin

    def _route_problem(result) -> JSONResponse | None:
        messages = {
            "not_found": (
                404,
                "Solicitud no encontrada",
                "La solicitud o el transporte no existe.",
            ),
            "forbidden": (
                403,
                "Centro sin permiso",
                "Solo el responsable de ese centro puede decidir esta "
                "solicitud.",
            ),
            "already_decided": (
                409,
                "Solicitud ya procesada",
                "Esta solicitud ya fue aceptada o declinada.",
            ),
            "not_ready": (
                409,
                "Aceptaciones pendientes",
                "La ruta exige que los dos centros hayan aceptado la "
                "solicitud.",
            ),
            "not_owner": (
                403,
                "Cuenta sin permiso",
                "Solo la cuenta que registró el transporte puede "
                "aceptar su ruta.",
            ),
            "local_stage_pending": (
                409,
                "Etapa previa pendiente",
                "La aceptación entre el Centro de Acopio Local y la "
                "Mulera debe completarse antes de esta.",
            ),
            "not_issued": (
                409,
                "Ruta sin código",
                "El Centro de Acopio Local todavía no inició la "
                "aceptación de ruta.",
            ),
            # §39: nunca se revela a qué transporte pertenece un código.
            "invalid_code": (
                422,
                "Código inválido",
                "El código ingresado no es válido para esta ruta.",
            ),
            "code_used": (
                409,
                "Código ya utilizado",
                "Ese código ya fue utilizado y no puede reutilizarse.",
            ),
        }
        if isinstance(result, str) and result in messages:
            status_code, title, detail = messages[result]
            return problem(status_code, title, detail)
        return None

    def _protected_text(value) -> str | None:
        if value is None:
            return None
        try:
            return fernet.decrypt(bytes(value)).decode()
        except Exception:
            return "[contenido protegido no legible]"

    @application.get(
        "/internal/v1/me/center-transport-requests",
        response_model=TransportCenterRequestsResponse,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def list_center_transport_requests(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = _steward_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        account_id, is_super_admin = actor
        rows = await data.list_center_transport_requests(
            account_id=account_id, is_super_admin=is_super_admin
        )
        items = []
        for row in rows:
            record = dict(row)
            # §12: la vista autorizada muestra al conductor; los
            # sensibles se descifran aquí y solo aquí.
            record["driver_document_number"] = _protected_text(
                record.pop("driver_document_number_encrypted", None)
            )
            record["driver_phone"] = _protected_text(
                record.pop("driver_phone_encrypted", None)
            )
            items.append(TransportCenterRequest(**record))
        return TransportCenterRequestsResponse(
            items=items, total=len(items)
        )

    @application.post(
        "/internal/v1/me/center-transport-requests/{request_id}/decision",
        response_model=TransportRequestDecisionReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def decide_center_transport_request(
        request_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = _steward_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        account_id, is_super_admin = actor
        try:
            payload = TransportRequestDecisionInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        result = await data.decide_center_transport_request(
            request_id=request_id,
            account_id=account_id,
            is_super_admin=is_super_admin,
            accept=payload.decision == "accept",
        )
        failure = _route_problem(result)
        if failure is not None:
            return failure
        return TransportRequestDecisionReceipt(**result)

    @application.get(
        "/internal/v1/me/center-route-acceptances",
        response_model=TransportRouteStatesResponse,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def list_center_route_acceptances(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = _steward_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        account_id, is_super_admin = actor
        rows = await data.list_center_route_acceptances(
            account_id=account_id, is_super_admin=is_super_admin
        )
        items = [TransportRouteState(**row) for row in rows]
        return TransportRouteStatesResponse(items=items, total=len(items))

    @application.post(
        "/internal/v1/me/humanitarian-transports/{transport_id}"
        "/route-acceptance",
        response_model=TransportRouteCodeReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def start_local_route_acceptance(
        transport_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = _steward_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        account_id, is_super_admin = actor
        result = await data.start_local_route_acceptance(
            transport_id=transport_id,
            account_id=account_id,
            is_super_admin=is_super_admin,
            generate_code=lambda: generate_route_code(datetime.now(UTC)),
        )
        failure = _route_problem(result)
        if failure is not None:
            return failure
        return TransportRouteCodeReceipt(**result)

    # CHG-175 — Etapa 2: Mulera ↔ Centro de Acopio Receptor.
    @application.post(
        "/internal/v1/me/humanitarian-transports/{transport_id}"
        "/reception-route-acceptance",
        response_model=TransportRouteCodeReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def start_reception_route_acceptance(
        transport_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = _steward_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        account_id, is_super_admin = actor
        result = await data.start_reception_route_acceptance(
            transport_id=transport_id,
            account_id=account_id,
            is_super_admin=is_super_admin,
            generate_code=lambda: generate_reception_route_code(
                datetime.now(UTC)
            ),
        )
        failure = _route_problem(result)
        if failure is not None:
            return failure
        return TransportRouteCodeReceipt(**result)

    @application.post(
        "/internal/v1/me/humanitarian-transports/{transport_id}"
        "/reception-route-code/validate",
        response_model=RouteCodeValidationReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def validate_reception_route_code(
        transport_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = _journey_actor(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        try:
            payload = RouteCodeInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        result = await data.validate_reception_route_code(
            transport_id=transport_id,
            account_id=account_id,
            code=payload.code,
        )
        failure = _route_problem(result)
        if failure is not None:
            return failure
        return RouteCodeValidationReceipt(**result)

    @application.post(
        "/internal/v1/me/humanitarian-transports/{transport_id}"
        "/reception-route-accept",
        response_model=RouteAcceptanceReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def accept_reception_route_by_mule(
        transport_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = _journey_actor(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        try:
            payload = RouteCodeInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        result = await data.accept_reception_route_by_mule(
            transport_id=transport_id,
            account_id=account_id,
            code=payload.code,
        )
        failure = _route_problem(result)
        if failure is not None:
            return failure
        return RouteAcceptanceReceipt(**result)

    @application.get(
        "/internal/v1/me/humanitarian-transports",
        response_model=MyTransportsResponse,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def list_my_transports(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = _journey_actor(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        rows = await data.list_my_transports(account_id=account_id)
        items = [MyTransport(**row) for row in rows]
        return MyTransportsResponse(items=items, total=len(items))

    @application.post(
        "/internal/v1/me/humanitarian-transports/{transport_id}"
        "/route-code/validate",
        response_model=RouteCodeValidationReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def validate_route_code(
        transport_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = _journey_actor(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        try:
            payload = RouteCodeInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        result = await data.validate_route_code(
            transport_id=transport_id,
            account_id=account_id,
            code=payload.code,
        )
        failure = _route_problem(result)
        if failure is not None:
            return failure
        return RouteCodeValidationReceipt(**result)

    @application.post(
        "/internal/v1/me/humanitarian-transports/{transport_id}"
        "/route-accept",
        response_model=RouteAcceptanceReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def accept_route_by_mule(
        transport_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = _journey_actor(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        try:
            payload = RouteCodeInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        result = await data.accept_route_by_mule(
            transport_id=transport_id,
            account_id=account_id,
            code=payload.code,
        )
        failure = _route_problem(result)
        if failure is not None:
            return failure
        return RouteAcceptanceReceipt(**result)

    # CHG-171 — Feed público del mapa: viajes vivos y llegadas
    # recientes con su rastro; nunca datos del conductor (§30).
    @application.get(
        "/internal/v1/humanitarian-transports/active",
        response_model=ActiveTransportsResponse,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def list_active_transports(
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        rows = await data.list_active_transports()
        return ActiveTransportsResponse(
            items=[ActiveTransport(**row) for row in rows],
            total=len(rows),
        )

    # CHG-162 — Alta de «Mi casita partida» (anónimo permitido).
    @application.post(
        "/internal/v1/damaged-home-reports",
        status_code=201,
        response_model=DamagedHomeReportReceipt,
        response_model_by_alias=True,
        tags=["BuildingReports"],
    )
    async def create_damaged_home_report(
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
        # CHG-182 — «Mi casita destruida» solo la publica quien tiene
        # cuenta: aquí se declara un medio para recibir dinero y hay que
        # poder responder por él y avisarle de los comentarios.
        if actor_kind != "authenticated" or account_id is None:
            return problem(
                401,
                "Sesión requerida",
                "Publicar tu casita exige una cuenta.",
            )
        oversize = check_declared_length(request)
        if oversize is not None:
            return oversize

        # CHG-162 (F2) — Las fotos del daño llegan en multipart, con la
        # parte JSON `payload` al lado. El envío JSON puro se conserva:
        # un bundle viejo sigue reportando sin fotografías (CHG-137).
        prepared: list[tuple[int, bytes, str]] = []
        if request.headers.get("content-type", "").startswith(
            "multipart/"
        ):
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
            photos_or_problem = await prepare_photo_parts(
                form,
                0,
                resolved_settings.max_photos,
                "El informe admite hasta tres fotografías del daño.",
            )
            if isinstance(photos_or_problem, JSONResponse):
                return photos_or_problem
            prepared = photos_or_problem
        else:
            raw_payload = await request.body()

        try:
            payload = DamagedHomeReportInput.model_validate_json(
                raw_payload
            )
        except ValidationError as error:
            return invalid_fields_problem(error)

        report_id = uuid4()
        saved_keys: list[str] = []

        def cleanup() -> None:
            for key in saved_keys:
                object_storage.delete(key)

        try:
            stored_photos = store_photos(
                f"damaged-home-reports/{report_id}",
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
                "No fue posible resguardar las fotografías; el informe "
                "no fue registrado.",
            )

        try:
            result, created = await data.create_damaged_home_report(
                report_id=report_id,
                idempotency_key=idempotency_key,
                description=payload.description.strip(),
                department=payload.department.strip(),
                municipality=payload.municipality.strip(),
                address=payload.address.strip(),
                latitude=payload.latitude,
                longitude=payload.longitude,
                account_id=account_id,
                photos=stored_photos,
                # CHG-182
                public_code=generate_damaged_home_code(datetime.now(UTC)),
                household_size=payload.household_size,
                donation_channel=payload.donation_channel,
                donation_reference=(
                    payload.donation_reference.strip()
                    if payload.donation_reference
                    else None
                ),
                # CHG-201: el modelo ya lo validó contra la lista de
                # anfitriones de TikTok; aquí solo se guarda.
                video_url=payload.video_url,
            )
        except asyncpg.PostgresError:
            cleanup()
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar el informe del hogar.",
            )
        if not created:
            # Reintento idempotente: los binarios de este intento sobran.
            cleanup()
        return DamagedHomeReportReceipt(**result)

    # CHG-153 — Alta de un punto logístico (JSON). La dependencia y la
    # ciudad se validan en el servicio (no se confía en el cliente).
    @application.post(
        "/internal/v1/aid-locations",
        status_code=201,
        response_model=AidLocationReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def create_aid_location(
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

        body = await request.body()
        try:
            payload = AidLocationInput.model_validate_json(body)
        except ValidationError as error:
            return invalid_fields_problem(error)

        # CHG-161 (F2) — Refuerzo server-side del portón de sesión: el
        # acopio local y el punto de distribución exigen una persona
        # responsable con cuenta. El formulario ya lo impide, pero la
        # regla vive aquí, como la dependencia y la ciudad.
        if payload.kind in AID_LOCATION_KINDS_REQUIRING_ACCOUNT and (
            actor_kind != "authenticated" or account_id is None
        ):
            return problem(
                401,
                "Sesión requerida",
                "Este tipo de punto logístico exige una cuenta.",
            )

        try:
            result = await data.create_aid_location(
                idempotency_key=idempotency_key,
                kind=payload.kind,
                name=payload.name.strip(),
                location_label=payload.address.strip(),
                municipality=payload.municipality.strip(),
                department=payload.department.strip(),
                latitude=payload.latitude,
                longitude=payload.longitude,
                description=(
                    payload.description.strip()
                    if payload.description
                    else None
                ),
                schedule=(
                    payload.schedule.strip() if payload.schedule else None
                ),
                contact=(
                    payload.contact.strip() if payload.contact else None
                ),
                parent_id=payload.parent_id,
                accepted_supplies=list(payload.accepted_supplies),
                operational_status=payload.operational_status,
                created_by_account_id=account_id,
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar el punto; ningún dato quedó "
                "publicado.",
            )

        if isinstance(result, str):
            messages = {
                "parent_not_found": (
                    "El centro asociado no existe."
                ),
                "parent_wrong_kind": (
                    "El centro asociado no es del tipo requerido para "
                    "este punto."
                ),
                "parent_other_city": (
                    "El centro asociado pertenece a otra ciudad; elige "
                    "uno de la misma ciudad."
                ),
            }
            # El campo culpable viaja en `fields` (CHG-114) para que el
            # formulario resalte el selector del centro asociado.
            return problem(
                422,
                "Centro asociado inválido",
                messages.get(result, "Centro asociado inválido."),
                fields=["parentId"],
            )

        row, _created = result
        return AidLocationReceipt(
            id=row["id"],
            kind=row["kind"],
            operational_status=row["operational_status"],
            created_at=row["created_at"],
        )

    # CHG-153 — Denuncia sobre un lugar de ayuda (JSON). Dual anónimo/
    # autenticado; el gateway resuelve la clave de denunciante
    # (`x-denouncer-key`) para el dedup y el umbral.
    @application.post(
        "/internal/v1/aid-locations/{location_id}/reports",
        status_code=202,
        response_model=AidLocationReportReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def create_aid_location_report(
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
        denouncer_key = request.headers.get("x-denouncer-key", "").strip()
        if not denouncer_key:
            return problem(
                422,
                "Denunciante no resuelto",
                "Falta la clave de denunciante resuelta por el gateway.",
            )

        body = await request.body()
        try:
            payload = AidLocationReportInput.model_validate_json(body)
        except ValidationError as error:
            return invalid_fields_problem(error)

        try:
            result = await data.create_aid_location_report(
                idempotency_key=idempotency_key,
                location_id=location_id,
                actor_kind=actor_kind,
                account_id=account_id,
                denouncer_key=denouncer_key,
                # CHG-165 §10: motivo del selector (en claro, sin PII) +
                # descripción cifrada.
                reason_category=payload.category,
                reason_encrypted=encrypt(payload.reason.strip()),
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar la denuncia.",
            )
        if result is None:
            return problem(
                404,
                "Lugar no disponible",
                "El lugar no existe.",
            )
        return AidLocationReportReceipt(
            location_id=result["id"],
            reports_count=result["reports_count"],
            under_observation=result["under_observation"],
            disabled=result.get("disabled", False),
        )

    # ------------------------------------------------------------------
    # CHG-176 — La misma comunidad para «Ofrecer comida»: comentarios
    # con estrellas, denuncias con sus umbrales y borrado admin. Comparte
    # tablas, reglas y modelos con los acopios.
    # ------------------------------------------------------------------

    @application.get(
        "/internal/v1/food-offers/{food_offer_id}/comments",
        response_model=AidLocationCommentsResponse,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def list_food_offer_comments(
        food_offer_id: UUID,
        data: Annotated[DisasterRepository, Depends(get_repository)],
        limit: int = 50,
    ):
        page = await data.list_food_offer_comments(
            food_offer_id=food_offer_id, limit=max(1, min(limit, 100))
        )
        if page is None:
            return problem(
                404, "Oferta no disponible", "La oferta no existe."
            )
        return AidLocationCommentsResponse(
            items=[
                AidLocationComment(
                    id=row["id"],
                    author_display_name=row["author_display_name"],
                    actor_kind=row["actor_kind"],
                    content=row["content"],
                    rating=row.get("rating"),
                    created_at=row["created_at"],
                )
                for row in page["items"]
            ],
            total=page["total"],
            rating_average=page["rating_average"],
            rating_count=page["rating_count"],
        )

    @application.post(
        "/internal/v1/food-offers/{food_offer_id}/comments",
        status_code=201,
        response_model=AidLocationComment,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def create_food_offer_comment(
        food_offer_id: UUID,
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
        author_display_name: str | None = None
        if actor_kind == "authenticated":
            display_raw = request.headers.get("x-actor-display", "").strip()
            if display_raw:
                try:
                    author_display_name = (
                        base64.b64decode(display_raw.encode()).decode()[:161]
                        or None
                    )
                except Exception:
                    author_display_name = None
        try:
            payload = AidLocationCommentInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        try:
            row = await data.create_food_offer_comment(
                idempotency_key=idempotency_key,
                food_offer_id=food_offer_id,
                actor_kind=actor_kind,
                account_id=account_id,
                author_display_name=author_display_name,
                content=payload.content.strip(),
                rating=payload.rating,
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible publicar el comentario.",
            )
        if row is None:
            return problem(
                404, "Oferta no disponible", "La oferta no existe."
            )
        return AidLocationComment(
            id=row["id"],
            author_display_name=row["author_display_name"],
            actor_kind=row["actor_kind"],
            content=row["content"],
            rating=row.get("rating"),
            created_at=row["created_at"],
        )

    @application.post(
        "/internal/v1/food-offers/{food_offer_id}/reports",
        status_code=202,
        response_model=FoodOfferReportReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def create_food_offer_report(
        food_offer_id: UUID,
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
        denouncer_key = request.headers.get("x-denouncer-key", "").strip()
        if not denouncer_key:
            return problem(
                422,
                "Denunciante no resuelto",
                "Falta la clave de denunciante resuelta por el gateway.",
            )
        try:
            payload = AidLocationReportInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        try:
            result = await data.create_food_offer_report(
                idempotency_key=idempotency_key,
                food_offer_id=food_offer_id,
                actor_kind=actor_kind,
                account_id=account_id,
                denouncer_key=denouncer_key,
                reason_category=payload.category,
                reason_encrypted=encrypt(payload.reason.strip()),
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar la denuncia.",
            )
        if result is None:
            return problem(
                404, "Oferta no disponible", "La oferta no existe."
            )
        return FoodOfferReportReceipt(
            food_offer_id=food_offer_id,
            reports_count=result["reports_count"],
            under_observation=result["under_observation"],
            disabled=result["disabled"],
        )

    @application.delete(
        "/internal/v1/admin/food-offers/{food_offer_id}/comments"
        "/{comment_id}",
        response_model=AidLocationCommentDeleteReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_delete_food_offer_comment(
        food_offer_id: UUID,
        comment_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display_name = actor
        deleted = await data.admin_delete_food_offer_comment(
            food_offer_id=food_offer_id,
            comment_id=comment_id,
            actor_account_id=actor_account_id,
            actor_display_name=actor_display_name,
        )
        if deleted == 0:
            return problem(
                404,
                "Comentario no encontrado",
                "El comentario no existe en esa oferta.",
            )
        return AidLocationCommentDeleteReceipt(deleted=deleted)

    @application.delete(
        "/internal/v1/admin/food-offers/{food_offer_id}",
        response_model=FoodOfferDeleteReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_delete_food_offer(
        food_offer_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display_name = actor
        deleted = await data.admin_delete_food_offer(
            food_offer_id=food_offer_id,
            actor_account_id=actor_account_id,
            actor_display_name=actor_display_name,
        )
        if deleted == 0:
            return problem(
                404, "Oferta no encontrada", "La oferta no existe."
            )
        return FoodOfferDeleteReceipt(deleted=deleted)

    # CHG-182 — «Mi casita destruida»: feed público, fotos públicas,
    # comunidad completa, bandeja de «Mi espacio» y borrado admin.

    def active_damaged_home_model(row: dict) -> ActiveDamagedHome:
        return ActiveDamagedHome(
            id=row["id"],
            public_code=row.get("public_code"),
            description=row["description"],
            department=row["department"],
            municipality=row["municipality"],
            address=row["address"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            household_size=row.get("household_size"),
            donation_channel=row.get("donation_channel"),
            donation_reference=row.get("donation_reference"),
            video_url=row.get("video_url"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            photo_urls=[
                f"/api/v1/public/damaged-homes/{row['id']}/photos/{photo_id}"
                for photo_id in (row.get("photo_ids") or [])
            ],
            comment_rating_average=row.get("comment_rating_average"),
            comment_rating_count=row.get("comment_rating_count") or 0,
        )

    @application.get(
        "/internal/v1/damaged-homes",
        response_model=DamagedHomePage,
        response_model_by_alias=True,
        tags=["BuildingReports"],
    )
    async def list_active_damaged_homes(
        data: Annotated[DisasterRepository, Depends(get_repository)],
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if limit not in (10, 25, 50):
            return problem(
                422,
                "Tamaño de página inválido",
                "El tamaño de página debe ser 10, 25 o 50.",
            )
        rows, total = await data.list_active_damaged_homes(limit, offset)
        return DamagedHomePage(
            items=[active_damaged_home_model(row) for row in rows],
            total=total,
            generated_at=datetime.now(UTC),
        )

    @application.get(
        "/internal/v1/public/damaged-homes/{damaged_home_id}"
        "/photos/{photo_id}",
        tags=["BuildingReports"],
    )
    async def serve_damaged_home_photo(
        damaged_home_id: UUID,
        photo_id: UUID,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        photo = await data.get_damaged_home_photo(
            damaged_home_id=damaged_home_id, photo_id=photo_id
        )
        if photo is None:
            return problem(
                404,
                "Fotografía no disponible",
                "La casita no tiene esa fotografía o ya no se publica.",
            )
        try:
            content = object_storage.load(photo["object_key"])
        except StorageUnavailableError:
            content = None
        if content is None:
            return problem(
                503,
                "Fotografía no disponible",
                "No fue posible leer la fotografía en este momento.",
            )
        from fastapi.responses import Response as RawResponse

        return RawResponse(
            content=content,
            media_type=photo["content_type"],
            headers={"Cache-Control": "private, max-age=300"},
        )

    @application.get(
        "/internal/v1/damaged-homes/{damaged_home_id}/comments",
        response_model=AidLocationCommentsResponse,
        response_model_by_alias=True,
        tags=["BuildingReports"],
    )
    async def list_damaged_home_comments(
        damaged_home_id: UUID,
        data: Annotated[DisasterRepository, Depends(get_repository)],
        limit: int = 50,
    ):
        page = await data.list_damaged_home_comments(
            damaged_home_id=damaged_home_id, limit=max(1, min(limit, 100))
        )
        if page is None:
            return problem(
                404, "Casita no disponible", "La publicación no existe."
            )
        return AidLocationCommentsResponse(
            items=[
                AidLocationComment(
                    id=row["id"],
                    author_display_name=row["author_display_name"],
                    actor_kind=row["actor_kind"],
                    content=row["content"],
                    rating=row.get("rating"),
                    created_at=row["created_at"],
                )
                for row in page["items"]
            ],
            total=page["total"],
            rating_average=page["rating_average"],
            rating_count=page["rating_count"],
        )

    @application.post(
        "/internal/v1/damaged-homes/{damaged_home_id}/comments",
        status_code=201,
        response_model=AidLocationComment,
        response_model_by_alias=True,
        tags=["BuildingReports"],
    )
    async def create_damaged_home_comment(
        damaged_home_id: UUID,
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
        author_display_name: str | None = None
        if actor_kind == "authenticated":
            display_raw = request.headers.get("x-actor-display", "").strip()
            if display_raw:
                try:
                    author_display_name = (
                        base64.b64decode(display_raw.encode()).decode()[:161]
                        or None
                    )
                except Exception:
                    author_display_name = None
        try:
            payload = AidLocationCommentInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        try:
            row = await data.create_damaged_home_comment(
                idempotency_key=idempotency_key,
                damaged_home_id=damaged_home_id,
                actor_kind=actor_kind,
                account_id=account_id,
                author_display_name=author_display_name,
                content=payload.content.strip(),
                rating=payload.rating,
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible publicar el comentario.",
            )
        if row is None:
            return problem(
                404, "Casita no disponible", "La publicación no existe."
            )

        # CHG-182 — Aviso a la dueña por el camino de CHG-054. Mejor
        # esfuerzo y fuera de la transacción: si el correo falla, el
        # comentario sigue publicado. Nunca se avisa de los comentarios
        # que ella misma escribe.
        owner_account_id = row.get("owner_account_id")
        if (
            row.get("created")
            and owner_account_id is not None
            and owner_account_id != account_id
        ):
            try:
                await report_notifier.notify_report_status(
                    owner_account_id,
                    "publicación de Mi casita destruida",
                    row.get("public_code") or "sin código",
                    "Alguien comentó tu publicación. Entra a «Mi "
                    "espacio» para leerlo.",
                )
            except Exception:
                pass

        return AidLocationComment(
            id=row["id"],
            author_display_name=row["author_display_name"],
            actor_kind=row["actor_kind"],
            content=row["content"],
            rating=row.get("rating"),
            created_at=row["created_at"],
        )

    @application.post(
        "/internal/v1/damaged-homes/{damaged_home_id}/reports",
        status_code=202,
        response_model=DamagedHomeComplaintReceipt,
        response_model_by_alias=True,
        tags=["BuildingReports"],
    )
    async def create_damaged_home_complaint(
        damaged_home_id: UUID,
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
        denouncer_key = request.headers.get("x-denouncer-key", "").strip()
        if not denouncer_key:
            return problem(
                422,
                "Denunciante no resuelto",
                "Falta la clave de denunciante resuelta por el gateway.",
            )
        try:
            payload = AidLocationReportInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        try:
            result = await data.create_damaged_home_complaint(
                idempotency_key=idempotency_key,
                damaged_home_id=damaged_home_id,
                actor_kind=actor_kind,
                account_id=account_id,
                denouncer_key=denouncer_key,
                reason_category=payload.category,
                reason_encrypted=encrypt(payload.reason.strip()),
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar la denuncia.",
            )
        if result is None:
            return problem(
                404, "Casita no disponible", "La publicación no existe."
            )
        return DamagedHomeComplaintReceipt(
            damaged_home_id=damaged_home_id,
            reports_count=result["reports_count"],
            under_observation=result["under_observation"],
            disabled=result["disabled"],
        )

    # CHG-202 — La dueña elimina su propia casita. Reutiliza el borrado
    # del super_admin: comentarios, denuncias y fotos caen por CASCADE y
    # los binarios salen del almacén. La casita no expira sola, así que
    # este es su único camino de salida sin pedírselo a nadie.
    @application.delete(
        "/internal/v1/me/damaged-homes/{damaged_home_id}",
        status_code=204,
        tags=["BuildingReports"],
    )
    async def delete_own_damaged_home(
        damaged_home_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_kind, account_id = actor
        if actor_kind != "authenticated" or account_id is None:
            return problem(
                401,
                "Sesión requerida",
                "Eliminar una casita exige la cuenta que la publicó.",
            )
        keys = await data.delete_own_damaged_home(damaged_home_id, account_id)
        if keys is None:
            # Ajena o inexistente: misma respuesta a propósito.
            return problem(
                404,
                "Casita no disponible",
                "La casita no existe o no es tuya.",
            )
        for key in keys:
            object_storage.delete(key)
        from fastapi.responses import Response as RawResponse

        return RawResponse(status_code=204)

    @application.get(
        "/internal/v1/me/damaged-homes",
        response_model=MyDamagedHomesResponse,
        response_model_by_alias=True,
        tags=["BuildingReports"],
    )
    async def list_my_damaged_homes(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_kind, account_id = actor
        if actor_kind != "authenticated" or account_id is None:
            return problem(
                401,
                "Sesión requerida",
                "Esta bandeja exige una cuenta.",
            )
        rows = await data.list_my_damaged_homes(account_id)
        items = [
            MyDamagedHome(
                **active_damaged_home_model(row).model_dump(by_alias=False),
                published=bool(row["visible"]) and row["disabled_at"] is None,
                unread_comments=int(row.get("unread_comments") or 0),
                comments_count=int(row.get("comments_count") or 0),
            )
            for row in rows
        ]
        return MyDamagedHomesResponse(
            items=items,
            total=len(items),
            unread_total=sum(item.unread_comments for item in items),
        )

    @application.post(
        "/internal/v1/me/damaged-homes/{damaged_home_id}/comments-seen",
        status_code=204,
        tags=["BuildingReports"],
    )
    async def mark_damaged_home_comments_seen(
        damaged_home_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_kind, account_id = actor
        if actor_kind != "authenticated" or account_id is None:
            return problem(
                401, "Sesión requerida", "Esta acción exige una cuenta."
            )
        marked = await data.mark_damaged_home_comments_seen(
            damaged_home_id=damaged_home_id, account_id=account_id
        )
        if not marked:
            return problem(
                404,
                "Casita no disponible",
                "La publicación no existe o no es tuya.",
            )
        from fastapi.responses import Response as RawResponse

        return RawResponse(status_code=204)

    @application.delete(
        "/internal/v1/admin/damaged-homes/{damaged_home_id}/comments"
        "/{comment_id}",
        response_model=AidLocationCommentDeleteReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_delete_damaged_home_comment(
        damaged_home_id: UUID,
        comment_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display_name = actor
        deleted = await data.admin_delete_damaged_home_comment(
            damaged_home_id=damaged_home_id,
            comment_id=comment_id,
            actor_account_id=actor_account_id,
            actor_display_name=actor_display_name,
        )
        if deleted == 0:
            return problem(
                404,
                "Comentario no encontrado",
                "El comentario no existe en esa publicación.",
            )
        return AidLocationCommentDeleteReceipt(deleted=deleted)

    @application.delete(
        "/internal/v1/admin/damaged-homes/{damaged_home_id}",
        response_model=DamagedHomeDeleteReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_delete_damaged_home(
        damaged_home_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display_name = actor
        deleted, storage_keys = await data.admin_delete_damaged_home(
            damaged_home_id=damaged_home_id,
            actor_account_id=actor_account_id,
            actor_display_name=actor_display_name,
        )
        if deleted == 0:
            return problem(
                404, "Casita no encontrada", "La publicación no existe."
            )
        # Los binarios se limpian fuera de la transacción: si el
        # almacenamiento falla, la fila ya no existe igual.
        for key in storage_keys:
            try:
                object_storage.delete(key)
            except Exception:
                pass
        return DamagedHomeDeleteReceipt(deleted=deleted)

    # CHG-180 — Comunidad de «Necesitamos ayuda»: comentarios con
    # estrellas, denuncia y borrado administrativo, con las mismas
    # reglas del acopio local (CHG-165/166/167) y de la oferta de
    # comida (CHG-176). Solo cambia el objetivo.
    @application.get(
        "/internal/v1/help-requests/{help_request_id}/comments",
        response_model=AidLocationCommentsResponse,
        response_model_by_alias=True,
        tags=["HelpRequests"],
    )
    async def list_help_request_comments(
        help_request_id: UUID,
        data: Annotated[DisasterRepository, Depends(get_repository)],
        limit: int = 50,
    ):
        page = await data.list_help_request_comments(
            help_request_id=help_request_id, limit=max(1, min(limit, 100))
        )
        if page is None:
            return problem(
                404,
                "Solicitud no disponible",
                "La solicitud no existe.",
            )
        return AidLocationCommentsResponse(
            items=[
                AidLocationComment(
                    id=row["id"],
                    author_display_name=row["author_display_name"],
                    actor_kind=row["actor_kind"],
                    content=row["content"],
                    rating=row.get("rating"),
                    created_at=row["created_at"],
                )
                for row in page["items"]
            ],
            total=page["total"],
            rating_average=page["rating_average"],
            rating_count=page["rating_count"],
        )

    @application.post(
        "/internal/v1/help-requests/{help_request_id}/comments",
        status_code=201,
        response_model=AidLocationComment,
        response_model_by_alias=True,
        tags=["HelpRequests"],
    )
    async def create_help_request_comment(
        help_request_id: UUID,
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
        author_display_name: str | None = None
        if actor_kind == "authenticated":
            display_raw = request.headers.get("x-actor-display", "").strip()
            if display_raw:
                try:
                    author_display_name = (
                        base64.b64decode(display_raw.encode()).decode()[:161]
                        or None
                    )
                except Exception:
                    author_display_name = None
        try:
            payload = AidLocationCommentInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        try:
            row = await data.create_help_request_comment(
                idempotency_key=idempotency_key,
                help_request_id=help_request_id,
                actor_kind=actor_kind,
                account_id=account_id,
                author_display_name=author_display_name,
                content=payload.content.strip(),
                rating=payload.rating,
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible publicar el comentario.",
            )
        if row is None:
            return problem(
                404,
                "Solicitud no disponible",
                "La solicitud no existe.",
            )
        return AidLocationComment(
            id=row["id"],
            author_display_name=row["author_display_name"],
            actor_kind=row["actor_kind"],
            content=row["content"],
            rating=row.get("rating"),
            created_at=row["created_at"],
        )

    @application.post(
        "/internal/v1/help-requests/{help_request_id}/reports",
        status_code=202,
        response_model=HelpRequestReportReceipt,
        response_model_by_alias=True,
        tags=["HelpRequests"],
    )
    async def create_help_request_report(
        help_request_id: UUID,
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
        denouncer_key = request.headers.get("x-denouncer-key", "").strip()
        if not denouncer_key:
            return problem(
                422,
                "Denunciante no resuelto",
                "Falta la clave de denunciante resuelta por el gateway.",
            )
        try:
            payload = AidLocationReportInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        try:
            result = await data.create_help_request_report(
                idempotency_key=idempotency_key,
                help_request_id=help_request_id,
                actor_kind=actor_kind,
                account_id=account_id,
                denouncer_key=denouncer_key,
                reason_category=payload.category,
                reason_encrypted=encrypt(payload.reason.strip()),
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar la denuncia.",
            )
        if result is None:
            return problem(
                404,
                "Solicitud no disponible",
                "La solicitud no existe.",
            )
        return HelpRequestReportReceipt(
            help_request_id=help_request_id,
            reports_count=result["reports_count"],
            under_observation=result["under_observation"],
            disabled=result["disabled"],
        )

    @application.delete(
        "/internal/v1/admin/help-requests/{help_request_id}/comments"
        "/{comment_id}",
        response_model=AidLocationCommentDeleteReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_delete_help_request_comment(
        help_request_id: UUID,
        comment_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display_name = actor
        deleted = await data.admin_delete_help_request_comment(
            help_request_id=help_request_id,
            comment_id=comment_id,
            actor_account_id=actor_account_id,
            actor_display_name=actor_display_name,
        )
        if deleted == 0:
            return problem(
                404,
                "Comentario no encontrado",
                "El comentario no existe en esa solicitud.",
            )
        return AidLocationCommentDeleteReceipt(deleted=deleted)

    # CHG-165 §4-8 — Comentarios públicos de un lugar de ayuda: los ve
    # cualquiera; comentan anónimos y cuentas por igual.
    @application.get(
        "/internal/v1/aid-locations/{location_id}/comments",
        response_model=AidLocationCommentsResponse,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def list_aid_location_comments(
        location_id: UUID,
        data: Annotated[DisasterRepository, Depends(get_repository)],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ):
        try:
            result = await data.list_aid_location_comments(
                location_id=location_id, limit=limit
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Consulta no disponible",
                "No fue posible consultar los comentarios.",
            )
        if result is None:
            return problem(404, "Lugar no disponible", "El lugar no existe.")
        return AidLocationCommentsResponse(
            items=[
                AidLocationComment(
                    id=row["id"],
                    author_display_name=row["author_display_name"],
                    actor_kind=row["actor_kind"],
                    content=row["content"],
                    rating=row.get("rating"),
                    created_at=row["created_at"],
                )
                for row in result["items"]
            ],
            total=result["total"],
            # CHG-166: promedio de estrellas calculado en el servicio.
            rating_average=result.get("rating_average"),
            rating_count=result.get("rating_count", 0),
        )

    @application.post(
        "/internal/v1/aid-locations/{location_id}/comments",
        status_code=201,
        response_model=AidLocationComment,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def create_aid_location_comment(
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
        # Nombre visible congelado al publicar; solo llega para cuentas
        # (el gateway lo resuelve; jamás correo/teléfono, §5).
        author_display_name: str | None = None
        if actor_kind == "authenticated":
            display_raw = request.headers.get("x-actor-display", "").strip()
            if display_raw:
                try:
                    author_display_name = (
                        base64.b64decode(display_raw.encode()).decode()[:161]
                        or None
                    )
                except Exception:
                    author_display_name = None

        body = await request.body()
        try:
            payload = AidLocationCommentInput.model_validate_json(body)
        except ValidationError as error:
            return invalid_fields_problem(error)

        try:
            row = await data.create_aid_location_comment(
                idempotency_key=idempotency_key,
                location_id=location_id,
                actor_kind=actor_kind,
                account_id=account_id,
                author_display_name=author_display_name,
                content=payload.content.strip(),
                # CHG-166: calificación 1-5 obligatoria.
                rating=payload.rating,
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible publicar el comentario.",
            )
        if row is None:
            return problem(404, "Lugar no disponible", "El lugar no existe.")
        return AidLocationComment(
            id=row["id"],
            author_display_name=row["author_display_name"],
            actor_kind=row["actor_kind"],
            content=row["content"],
            rating=row.get("rating"),
            created_at=row["created_at"],
        )

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
            "El reporte requiere entre una y tres fotografías.",
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
            pending_reason_detail_protected=encrypt(
                payload.pending_reason_detail
            ),
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
                    stored_report,
                    stored_files,
                    related_event_name=payload.related_event_name,
                )
            )
        except asyncpg.ForeignKeyViolationError:
            cleanup()
            return problem(
                422,
                "Datos inválidos",
                invalid_fields_detail(["relatedDisasterId"]),
                fields=["relatedDisasterId"],
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
                invalid_fields_detail(["relatedDisasterId"]),
                fields=["relatedDisasterId"],
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
                # CHG-220: altitud y su precisión, nulas sin fix real.
                altitude_meters=payload.altitude_meters,
                altitude_accuracy_meters=payload.altitude_accuracy_meters,
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

    # CHG-069 — "Mi espacio": reportes propios (con novedades que otras
    # personas aportaron a sus casos) y alertas de voluntariado.
    # Exclusivo de cuentas autenticadas resueltas por el gateway.

    def volunteer_alert_model(row: dict) -> VolunteerAlert:
        return VolunteerAlert(
            id=row["id"],
            description=row["description"],
            address=row["address"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @application.get(
        "/internal/v1/me/reports",
        response_model=MyReportsPage,
        response_model_by_alias=True,
        tags=["MySpace"],
    )
    async def list_my_reports(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = require_offer_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        rows = await data.list_my_reports(account_id)
        missing_ids = [
            row["id"]
            for row in rows
            if row["kind"] == "missing_person_report"
        ]
        novelties = await data.list_report_novelties(
            account_id, missing_ids
        )
        return MyReportsPage(
            items=[
                MyReportSummary(
                    id=row["id"],
                    kind=row["kind"],
                    reference_code=row["reference_code"],
                    title=row["title"],
                    status=row["status"],
                    received_at=row["received_at"],
                    novelties=[
                        MyReportNovelty(
                            claimed_outcome=item["claimed_outcome"],
                            moderation_status=item["moderation_status"],
                            received_at=item["received_at"],
                        )
                        for item in novelties.get(row["id"], [])[:50]
                    ],
                )
                for row in rows
            ],
            total=len(rows),
            generated_at=datetime.now(UTC),
        )

    @application.post(
        "/internal/v1/me/volunteer-alerts",
        status_code=201,
        response_model=VolunteerAlert,
        response_model_by_alias=True,
        tags=["MySpace"],
    )
    async def create_volunteer_alert(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = require_offer_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        try:
            payload = VolunteerAlertInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        row = await data.create_volunteer_alert(
            account_id,
            payload.description.strip(),
            payload.address.strip(),
            payload.latitude,
            payload.longitude,
        )
        return volunteer_alert_model(row)

    @application.get(
        "/internal/v1/me/volunteer-alerts",
        response_model=VolunteerAlertPage,
        response_model_by_alias=True,
        tags=["MySpace"],
    )
    async def list_my_volunteer_alerts(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = require_offer_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        rows = await data.list_my_volunteer_alerts(account_id)
        return VolunteerAlertPage(
            items=[volunteer_alert_model(row) for row in rows[:100]],
            total=len(rows),
            generated_at=datetime.now(UTC),
        )

    @application.post(
        "/internal/v1/me/volunteer-alerts/{alert_id}/resolve",
        response_model=VolunteerAlert,
        response_model_by_alias=True,
        tags=["MySpace"],
    )
    async def resolve_volunteer_alert(
        alert_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = require_offer_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        row = await data.resolve_volunteer_alert(account_id, alert_id)
        if row is None:
            return problem(
                404,
                "Alerta no disponible",
                "La alerta no existe, no es tuya o ya fue resuelta.",
            )
        return volunteer_alert_model(row)

    # CHG-125 — «Necesitamos ayuda»: creación pública (anónima o con
    # cuenta), listado vigente con conteo de atención, atención
    # idempotente y fotografía pública. La expiración la imponen las
    # consultas del repositorio (DEC-125-02).

    def active_help_request_model(row: dict) -> ActiveHelpRequest:
        return ActiveHelpRequest(
            id=row["id"],
            description=row["description"],
            address=row["address"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            notification_radius_km=row.get("notification_radius_km"),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            attenders_count=row["attenders_count"],
            attended_by_me=bool(row["attended_by_me"]),
            created_by_me=bool(row.get("created_by_me")),
            photo_url=(
                f"/api/v1/public/help-requests/{row['id']}/photo"
                if row.get("has_photo")
                else None
            ),
            # CHG-180: sin esto la puntuación se quedaba en la consulta
            # y nunca llegaba al popup del mapa (el mismo descuido que
            # CHG-176 corrigió en las ofertas).
            comment_rating_average=row.get("comment_rating_average"),
            comment_rating_count=row.get("comment_rating_count") or 0,
        )

    @application.post(
        "/internal/v1/help-requests",
        status_code=201,
        response_model=HelpRequestReceipt,
        response_model_by_alias=True,
        tags=["HelpRequests"],
    )
    async def create_help_request(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        idempotency_key = validate_idempotency_key(request)
        if isinstance(idempotency_key, JSONResponse):
            return idempotency_key
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        _actor_kind, reporter_account_id = actor

        oversized = check_declared_length(request)
        if oversized is not None:
            return oversized

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
            payload = HelpRequestInput.model_validate_json(raw_payload)
        except ValidationError as error:
            return invalid_fields_problem(error)

        prepared = await prepare_photo_parts(
            form,
            0,
            1,
            "La solicitud admite máximo una fotografía del lugar.",
        )
        if isinstance(prepared, JSONResponse):
            return prepared

        received_at = datetime.now(UTC)
        request_id = uuid4()
        saved_keys: list[str] = []

        def cleanup() -> None:
            for key in saved_keys:
                object_storage.delete(key)

        try:
            stored = store_photos(
                f"help-requests/{request_id}", prepared, saved_keys
            )
        except PhotoProcessingError:
            cleanup()
            return problem(
                415,
                "Fotografía no procesable",
                "La fotografía no pudo validarse como imagen segura.",
            )
        except StorageUnavailableError:
            cleanup()
            return problem(
                503,
                "Almacenamiento no disponible",
                "No fue posible resguardar la fotografía; la solicitud "
                "no fue registrada.",
            )
        photo = stored[0] if stored else None

        try:
            row, created = await data.create_help_request(
                idempotency_key=idempotency_key,
                public_code=generate_help_request_code(received_at),
                reporter_account_id=reporter_account_id,
                description=payload.description.strip(),
                address=payload.address.strip(),
                latitude=payload.latitude,
                longitude=payload.longitude,
                notification_radius_km=payload.notification_radius_km,
                duration_hours=payload.duration_hours,
                photo_storage_key=(
                    photo.storage_key if photo else None
                ),
                photo_derived_storage_key=(
                    photo.derived_storage_key if photo else None
                ),
                photo_content_type=(
                    photo.content_type if photo else None
                ),
            )
        except asyncpg.PostgresError:
            cleanup()
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar la solicitud; ningún dato "
                "quedó publicado.",
            )

        if not created:
            # Reintento idempotente: los archivos de este intento sobran.
            cleanup()

        return HelpRequestReceipt(
            id=row["id"],
            public_code=row["public_code"],
            status="active",
            received_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    @application.get(
        "/internal/v1/help-requests",
        response_model=HelpRequestPage,
        response_model_by_alias=True,
        tags=["HelpRequests"],
    )
    async def list_active_help_requests(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if limit not in (10, 25, 50):
            return problem(
                422,
                "Tamaño de página inválido",
                "El tamaño de página debe ser 10, 25 o 50.",
            )
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        _actor_kind, account_id = actor
        rows, total = await data.list_active_help_requests(
            limit, offset, account_id
        )
        return HelpRequestPage(
            items=[active_help_request_model(row) for row in rows],
            total=total,
            generated_at=datetime.now(UTC),
        )

    # CHG-163 — «Ofrecer comida»: mismas reglas que las solicitudes de
    # ayuda (anónimo permitido, vigencia server-side, JSON sin fotos en
    # F1). El mapa la pinta desde el endpoint dedicado (DEC-125-10).
    @application.post(
        "/internal/v1/food-offers",
        status_code=201,
        response_model=FoodOfferReceipt,
        response_model_by_alias=True,
        tags=["FoodOffers"],
    )
    async def create_food_offer(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        idempotency_key = validate_idempotency_key(request)
        if isinstance(idempotency_key, JSONResponse):
            return idempotency_key
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        _actor_kind, reporter_account_id = actor
        try:
            payload = FoodOfferInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        received_at = datetime.now(UTC)
        try:
            row, _created = await data.create_food_offer(
                idempotency_key=idempotency_key,
                public_code=generate_food_offer_code(received_at),
                reporter_account_id=reporter_account_id,
                description=payload.description.strip(),
                address=payload.address.strip(),
                latitude=payload.latitude,
                longitude=payload.longitude,
                notification_radius_km=payload.notification_radius_km,
                duration_hours=payload.duration_hours,
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar la oferta; ningún dato "
                "quedó publicado.",
            )
        return FoodOfferReceipt(
            id=row["id"],
            public_code=row["public_code"],
            status="active",
            received_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    @application.get(
        "/internal/v1/food-offers",
        response_model=FoodOfferPage,
        response_model_by_alias=True,
        tags=["FoodOffers"],
    )
    async def list_active_food_offers(
        data: Annotated[DisasterRepository, Depends(get_repository)],
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if limit not in (10, 25, 50):
            return problem(
                422,
                "Tamaño de página inválido",
                "El tamaño de página debe ser 10, 25 o 50.",
            )
        rows, total = await data.list_active_food_offers(limit, offset)
        return FoodOfferPage(
            items=[
                ActiveFoodOffer(
                    id=row["id"],
                    description=row["description"],
                    address=row["address"],
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                    notification_radius_km=row.get(
                        "notification_radius_km"
                    ),
                    created_at=row["created_at"],
                    expires_at=row["expires_at"],
                    # CHG-176: sin esto la puntuación se quedaba en la
                    # consulta y nunca llegaba al mapa.
                    comment_rating_average=row.get(
                        "comment_rating_average"
                    ),
                    comment_rating_count=row.get("comment_rating_count")
                    or 0,
                )
                for row in rows
            ],
            total=total,
            generated_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # CHG-205 — «Ofrecer alojamiento temporal»: las mismas rutas que la
    # oferta de comida, sobre su propia tabla. Anónimo permitido,
    # vigencia en servidor y comunidad completa.
    # ------------------------------------------------------------------

    @application.get(
        "/internal/v1/shelter-offers/{shelter_offer_id}/comments",
        response_model=AidLocationCommentsResponse,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def list_shelter_offer_comments(
        shelter_offer_id: UUID,
        data: Annotated[DisasterRepository, Depends(get_repository)],
        limit: int = 50,
    ):
        page = await data.list_shelter_offer_comments(
            shelter_offer_id=shelter_offer_id, limit=max(1, min(limit, 100))
        )
        if page is None:
            return problem(
                404, "Oferta no disponible", "La oferta no existe."
            )
        return AidLocationCommentsResponse(
            items=[
                AidLocationComment(
                    id=row["id"],
                    author_display_name=row["author_display_name"],
                    actor_kind=row["actor_kind"],
                    content=row["content"],
                    rating=row.get("rating"),
                    created_at=row["created_at"],
                )
                for row in page["items"]
            ],
            total=page["total"],
            rating_average=page["rating_average"],
            rating_count=page["rating_count"],
        )

    @application.post(
        "/internal/v1/shelter-offers/{shelter_offer_id}/comments",
        status_code=201,
        response_model=AidLocationComment,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def create_shelter_offer_comment(
        shelter_offer_id: UUID,
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
        author_display_name: str | None = None
        if actor_kind == "authenticated":
            display_raw = request.headers.get("x-actor-display", "").strip()
            if display_raw:
                try:
                    author_display_name = (
                        base64.b64decode(display_raw.encode()).decode()[:161]
                        or None
                    )
                except Exception:
                    author_display_name = None
        try:
            payload = AidLocationCommentInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        try:
            row = await data.create_shelter_offer_comment(
                idempotency_key=idempotency_key,
                shelter_offer_id=shelter_offer_id,
                actor_kind=actor_kind,
                account_id=account_id,
                author_display_name=author_display_name,
                content=payload.content.strip(),
                rating=payload.rating,
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible publicar el comentario.",
            )
        if row is None:
            return problem(
                404, "Oferta no disponible", "La oferta no existe."
            )
        return AidLocationComment(
            id=row["id"],
            author_display_name=row["author_display_name"],
            actor_kind=row["actor_kind"],
            content=row["content"],
            rating=row.get("rating"),
            created_at=row["created_at"],
        )

    @application.post(
        "/internal/v1/shelter-offers/{shelter_offer_id}/reports",
        status_code=202,
        response_model=ShelterOfferReportReceipt,
        response_model_by_alias=True,
        tags=["HumanitarianDirectory"],
    )
    async def create_shelter_offer_report(
        shelter_offer_id: UUID,
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
        denouncer_key = request.headers.get("x-denouncer-key", "").strip()
        if not denouncer_key:
            return problem(
                422,
                "Denunciante no resuelto",
                "Falta la clave de denunciante resuelta por el gateway.",
            )
        try:
            payload = AidLocationReportInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        try:
            result = await data.create_shelter_offer_report(
                idempotency_key=idempotency_key,
                shelter_offer_id=shelter_offer_id,
                actor_kind=actor_kind,
                account_id=account_id,
                denouncer_key=denouncer_key,
                reason_category=payload.category,
                reason_encrypted=encrypt(payload.reason.strip()),
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar la denuncia.",
            )
        if result is None:
            return problem(
                404, "Oferta no disponible", "La oferta no existe."
            )
        return ShelterOfferReportReceipt(
            shelter_offer_id=shelter_offer_id,
            reports_count=result["reports_count"],
            under_observation=result["under_observation"],
            disabled=result["disabled"],
        )

    @application.delete(
        "/internal/v1/admin/shelter-offers/{shelter_offer_id}/comments"
        "/{comment_id}",
        response_model=AidLocationCommentDeleteReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_delete_shelter_offer_comment(
        shelter_offer_id: UUID,
        comment_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display_name = actor
        deleted = await data.admin_delete_shelter_offer_comment(
            shelter_offer_id=shelter_offer_id,
            comment_id=comment_id,
            actor_account_id=actor_account_id,
            actor_display_name=actor_display_name,
        )
        if deleted == 0:
            return problem(
                404,
                "Comentario no encontrado",
                "El comentario no existe en esa oferta.",
            )
        return AidLocationCommentDeleteReceipt(deleted=deleted)

    @application.delete(
        "/internal/v1/admin/shelter-offers/{shelter_offer_id}",
        response_model=ShelterOfferDeleteReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_delete_shelter_offer(
        shelter_offer_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display_name = actor
        deleted = await data.admin_delete_shelter_offer(
            shelter_offer_id=shelter_offer_id,
            actor_account_id=actor_account_id,
            actor_display_name=actor_display_name,
        )
        if deleted == 0:
            return problem(
                404, "Oferta no encontrada", "La oferta no existe."
            )
        return ShelterOfferDeleteReceipt(deleted=deleted)

    @application.post(
        "/internal/v1/shelter-offers",
        status_code=201,
        response_model=ShelterOfferReceipt,
        response_model_by_alias=True,
        tags=["ShelterOffers"],
    )
    async def create_shelter_offer(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        idempotency_key = validate_idempotency_key(request)
        if isinstance(idempotency_key, JSONResponse):
            return idempotency_key
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        _actor_kind, reporter_account_id = actor
        try:
            payload = ShelterOfferInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        received_at = datetime.now(UTC)
        try:
            row, _created = await data.create_shelter_offer(
                idempotency_key=idempotency_key,
                public_code=generate_shelter_offer_code(received_at),
                reporter_account_id=reporter_account_id,
                description=payload.description.strip(),
                address=payload.address.strip(),
                latitude=payload.latitude,
                longitude=payload.longitude,
                notification_radius_km=payload.notification_radius_km,
                duration_hours=payload.duration_hours,
                spaces_available=payload.spaces_available,
                shared_space=payload.shared_space,
                accepts_pets=payload.accepts_pets,
                accessibility_notes=payload.accessibility_notes,
            )
        except asyncpg.PostgresError:
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar la oferta; ningún dato "
                "quedó publicado.",
            )
        return ShelterOfferReceipt(
            id=row["id"],
            public_code=row["public_code"],
            status="active",
            received_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    @application.get(
        "/internal/v1/shelter-offers",
        response_model=ShelterOfferPage,
        response_model_by_alias=True,
        tags=["ShelterOffers"],
    )
    async def list_active_shelter_offers(
        data: Annotated[DisasterRepository, Depends(get_repository)],
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if limit not in (10, 25, 50):
            return problem(
                422,
                "Tamaño de página inválido",
                "El tamaño de página debe ser 10, 25 o 50.",
            )
        rows, total = await data.list_active_shelter_offers(limit, offset)
        return ShelterOfferPage(
            items=[
                ActiveShelterOffer(
                    id=row["id"],
                    description=row["description"],
                    address=row["address"],
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                    notification_radius_km=row.get(
                        "notification_radius_km"
                    ),
                    spaces_available=row["spaces_available"],
                    shared_space=row["shared_space"],
                    accepts_pets=row["accepts_pets"],
                    accessibility_notes=row.get("accessibility_notes"),
                    created_at=row["created_at"],
                    expires_at=row["expires_at"],
                    # CHG-205 (heredado de CHG-176): sin esto la puntuación se quedaba en la
                    # consulta y nunca llegaba al mapa.
                    comment_rating_average=row.get(
                        "comment_rating_average"
                    ),
                    comment_rating_count=row.get("comment_rating_count")
                    or 0,
                )
                for row in rows
            ],
            total=total,
            generated_at=datetime.now(UTC),
        )

    @application.post(
        "/internal/v1/help-requests/{request_id}/attend",
        response_model=HelpRequestAttendReceipt,
        response_model_by_alias=True,
        tags=["HelpRequests"],
    )
    async def attend_help_request(
        request_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_kind, account_id = actor
        if actor_kind != "authenticated" or account_id is None:
            return problem(
                401,
                "Sesión requerida",
                "Atender una solicitud exige una cuenta autenticada "
                "resuelta por el gateway.",
            )
        # CHG-193: el cuerpo es opcional — un cliente anterior no lo
        # manda y entonces no se comparte nada. La instantánea (nombre y
        # teléfono) la rellena el gateway desde la sesión; el navegador
        # no elige con qué nombre figura nadie.
        raw_body = await request.body()
        try:
            attend_input = (
                HelpRequestAttendInput.model_validate_json(raw_body)
                if raw_body
                else HelpRequestAttendInput()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)

        row = await data.attend_help_request(
            request_id,
            account_id,
            shares_identity=attend_input.shares_identity,
            name_encrypted=(
                encrypt(attend_input.name)
                if attend_input.shares_identity
                else None
            ),
            phone_encrypted=(
                encrypt(attend_input.phone)
                if attend_input.shares_identity
                else None
            ),
        )
        if row is None:
            return problem(
                404,
                "Solicitud no disponible",
                "La solicitud no existe o ya expiró.",
            )
        return HelpRequestAttendReceipt(
            id=row["id"],
            attenders_count=row["attenders_count"],
            attending=True,
        )

    # CHG-148 — Voluntario ANÓNIMO desde el detalle del mapa: sin
    # cuenta. Recoge solo datos personales del propio voluntario, los
    # cifra (solo super_admin) y aumenta el contador. Reintento seguro
    # por Idempotency-Key. Quien tiene cuenta usa /attend, no esto.
    @application.post(
        "/internal/v1/help-requests/{request_id}/volunteers",
        response_model=HelpRequestAttendReceipt,
        response_model_by_alias=True,
        tags=["HelpRequests"],
    )
    async def volunteer_for_help_request(
        request_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        idempotency_key = validate_idempotency_key(request)
        if isinstance(idempotency_key, JSONResponse):
            return idempotency_key

        oversized = check_declared_length(request)
        if oversized is not None:
            return oversized

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
            payload = HelpRequestVolunteerInput.model_validate_json(
                raw_payload
            )
        except ValidationError as error:
            return invalid_fields_problem(error)

        prepared = await prepare_photo_parts(
            form,
            0,
            1,
            "El voluntario admite máximo una fotografía.",
        )
        if isinstance(prepared, JSONResponse):
            return prepared

        volunteer_id = uuid4()
        saved_keys: list[str] = []

        def cleanup() -> None:
            for key in saved_keys:
                object_storage.delete(key)

        try:
            stored = store_photos(
                f"help-request-volunteers/{volunteer_id}",
                prepared,
                saved_keys,
            )
        except PhotoProcessingError:
            cleanup()
            return problem(
                415,
                "Fotografía no procesable",
                "La fotografía no pudo validarse como imagen segura.",
            )
        except StorageUnavailableError:
            cleanup()
            return problem(
                503,
                "Almacenamiento no disponible",
                "No fue posible resguardar la fotografía; el voluntario "
                "no quedó registrado.",
            )
        photo = stored[0] if stored else None

        try:
            result = await data.create_help_request_volunteer(
                idempotency_key=idempotency_key,
                request_id=request_id,
                name_encrypted=encrypt(payload.name.strip()),
                phone_encrypted=encrypt(payload.phone),
                email_encrypted=encrypt(payload.email),
                photo_storage_key=photo.storage_key if photo else None,
                photo_derived_storage_key=(
                    photo.derived_storage_key if photo else None
                ),
                photo_content_type=(
                    photo.content_type if photo else None
                ),
                shares_contact=payload.shares_contact,
            )
        except asyncpg.PostgresError:
            cleanup()
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar al voluntario; nada quedó "
                "guardado.",
            )

        if result is None:
            cleanup()
            return problem(
                404,
                "Solicitud no disponible",
                "La solicitud no existe o ya expiró.",
            )
        row, created = result
        if not created:
            # Reintento idempotente: los archivos de este intento sobran.
            cleanup()
        return HelpRequestAttendReceipt(
            id=row["id"],
            attenders_count=row["attenders_count"],
            attending=True,
        )

    # CHG-193 — Quién atiende MI solicitud. Solo para su dueña: la
    # consulta de una solicitud ajena y la de una inexistente responden
    # lo mismo, para no delatar cuáles existen.
    # CHG-196 — La dueña elimina su propia solicitud. Reutiliza el
    # borrado del super_admin: la fila se va, atendedores y voluntarios
    # caen por CASCADE y las fotos salen del almacén (DEC-196-01).
    @application.delete(
        "/internal/v1/help-requests/{request_id}",
        status_code=204,
        tags=["HelpRequests"],
    )
    async def delete_own_help_request(
        request_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_kind, account_id = actor
        if actor_kind != "authenticated" or account_id is None:
            return problem(
                401,
                "Sesión requerida",
                "Eliminar una solicitud exige la cuenta que la creó.",
            )
        row = await data.delete_own_help_request(request_id, account_id)
        if row is None:
            # Ajena o inexistente: misma respuesta a propósito.
            return problem(
                404,
                "Solicitud no disponible",
                "La solicitud no existe o no es tuya.",
            )
        for key in (
            row.get("photo_storage_key"),
            row.get("photo_derived_storage_key"),
        ):
            if key:
                object_storage.delete(key)
        from fastapi.responses import Response as RawResponse

        return RawResponse(status_code=204)

    @application.get(
        "/internal/v1/help-requests/{request_id}/attenders",
        response_model=HelpRequestAttendersPage,
        response_model_by_alias=True,
        tags=["HelpRequests"],
    )
    async def list_help_request_attenders(
        request_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_kind, account_id = actor
        if actor_kind != "authenticated" or account_id is None:
            return problem(
                401,
                "Sesión requerida",
                "Ver quién atiende una solicitud exige la cuenta que la "
                "creó.",
            )
        rows = await data.list_help_request_attenders(
            request_id, account_id
        )
        if rows is None:
            return problem(
                404,
                "Solicitud no disponible",
                "La solicitud no existe o no es tuya.",
            )
        items = []
        for row in rows:
            shared = bool(row["shares_contact"])
            items.append(
                HelpRequestAttender(
                    id=row["id"],
                    kind=row["kind"],
                    joined_at=row["created_at"],
                    shares_contact=shared,
                    name=decrypt_text(row["name_encrypted"])
                    if shared
                    else None,
                    phone=decrypt_text(row["phone_encrypted"])
                    if shared
                    else None,
                    photo_url=(
                        "/api/v1/help-requests/"
                        f"{request_id}/attenders/{row['id']}/photo"
                        if shared and row["has_photo"]
                        else None
                    ),
                )
            )
        return HelpRequestAttendersPage(
            items=items,
            total=len(items),
            generated_at=datetime.now(UTC),
        )

    @application.get(
        "/internal/v1/help-requests/{request_id}/attenders/"
        "{volunteer_id}/photo",
        tags=["HelpRequests"],
    )
    async def serve_help_request_attender_photo(
        request_id: UUID,
        volunteer_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_kind, account_id = actor
        if actor_kind != "authenticated" or account_id is None:
            return problem(
                401,
                "Sesión requerida",
                "La fotografía de quien atiende es de su solicitud.",
            )
        photo = await data.get_help_request_volunteer_photo(
            request_id, volunteer_id, account_id
        )
        if photo is None:
            return problem(
                404,
                "Fotografía no disponible",
                "No hay fotografía compartida para esa persona.",
            )
        try:
            content = object_storage.load(photo["object_key"])
        except StorageUnavailableError:
            content = None
        if content is None:
            return problem(
                503,
                "Fotografía no disponible",
                "No fue posible leer la fotografía en este momento.",
            )

        from fastapi.responses import Response as RawResponse

        return RawResponse(
            content=content,
            media_type=photo["content_type"],
            # Privada: es de una persona concreta y solo la ve la dueña
            # de la solicitud, así que no se cachea en ningún sitio.
            headers={"Cache-Control": "no-store"},
        )

    @application.get(
        "/internal/v1/public/help-requests/{request_id}/photo",
        tags=["HelpRequests"],
    )
    async def serve_help_request_photo(
        request_id: UUID,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        photo = await data.get_help_request_photo(request_id)
        if photo is None:
            return problem(
                404,
                "Fotografía no disponible",
                "La solicitud no tiene fotografía o ya expiró.",
            )
        try:
            content = object_storage.load(photo["object_key"])
        except StorageUnavailableError:
            content = None
        if content is None:
            return problem(
                503,
                "Fotografía no disponible",
                "No fue posible leer la fotografía en este momento.",
            )

        from fastapi.responses import Response as RawResponse

        return RawResponse(
            content=content,
            media_type=photo["content_type"],
            # Pública y cacheable por poco tiempo: al expirar la
            # solicitud la copia vieja no debe sobrevivir mucho.
            headers={"Cache-Control": "public, max-age=300"},
        )

    # CHG-138 — Gestión de solicitudes de ayuda desde la consola: se ve
    # TODO lo que llega (activas y expiradas) y se borra una a una o se
    # vacía por completo. Borrado físico = decisión explícita del
    # operador (excepción deliberada a DEC-125-02); cada operación
    # queda en la auditoría y limpia las fotos del storage.

    @application.get(
        "/internal/v1/admin/help-requests",
        response_model=AdminHelpRequestPage,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_list_help_requests(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        rows, total = await data.admin_list_help_requests(limit, offset)
        return AdminHelpRequestPage(
            items=[AdminHelpRequest(**row) for row in rows],
            total=total,
            generated_at=datetime.now(UTC),
        )

    @application.delete(
        "/internal/v1/admin/help-requests/{request_id}",
        response_model=AdminHelpRequestDeleteReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_delete_help_request(
        request_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display = actor
        row = await data.admin_delete_help_request(request_id)
        if row is None:
            return problem(
                404,
                "Solicitud no encontrada",
                "La solicitud no existe o ya fue eliminada.",
            )
        for key in (
            row.get("photo_storage_key"),
            row.get("photo_derived_storage_key"),
        ):
            if key:
                object_storage.delete(key)
        await data.admin_write_audit(
            actor_account_id,
            actor_display,
            "help_request_deleted",
            "help_request",
            request_id,
            "success",
        )
        return AdminHelpRequestDeleteReceipt(deleted=1)

    @application.delete(
        "/internal/v1/admin/help-requests",
        response_model=AdminHelpRequestDeleteReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_purge_help_requests(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display = actor
        deleted, photo_keys = await data.admin_purge_help_requests()
        for key in photo_keys:
            object_storage.delete(key)
        await data.admin_write_audit(
            actor_account_id,
            actor_display,
            "help_requests_purged",
            "help_request",
            None,
            "success",
            changed_fields=[f"deleted:{deleted}"],
        )
        return AdminHelpRequestDeleteReceipt(deleted=deleted)

    # CHG-139 — Reinicio absoluto de los datos de emergencia: TRUNCATE
    # de todo el esquema (incluida la auditoría) + vaciado del
    # almacenamiento de fotos. El acto queda como PRIMER evento de la
    # auditoría nueva. Las cuentas las reinicia identity-service (el
    # gateway orquesta ambos).
    @application.post(
        "/internal/v1/admin/platform-reset",
        tags=["Administration"],
    )
    async def admin_platform_reset(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display = actor
        tables_cleared = await data.admin_reset_platform()
        object_storage.wipe()
        await data.admin_write_audit(
            actor_account_id,
            actor_display,
            "platform_reset",
            "platform",
            None,
            "success",
            changed_fields=[f"tables:{tables_cleared}"],
        )
        return {"tablesCleared": tables_cleared}

    # CHG-148 — Voluntarios anónimos de una solicitud, SOLO para el
    # super_admin: la PII se descifra aquí y nunca sale de la consola.
    @application.get(
        "/internal/v1/admin/help-requests/{request_id}/volunteers",
        response_model=AdminHelpRequestVolunteerPage,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_list_help_request_volunteers(
        request_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        rows = await data.admin_list_help_request_volunteers(request_id)
        items = [
            AdminHelpRequestVolunteer(
                id=row["id"],
                name=decrypt_text(row["name_encrypted"]) or "",
                phone=decrypt_text(row["phone_encrypted"]),
                email=decrypt_text(row["email_encrypted"]),
                has_photo=row["has_photo"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
        return AdminHelpRequestVolunteerPage(
            items=items,
            total=len(items),
            generated_at=datetime.now(UTC),
        )

    @application.get(
        "/internal/v1/admin/help-request-volunteers/{volunteer_id}/photo",
        tags=["Administration"],
    )
    async def admin_serve_volunteer_photo(
        volunteer_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        photo = await data.get_help_request_volunteer_photo(volunteer_id)
        if photo is None:
            return problem(
                404,
                "Fotografía no disponible",
                "El voluntario no adjuntó fotografía.",
            )
        try:
            content = object_storage.load(photo["object_key"])
        except StorageUnavailableError:
            content = None
        if content is None:
            return problem(
                503,
                "Fotografía no disponible",
                "No fue posible leer la fotografía en este momento.",
            )

        from fastapi.responses import Response as RawResponse

        return RawResponse(
            content=content,
            media_type=photo["content_type"],
            # Privada: nunca cachear la PII del voluntario.
            headers={"Cache-Control": "no-store"},
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

    # CHG-165 — Verificación y reactivación de Centros de Acopio Local
    # (solo super_admin). El estado operativo y el de verificación son
    # independientes (§25); nada se borra jamás (§14).
    def admin_aid_location_model(row: dict) -> AdminAidLocationSummary:
        return AdminAidLocationSummary(
            id=row["id"],
            kind=row["kind"],
            name=row["name"],
            location_label=row["location_label"],
            municipality=row["municipality"],
            department=row["department"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            description=row["description"],
            schedule=row["schedule"],
            contact=row["contact"],
            created_at=row["created_at"],
            created_by_account_id=row["created_by_account_id"],
            verification_status=row["verification_status"],
            operational_status=row["operational_status"],
            disabled_at=row["disabled_at"],
            verified_at=row["verified_at"],
            active_reports_count=row["active_reports_count"],
        )

    @application.get(
        "/internal/v1/admin/aid-locations/verifications",
        response_model=AdminAidLocationVerificationsResponse,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_aid_location_verifications(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        result = await data.admin_list_aid_location_verifications()
        return AdminAidLocationVerificationsResponse(
            pending=[
                admin_aid_location_model(row) for row in result["pending"]
            ],
            disabled=[
                admin_aid_location_model(row) for row in result["disabled"]
            ],
        )

    @application.post(
        "/internal/v1/admin/aid-locations/{location_id}/verification",
        response_model=AdminAidLocationActionReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_decide_aid_location_verification(
        location_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_id, actor_display = actor
        body = await request.body()
        try:
            payload = AdminAidLocationVerificationDecision.model_validate_json(
                body
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        row = await data.admin_decide_aid_location_verification(
            location_id=location_id,
            decision=payload.decision,
            actor_account_id=actor_id,
            actor_display_name=actor_display,
            reason_encrypted=encrypt(
                payload.reason.strip() if payload.reason else None
            ),
        )
        if row is None:
            return problem(404, "Centro no disponible", "El centro no existe.")
        return AdminAidLocationActionReceipt(
            id=row["id"],
            verification_status=row["verification_status"],
            operational_status=row["operational_status"],
            disabled_at=row["disabled_at"],
            active_reports_count=row["active_reports_count"],
        )

    @application.post(
        "/internal/v1/admin/aid-locations/{location_id}/reactivate",
        response_model=AdminAidLocationActionReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_reactivate_aid_location(
        location_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_id, actor_display = actor
        row = await data.admin_reactivate_aid_location(
            location_id=location_id,
            actor_account_id=actor_id,
            actor_display_name=actor_display,
            reason_encrypted=None,
        )
        if row is None:
            return problem(404, "Centro no disponible", "El centro no existe.")
        if row == "not_disabled":
            return problem(
                409,
                "Centro no deshabilitado",
                "Solo puede reactivarse un centro deshabilitado por "
                "denuncias.",
            )
        return AdminAidLocationActionReceipt(
            id=row["id"],
            verification_status=row["verification_status"],
            operational_status=row["operational_status"],
            disabled_at=row["disabled_at"],
            active_reports_count=row["active_reports_count"],
        )

    # CHG-167 — Borrado admin de un comentario de acopio local:
    # definitivo y auditado (patrón CHG-159); el promedio CHG-166 se
    # recalcula solo al leer.
    @application.delete(
        "/internal/v1/admin/aid-locations/{location_id}/comments/"
        "{comment_id}",
        response_model=AidLocationCommentDeleteReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_delete_aid_location_comment(
        location_id: UUID,
        comment_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display = actor
        deleted = await data.admin_delete_aid_location_comment(
            location_id=location_id,
            comment_id=comment_id,
            actor_account_id=actor_account_id,
            actor_display_name=actor_display,
        )
        if deleted == 0:
            return problem(
                404,
                "Comentario no encontrado",
                "El comentario no existe o ya fue borrado.",
            )
        return AidLocationCommentDeleteReceipt(deleted=deleted)

    # CHG-170 — Borrado admin del acopio completo (desde su ficha de
    # VER MÁS): definitivo y auditado; los dependientes quedan
    # desvinculados y las fotos de valoraciones salen del storage.
    @application.delete(
        "/internal/v1/admin/aid-locations/{location_id}",
        response_model=AdminAidLocationDeleteReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_delete_aid_location(
        location_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display = actor
        result = await data.admin_delete_aid_location(
            location_id=location_id,
            actor_account_id=actor_account_id,
            actor_display_name=actor_display,
        )
        if result is None:
            return problem(
                404,
                "Acopio no encontrado",
                "El centro de acopio no existe o ya fue eliminado.",
            )
        if result == "has_transports":
            return problem(
                409,
                "Acopio con transportes asociados",
                "El acopio tiene transportes humanitarios registrados "
                "(trazabilidad CHG-161) y no puede eliminarse mientras "
                "existan.",
            )
        for key in result["photo_keys"]:
            object_storage.delete(key)
        return AdminAidLocationDeleteReceipt(deleted=result["deleted"])

    # CHG-154 — Gestión admin de registros de personas: listar (con
    # ocultos), ocultar (reversible), restaurar y editar. Nada se
    # borra: el borrado definitivo será un apartado futuro.
    def admin_person_model(row: dict) -> AdminPersonRecord:
        return AdminPersonRecord(
            id=row["id"],
            display_name=row["display_name"],
            status=row["status"],
            location=row["location"],
            related_event=row["related_event"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            has_linked_case=row["missing_person_case_id"] is not None,
            source=SourceReference(
                name=row["source_name"],
                source_type=row["source_type"],
                url=row["source_url"],
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            hidden_at=row["hidden_at"],
            hidden_by=row["hidden_by"],
        )

    @application.get(
        "/internal/v1/admin/people",
        response_model=AdminPeoplePage,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_list_people(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
        statuses: Annotated[list[HumanStatus] | None, Query()] = None,
        q: Annotated[
            str | None, Query(min_length=2, max_length=100)
        ] = None,
        visibility: Annotated[AdminPeopleVisibility, Query()] = "visible",
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        rows, total = await data.admin_list_people(
            statuses, q, visibility, limit, offset
        )
        return AdminPeoplePage(
            items=[admin_person_model(row) for row in rows],
            total=total,
        )

    @application.patch(
        "/internal/v1/admin/people/{person_id}",
        response_model=AdminPersonRecord,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_update_person(
        person_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        body = await request.body()
        try:
            payload = AdminPersonUpdateInput.model_validate_json(body)
        except ValidationError as error:
            return invalid_fields_problem(error)
        fields = payload.model_dump(by_alias=False, exclude_none=True)
        if not fields:
            return problem(
                422,
                "Nada que modificar",
                "Envía al menos un campo a modificar.",
            )
        result = await data.admin_update_person(person_id, fields)
        if result is None:
            return problem(
                404,
                "Registro no disponible",
                "El registro de persona no existe.",
            )
        if result == "status_locked":
            return problem(
                409,
                "Estado gobernado por novedades",
                "Este registro tiene caso ciudadano vinculado: su "
                "estado lo derivan las novedades verificadas y no se "
                "edita a mano.",
            )
        return admin_person_model(result)

    @application.post(
        "/internal/v1/admin/people/{person_id}/hide",
        response_model=AdminPersonRecord,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_hide_person(
        person_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        _account_id, display_name = actor
        result = await data.admin_hide_person(person_id, display_name)
        if result is None:
            return problem(
                404,
                "Registro no disponible",
                "El registro de persona no existe.",
            )
        return admin_person_model(result)

    @application.post(
        "/internal/v1/admin/people/{person_id}/restore",
        response_model=AdminPersonRecord,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_restore_person(
        person_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        result = await data.admin_restore_person(person_id)
        if result is None:
            return problem(
                404,
                "Registro no disponible",
                "El registro de persona no existe.",
            )
        return admin_person_model(result)

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
        # CHG-159: filtro por tema (mapa tema→tipos en admin_rules).
        theme: Annotated[AdminSubmissionTheme | None, Query()] = None,
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
            q, kind, status, received_from, received_to, limit, offset,
            theme=theme,
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

    # CHG-105 — Retirada rápida de la fotografía pública: deja de
    # publicarse de inmediato sin tocar el expediente, que conserva el
    # original. Es la contraparte de publicar al crear.
    @application.delete(
        "/internal/v1/admin/missing-persons/{case_id}/public-photo",
        tags=["Administration"],
    )
    async def admin_withdraw_public_photo(
        case_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display = actor
        retirada = await data.withdraw_public_person_photo(
            case_id, actor_display
        )
        await data.admin_write_audit(
            actor_account_id,
            actor_display,
            "public_photo_withdrawn",
            "missing_person_case",
            case_id,
            "success" if retirada else "failed",
        )
        if not retirada:
            return problem(
                404,
                "Fotografía no disponible",
                "El caso no tiene una fotografía pública que retirar.",
            )
        return {"status": "withdrawn"}

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

    # CHG-159 — borrado definitivo: solo legal desde archived/rejected
    # (available_actions); la fila fuente desaparece con su evidencia.
    @application.delete(
        "/internal/v1/admin/submissions/{submission_id}/permanent",
        response_model=AdminSubmissionDeleteReceipt,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_delete_submission_permanently(
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
            "delete",
            payload.reason,
            "delete",
        )
        if isinstance(result, JSONResponse):
            return result
        audit_event_id, _version = result
        return AdminSubmissionDeleteReceipt(
            id=submission_id,
            audit_event_id=audit_event_id,
            deleted_at=datetime.now(UTC),
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

    # ------------------------------------------------------------------
    # CHG-208 — Monitoreo sísmico y red privada de emergencia.
    # ------------------------------------------------------------------

    def _seismic_zone_views(
        zone_rows: list[dict],
    ) -> dict[UUID, list[SeismicZoneView]]:
        by_event: dict[UUID, list[SeismicZoneView]] = {}
        for row in zone_rows:
            title, description = seismic_rules.ZONE_TEXTS[
                row["severity_level"]
            ]
            by_event.setdefault(row["seismic_event_id"], []).append(
                SeismicZoneView(
                    id=row["id"],
                    severity_level=row["severity_level"],
                    source=row["source"],
                    title=title,
                    description=description,
                    geometry=_json.loads(row["geometry_geojson"]),
                )
            )
        return by_event

    def _seismic_event_view(
        event: dict, zones: list[SeismicZoneView]
    ) -> SeismicEventView:
        preliminary = (
            event["processing_status"] == "SEISMIC_DATA_PRELIMINARY"
        )
        # CHG-218: los círculos se retiran a las N horas del origen; el
        # evento puede seguir vivo (alertas activas) sin ellos.
        zones_expired = event["origin_time_utc"] <= datetime.now(
            UTC
        ) - timedelta(hours=resolved_settings.seismic_visibility_hours)
        return SeismicEventView(
            id=event["id"],
            source=event["source"],
            source_event_id=event["source_event_id"],
            magnitude=event["magnitude"],
            depth_km=event.get("depth_km"),
            latitude=event["latitude"],
            longitude=event["longitude"],
            origin_time_utc=event["origin_time_utc"],
            processing_status=event["processing_status"],
            is_simulated=event["is_simulated"],
            simulated_banner=(
                seismic_rules.SIMULATED_BANNER
                if event["is_simulated"]
                else None
            ),
            description=event.get("description"),
            pending_instrumental_notice=(
                seismic_rules.PENDING_INSTRUMENTAL_NOTICE
                if preliminary and not event["is_simulated"]
                else None
            ),
            zones=[] if zones_expired else zones,
            zones_expired=zones_expired,
        )

    def resolve_super_admin(
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
                "Esta operación exige una cuenta autenticada.",
            )
        role = request.headers.get("x-actor-role", "").strip()
        if role != "super_admin":
            return problem(
                403,
                "Permiso insuficiente",
                "Solo el Super Administrador puede operar el "
                "generador sísmico.",
            )
        return account_id

    def require_account(
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
                "Esta operación exige una cuenta autenticada "
                "resuelta por el gateway.",
            )
        return account_id

    @application.get(
        "/internal/v1/seismic/events",
        response_model=SeismicEventsResponse,
        response_model_by_alias=True,
        tags=["Seismic"],
    )
    async def list_seismic_events(
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        events = await data.list_public_seismic_events(
            resolved_settings.seismic_visibility_hours, 20
        )
        zones = await data.list_intensity_zones_for_events(
            [event["id"] for event in events]
        )
        zone_views = _seismic_zone_views(zones)
        return SeismicEventsResponse(
            events=[
                _seismic_event_view(
                    event, zone_views.get(event["id"], [])
                )
                for event in events
            ],
            generated_at=datetime.now(UTC),
        )

    @application.get(
        "/internal/v1/seismic/events/{event_id}/affected",
        response_model=SeismicAffectedResponse,
        response_model_by_alias=True,
        tags=["Seismic"],
    )
    async def list_seismic_affected(
        event_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        """Spec §41-45 y §51: el backend filtra la autorización. El
        público recibe triángulos ANÓNIMOS con coordenada redondeada;
        solo el contacto ACEPTADO recibe nombre y alertId — y la
        coordenada exacta únicamente en el panel auditado."""
        event = await data.get_seismic_event(event_id)
        if event is None or event.get("deactivated_at") is not None:
            return problem(
                404,
                "Evento no disponible",
                "El evento sísmico no existe o fue retirado.",
            )
        actor = resolve_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        _actor_kind, viewer_account_id = actor
        alerts = await data.list_alerts_for_event(event_id)
        identified: dict[UUID, dict] = {}
        if viewer_account_id is not None:
            authorized = await data.accepted_owner_alerts_for_viewer(
                event_id, viewer_account_id
            )
            identified = {row["id"]: row for row in authorized}
        markers: list[SeismicAffectedMarker] = []
        for alert in alerts:
            latitude, longitude = seismic_rules.anonymize_coordinate(
                alert["event_latitude"], alert["event_longitude"]
            )
            marker = SeismicAffectedMarker(
                latitude=latitude,
                longitude=longitude,
                severity_level=alert["severity_level"],
                status=alert["status"],
            )
            if (
                viewer_account_id is not None
                and alert["account_id"] == viewer_account_id
            ):
                marker.is_self = True
                marker.alert_id = alert["id"]
            elif alert["id"] in identified:
                row = identified[alert["id"]]
                marker.identified = True
                marker.display_name = row["display_name"]
                marker.alert_id = alert["id"]
                # Spec §79: la identificación en el mapa ya es acceso
                # a información sensible; queda auditada.
                await data.log_emergency_access(
                    viewer_account_id,
                    alert["account_id"],
                    event_id,
                    alert["id"],
                    "MARKER",
                )
            markers.append(marker)
        return SeismicAffectedResponse(
            event_id=event_id,
            markers=markers,
            generated_at=datetime.now(UTC),
        )

    @application.get(
        "/internal/v1/seismic/settings",
        response_model=SeismicSettingsView,
        response_model_by_alias=True,
        tags=["Seismic"],
    )
    async def get_seismic_settings(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = require_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        settings_row = await data.get_seismic_settings(account_id)
        return SeismicSettingsView(
            enabled=bool(settings_row and settings_row["enabled"]),
            max_contacts=seismic_rules.MAX_EMERGENCY_CONTACTS,
        )

    @application.put(
        "/internal/v1/seismic/settings",
        response_model=SeismicSettingsView,
        response_model_by_alias=True,
        tags=["Seismic"],
    )
    async def update_seismic_settings(
        request: Request,
        payload: SeismicSettingsUpdate,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = require_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        row = await data.upsert_seismic_settings(
            account_id, payload.enabled, payload.display_name
        )
        if not payload.enabled:
            # Spec §84: apagar detiene alertas y compartición futura
            # en el acto; el histórico queda para auditoría.
            await data.expire_my_active_alerts(account_id)
        return SeismicSettingsView(
            enabled=row["enabled"],
            max_contacts=seismic_rules.MAX_EMERGENCY_CONTACTS,
        )

    def _contact_view(row: dict) -> EmergencyContactView:
        display_name = row.get("contact_display_name")
        # CHG-215: la vía directa por ID no tiene candidato; el nombre
        # lo fijó el gateway desde la cuenta al vincular.
        if not display_name:
            display_name = row.get("direct_display_name")
        if not display_name:
            first = row.get("candidate_first_names") or ""
            last = row.get("candidate_last_names") or ""
            display_name = f"{first} {last}".strip() or "Contacto"
        return EmergencyContactView(
            id=row["id"],
            status=row["status"],
            display_name=display_name,
            linked=row.get("contact_account_id") is not None,
            created_at=row["created_at"],
        )

    @application.get(
        "/internal/v1/seismic/contacts",
        response_model=EmergencyContactsResponse,
        response_model_by_alias=True,
        tags=["Seismic"],
    )
    async def list_emergency_contacts(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = require_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        rows = await data.list_emergency_contacts_of_owner(account_id)
        return EmergencyContactsResponse(
            contacts=[_contact_view(row) for row in rows],
            max_contacts=seismic_rules.MAX_EMERGENCY_CONTACTS,
        )

    @application.post(
        "/internal/v1/seismic/contacts",
        response_model=EmergencyContactView,
        response_model_by_alias=True,
        status_code=201,
        tags=["Seismic"],
    )
    async def create_emergency_contact(
        request: Request,
        payload: EmergencyContactInput,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = require_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        settings_row = await data.get_seismic_settings(account_id)
        if not (settings_row and settings_row["enabled"]):
            return problem(
                409,
                "Servicio desactivado",
                "Activa «Alertas sísmicas y red de emergencia» antes "
                "de registrar contactos.",
            )
        candidate = await data.create_emergency_candidate(
            created_by_account_id=account_id,
            first_names=payload.first_names.strip(),
            last_names=payload.last_names.strip(),
            document_type=payload.document_type,
            document_encrypted=encrypt(payload.document_number),
            document_hash=seismic_rules.document_hash(
                payload.document_number
            ),
            phone_normalized=seismic_rules.normalize_phone(
                payload.phone
            ),
            name_normalized=seismic_rules.normalize_name(
                f"{payload.first_names} {payload.last_names}"
            ),
        )
        result = await data.create_emergency_contact(
            account_id, None, candidate["id"]
        )
        if result == "limit":
            return problem(
                422,
                "Límite de contactos",
                "La red de emergencia admite máximo "
                f"{seismic_rules.MAX_EMERGENCY_CONTACTS} contactos.",
            )
        if result == "duplicate":
            return problem(
                409,
                "Contacto duplicado",
                "Esa persona ya está en tu red de emergencia.",
            )
        return _contact_view(
            {
                **result,
                "candidate_first_names": candidate["first_names"],
                "candidate_last_names": candidate["last_names"],
                "contact_display_name": None,
            }
        )

    # CHG-215 — Vínculo directo por ID compartible: sin candidato ni
    # matching; la invitación nace PENDING apuntando a la cuenta y le
    # aparece a esa persona sin teclear su documento.
    @application.post(
        "/internal/v1/seismic/contacts/direct",
        response_model=EmergencyContactView,
        response_model_by_alias=True,
        status_code=201,
        tags=["Seismic"],
    )
    async def create_emergency_contact_direct(
        request: Request,
        payload: EmergencyContactDirectInput,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = require_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        settings_row = await data.get_seismic_settings(account_id)
        if not (settings_row and settings_row["enabled"]):
            return problem(
                409,
                "Servicio desactivado",
                "Activa «Alertas sísmicas y red de emergencia» antes "
                "de registrar contactos.",
            )
        result = await data.create_emergency_contact(
            account_id,
            payload.contact_account_id,
            None,
            direct_display_name=payload.display_name.strip(),
        )
        if result == "limit":
            return problem(
                422,
                "Límite de contactos",
                "La red de emergencia admite máximo "
                f"{seismic_rules.MAX_EMERGENCY_CONTACTS} contactos.",
            )
        if result == "duplicate":
            return problem(
                409,
                "Contacto duplicado",
                "Esa persona ya está en tu red de emergencia.",
            )
        if result == "self":
            return problem(
                422,
                "ID propio",
                "Ese es tu propio ID: elige a otra persona.",
            )
        return _contact_view(
            {
                **result,
                "direct_display_name": payload.display_name.strip(),
                "contact_display_name": None,
            }
        )

    @application.delete(
        "/internal/v1/seismic/contacts/{contact_id}",
        status_code=204,
        tags=["Seismic"],
    )
    async def revoke_emergency_contact(
        contact_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = require_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        revoked = await data.revoke_emergency_contact(
            contact_id, account_id
        )
        if not revoked:
            return problem(
                404,
                "Contacto no disponible",
                "El contacto no existe, no es tuyo o ya estaba "
                "revocado.",
            )
        return JSONResponse(status_code=204, content=None)

    @application.post(
        "/internal/v1/seismic/invitations/match",
        response_model=EmergencyInvitationsResponse,
        response_model_by_alias=True,
        tags=["Seismic"],
    )
    async def match_emergency_invitations(
        request: Request,
        payload: EmergencyInvitationMatchInput,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        """Spec §29-§32: coincidencias SOLO desde invitaciones creadas
        para esa identidad, reforzadas por documento o por teléfono con
        nombre coherente. Jamás un directorio por nombre (spec §74)."""
        account_id = require_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        claimed_document = payload.document_number
        claimed_phone = payload.phone
        document_hashed = (
            seismic_rules.document_hash(claimed_document)
            if claimed_document
            else None
        )
        phone_normalized = (
            seismic_rules.normalize_phone(claimed_phone)
            if claimed_phone
            else None
        )
        strengths: dict[UUID, str] = {}
        candidates = await data.match_unregistered_candidates(
            document_hashed, phone_normalized
        )
        for candidate in candidates:
            strength = seismic_rules.match_strength(
                candidate_document_hash=candidate["document_hash"],
                candidate_phone_normalized=candidate[
                    "phone_normalized"
                ],
                candidate_name_normalized=candidate["name_normalized"],
                claimed_document=claimed_document,
                claimed_phone=claimed_phone,
                claimed_full_name=payload.display_name,
            )
            if strength is None:
                continue
            linked = await data.link_candidate_to_account(
                candidate["id"], account_id
            )
            if linked is not None:
                strengths[candidate["id"]] = strength
        invitations = await data.list_emergency_invitations_for(
            account_id
        )
        return EmergencyInvitationsResponse(
            invitations=[
                EmergencyInvitationView(
                    id=row["id"],
                    owner_display_name=(
                        row.get("owner_display_name")
                        or "Una persona registrada"
                    ),
                    created_at=row["created_at"],
                    match_strength=strengths.get(
                        row.get("candidate_id")
                    ),
                )
                for row in invitations
            ]
        )

    @application.post(
        "/internal/v1/seismic/invitations/{contact_id}/respond",
        response_model=EmergencyContactView,
        response_model_by_alias=True,
        tags=["Seismic"],
    )
    async def respond_emergency_invitation(
        contact_id: UUID,
        request: Request,
        payload: EmergencyInvitationRespondInput,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        """Spec §33: el vínculo solo queda activo tras ACEPTAR."""
        account_id = require_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        row = await data.respond_emergency_contact(
            contact_id, account_id, payload.accept
        )
        if row is None:
            return problem(
                404,
                "Invitación no disponible",
                "La invitación no existe, no es para ti o ya fue "
                "respondida.",
            )
        return EmergencyContactView(
            id=row["id"],
            status=row["status"],
            display_name=payload.display_name,
            linked=True,
            created_at=datetime.now(UTC),
        )

    @application.get(
        "/internal/v1/seismic/alerts/mine",
        response_model=MySeismicAlertsResponse,
        response_model_by_alias=True,
        tags=["Seismic"],
    )
    async def list_my_seismic_alerts(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        account_id = require_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        rows = await data.list_my_seismic_alerts(account_id)
        return MySeismicAlertsResponse(
            alerts=[
                MySeismicAlertView(
                    id=row["id"],
                    event_id=row["seismic_event_id"],
                    status=row["status"],
                    severity_level=row["severity_level"],
                    magnitude=row["magnitude"],
                    requires_confirmation=(
                        row["status"] == "ACTIVE"
                        and seismic_rules.requires_confirmation(
                            row["magnitude"]
                        )
                    ),
                    is_simulated=row["is_simulated"],
                    created_at=row["created_at"],
                )
                for row in rows[:20]
            ]
        )

    @application.post(
        "/internal/v1/seismic/alerts/mine/confirm-safe",
        response_model=ConfirmSafeReceipt,
        response_model_by_alias=True,
        tags=["Seismic"],
    )
    async def confirm_safe(
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        """«ESTOY BIEN» (spec §54/§59): ACTIVE → SAFE_CONFIRMED y sus
        contactos aceptados reciben la confirmación."""
        account_id = require_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        raw_body = await request.body()
        try:
            payload = (
                ConfirmSafeInput.model_validate_json(raw_body)
                if raw_body
                else ConfirmSafeInput()
            )
        except ValidationError as error:
            return invalid_fields_problem(error)
        confirmed = await data.confirm_safe(
            account_id, payload.event_id
        )
        now = datetime.now(UTC)
        if confirmed:
            settings_row = await data.get_seismic_settings(account_id)
            display_name = (
                settings_row.get("display_name")
                if settings_row
                else None
            ) or "Una persona"
            hora_local = confirmed[0]["safe_confirmed_at"].astimezone(
                timezone(timedelta(hours=-5))
            ).strftime("%H:%M")
            title, body = seismic_rules.safe_notification_texts(
                display_name, hora_local
            )
            for alert in confirmed:
                await seismic_ingest._notify_alert_recipients(
                    data,
                    report_notifier,
                    alert_id=alert["id"],
                    owner_account_id=account_id,
                    kind="SAFE_CONFIRMED",
                    title=title,
                    body=body,
                    tracking_code=str(alert["seismic_event_id"]),
                )
        return ConfirmSafeReceipt(
            confirmed=len(confirmed), confirmed_at=now
        )

    @application.get(
        "/internal/v1/seismic/alerts/{alert_id}",
        response_model=EmergencyPanelView,
        response_model_by_alias=True,
        tags=["Seismic"],
    )
    async def get_emergency_panel(
        alert_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        """Panel privado (spec §46-§50): solo contactos ACEPTADOS; una
        alerta ajena responde lo mismo que una inexistente. Cada
        apertura queda en la auditoría de accesos. El documento de
        identidad JAMÁS se incluye (spec §49)."""
        account_id = require_account(request)
        if isinstance(account_id, JSONResponse):
            return account_id
        row = await data.get_alert_for_authorized_viewer(
            alert_id, account_id
        )
        if row is None:
            return problem(
                404,
                "Alerta no disponible",
                "La alerta no existe o no estás autorizado para "
                "consultarla.",
            )
        await data.log_emergency_access(
            account_id,
            row["account_id"],
            row["seismic_event_id"],
            alert_id,
            "PANEL",
        )
        return EmergencyPanelView(
            alert_id=row["id"],
            display_name=row["display_name"] or "Una persona",
            status=row["status"],
            magnitude=row["magnitude"],
            origin_time_utc=row["origin_time_utc"],
            severity_level=row["severity_level"],
            zone_title=seismic_rules.ZONE_TEXTS[
                row["severity_level"]
            ][0],
            latitude=row["event_latitude"],
            longitude=row["event_longitude"],
            accuracy_meters=row.get("event_location_accuracy"),
            altitude_meters=row.get("event_altitude"),
            altitude_accuracy_meters=row.get("event_altitude_accuracy"),
            located_at=row.get("event_location_timestamp"),
            resolved_address=row.get("resolved_address"),
            alert_created_at=row["created_at"],
            safe_confirmed_at=row.get("safe_confirmed_at"),
            is_simulated=row["is_simulated"],
        )

    @application.post(
        "/internal/v1/admin/seismic/simulations",
        response_model=SeismicSimulationReceipt,
        response_model_by_alias=True,
        status_code=201,
        tags=["Seismic"],
    )
    async def create_seismic_simulation(
        request: Request,
        payload: SeismicSimulationInput,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        """Generador del Super Admin (spec §62-§67): epicentro elegido
        en el mapa, zonas automáticas o polígonos manuales irregulares,
        `is_simulated` siempre, y por defecto SOLO cuentas de prueba."""
        admin_id = resolve_super_admin(request)
        if isinstance(admin_id, JSONResponse):
            return admin_id
        manual_zones: list[dict] = []
        if payload.zones:
            for zone in payload.zones:
                reason = seismic_rules.validate_manual_zone_geometry(
                    zone.geometry
                )
                if reason is not None:
                    return problem(422, "Zona inválida", reason)
                manual_zones.append(
                    {
                        "source": "SIMULATED",
                        "severity_level": zone.severity_level,
                        "geometry_geojson": _json.dumps(zone.geometry),
                    }
                )
        now = datetime.now(UTC)
        event = await data.insert_seismic_event(
            source="SIMULATED",
            source_event_id=seismic_rules.simulation_event_id(
                now, secrets.token_hex(4)
            ),
            origin_time_utc=now,
            magnitude=payload.magnitude,
            depth_km=payload.depth_km,
            latitude=payload.latitude,
            longitude=payload.longitude,
            description=payload.description,
            is_simulated=True,
            simulated_by_account_id=admin_id,
            notify_real_users=payload.notify_real_users,
        )
        if manual_zones:
            zones_created = len(
                await data.replace_intensity_zones(
                    event["id"], manual_zones, supersede=False
                )
            )
        else:
            auto = seismic_rules.provisional_zones_geojson(
                magnitude=payload.magnitude,
                depth_km=payload.depth_km,
                latitude=payload.latitude,
                longitude=payload.longitude,
            )
            zones_created = len(
                await data.replace_intensity_zones(
                    event["id"],
                    [
                        {
                            "source": "SIMULATED",
                            "severity_level": severity,
                            "geometry_geojson": _json.dumps(geometry),
                        }
                        for severity, geometry in auto
                    ],
                    supersede=False,
                )
            )
        created = await seismic_ingest.activate_alerts_for_event(
            data, report_notifier, event
        )
        return SeismicSimulationReceipt(
            event_id=event["id"],
            source_event_id=event["source_event_id"],
            zones_created=zones_created,
            alerts_activated=len(created),
            banner=seismic_rules.SIMULATED_BANNER,
        )

    @application.delete(
        "/internal/v1/admin/seismic/simulations/{event_id}",
        status_code=204,
        tags=["Seismic"],
    )
    async def delete_seismic_simulation(
        event_id: UUID,
        request: Request,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        admin_id = resolve_super_admin(request)
        if isinstance(admin_id, JSONResponse):
            return admin_id
        removed = await data.deactivate_simulation(event_id)
        if not removed:
            return problem(
                404,
                "Simulacro no disponible",
                "El evento no existe, no es un simulacro o ya fue "
                "retirado.",
            )
        return JSONResponse(status_code=204, content=None)

    @application.post(
        "/internal/v1/admin/seismic/test-accounts",
        response_model=SeismicTestAccountsReceipt,
        response_model_by_alias=True,
        tags=["Seismic"],
    )
    async def configure_seismic_test_accounts(
        request: Request,
        payload: SeismicTestAccountsInput,
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        """Spec §68-§70: cuentas de prueba con ubicación y relaciones
        cruzadas para verificar privacidad de punta a punta."""
        admin_id = resolve_super_admin(request)
        if isinstance(admin_id, JSONResponse):
            return admin_id
        for account in payload.accounts:
            await data.upsert_seismic_settings(
                account.account_id,
                True,
                account.display_name,
                is_test_account=True,
            )
            await data.upsert_visitor_presence(
                uuid4(),
                account.account_id,
                account.latitude,
                account.longitude,
                10.0,
                "web",
            )
        relations_created = 0
        for relation in payload.relations:
            result = await data.create_emergency_contact(
                relation.owner_account_id,
                relation.contact_account_id,
                None,
            )
            if isinstance(result, dict):
                accepted = await data.respond_emergency_contact(
                    result["id"],
                    relation.contact_account_id,
                    True,
                )
                if accepted is not None:
                    relations_created += 1
        return SeismicTestAccountsReceipt(
            accounts_configured=len(payload.accounts),
            relations_created=relations_created,
        )

    return application


app = create_app()
