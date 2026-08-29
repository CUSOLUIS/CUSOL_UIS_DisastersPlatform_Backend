"""Monitoreo sísmico y red privada de emergencia (CHG-208).

Reglas puras del subsistema de notificación sísmica rápida POSTERIOR a
la detección: normalización del catálogo del SGC, idempotencia frente a
revisiones, clasificación de severidad, persistencia del marcador por
magnitud, estimación provisional de zonas (etiquetada como tal, jamás
presentada como instrumental), matching seguro de identidad para la red
de contactos y textos prudentes de interfaz y notificación.

La capa HTTP, la persistencia y las pruebas comparten esta única
fuente. Aquí no hay red, ni base de datos, ni secretos.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol


SeverityLevel = Literal["STRONG", "MODERATE", "LIGHT"]
AlertStatus = Literal["ACTIVE", "SAFE_CONFIRMED", "EXPIRED"]
ContactStatus = Literal["PENDING", "ACCEPTED", "REJECTED", "REVOKED"]

# Orden operativo: si una ubicación cae en varias zonas, manda la más
# severa (spec §40).
SEVERITY_ORDER: tuple[SeverityLevel, ...] = ("STRONG", "MODERATE", "LIGHT")

# Máximo de contactos de emergencia por usuario (spec §26 pedía 5;
# CHG-213 lo baja a 3 por decisión del usuario).
MAX_EMERGENCY_CONTACTS = 3

# Banda permanente de los simulacros (spec §66).
SIMULATED_BANNER = "🧪 EVENTO SIMULADO — NO ES UN SISMO REAL"

# Textos de zona (spec §16-18): siempre «estimada», nunca afirmaciones
# de daños. La interfaz los muestra tal cual.
ZONE_TEXTS: dict[SeverityLevel, tuple[str, str]] = {
    "STRONG": (
        "Sacudida fuerte estimada",
        "Pueden existir afectaciones. Verifique su entorno y siga "
        "las instrucciones oficiales.",
    ),
    "MODERATE": (
        "Sacudida moderada estimada",
        "Puede haber movimiento claramente perceptible.",
    ),
    "LIGHT": (
        "Sacudida leve estimada",
        "Movimiento potencialmente perceptible con menor severidad "
        "instrumental.",
    ),
}

# Aviso mientras no exista producto instrumental (spec §22).
PENDING_INSTRUMENTAL_NOTICE = "Estimación instrumental pendiente del SGC"


# ---------------------------------------------------------------------------
# Normalización de identidad (spec §30): tildes, mayúsculas, espacios,
# prefijo telefónico y tipo documental no deben romper una coincidencia.
# ---------------------------------------------------------------------------

def normalize_name(value: str) -> str:
    """«Julián  Villamizar» → «julian villamizar»."""
    sin_tildes = "".join(
        ch
        for ch in unicodedata.normalize("NFD", value)
        if unicodedata.category(ch) != "Mn"
    )
    limpio = re.sub(r"[^a-z0-9ñ ]", " ", sin_tildes.lower())
    return re.sub(r"\s+", " ", limpio).strip()


def normalize_phone(value: str) -> str:
    """Solo dígitos; el prefijo colombiano (+57/57) se descarta para
    que «+57 300 123 4567» y «3001234567» sean el mismo número."""
    digits = re.sub(r"\D", "", value)
    if len(digits) > 10 and digits.startswith("57"):
        digits = digits[2:]
    return digits.lstrip("0")


def normalize_document(value: str) -> str:
    """Alfanumérico en mayúsculas, sin separadores."""
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def document_hash(value: str) -> str:
    """SHA-256 del documento normalizado: casa sin descifrar."""
    normalized = normalize_document(value)
    return hashlib.sha256(normalized.encode()).hexdigest()


def name_coherent(candidate_name_normalized: str, full_name: str) -> bool:
    """Coherencia mínima para reforzar un match por teléfono: al menos
    dos palabras compartidas (o todas las del más corto). Un match por
    documento idéntico no la necesita, pero la registra."""
    a = set(candidate_name_normalized.split())
    b = set(normalize_name(full_name).split())
    if not a or not b:
        return False
    shared = len(a & b)
    return shared >= min(2, len(a), len(b))


MatchStrength = Literal["document", "phone"]


def match_strength(
    *,
    candidate_document_hash: str,
    candidate_phone_normalized: str,
    candidate_name_normalized: str,
    claimed_document: str | None,
    claimed_phone: str | None,
    claimed_full_name: str,
) -> MatchStrength | None:
    """Spec §31: jamás vincular solo por nombre. Documento idéntico
    manda; teléfono idéntico exige coherencia de nombre. En cualquier
    otro caso, no hay coincidencia (y no se sugiere nada: sin
    directorio público, spec §74)."""
    if claimed_document:
        if document_hash(claimed_document) == candidate_document_hash:
            return "document"
    if claimed_phone:
        if (
            normalize_phone(claimed_phone) == candidate_phone_normalized
            and candidate_phone_normalized != ""
            and name_coherent(candidate_name_normalized, claimed_full_name)
        ):
            return "phone"
    return None


# ---------------------------------------------------------------------------
# Persistencia del marcador (spec §52-53): reglas de producto, no
# clasificación científica. M = 4.5 exacto pertenece al tramo superior.
# ---------------------------------------------------------------------------

def alert_expiry(magnitude: float, now: datetime) -> datetime | None:
    """None = persiste hasta confirmación «ESTOY BIEN»."""
    if magnitude >= 4.5:
        return None
    if magnitude >= 3.0:
        return now + timedelta(minutes=10)
    return now + timedelta(minutes=3)


def requires_confirmation(magnitude: float) -> bool:
    return magnitude >= 4.5


# CHG-225 — Los círculos del mapa viven lo mismo que la alerta del
# triángulo: M < 3.0 → 3 min; 3.0 ≤ M < 4.5 → 10 min; M ≥ 4.5 → no
# caducan por sí solos y solo los retira el tope general de visibilidad
# del evento (CHG-218, 24 h por defecto). Antes TODOS duraban 24 h.
def zones_expiry(
    magnitude: float, origin_time: datetime, visibility_hours: int
) -> datetime:
    """Instante a partir del cual el evento viaja sin zonas."""
    ceiling = origin_time + timedelta(hours=visibility_hours)
    by_intensity = alert_expiry(magnitude, origin_time)
    if by_intensity is None:
        return ceiling
    return min(by_intensity, ceiling)


def zones_expired(
    magnitude: float,
    origin_time: datetime,
    visibility_hours: int,
    now: datetime,
) -> bool:
    return now >= zones_expiry(magnitude, origin_time, visibility_hours)


# ---------------------------------------------------------------------------
# Clasificación de severidad (spec §16-19): las tres capas se derivan
# de INTENSIDAD (cómo se sintió en cada lugar), nunca solo de la
# magnitud. Umbrales del modelo operativo sobre intensidad instrumental
# tipo MMI: ajustables cuando el adaptador del SGC entregue producto.
# ---------------------------------------------------------------------------

INTENSITY_STRONG_MIN = 6.0
INTENSITY_MODERATE_MIN = 4.5
INTENSITY_LIGHT_MIN = 3.0


def classify_intensity(intensity: float) -> SeverityLevel | None:
    if intensity >= INTENSITY_STRONG_MIN:
        return "STRONG"
    if intensity >= INTENSITY_MODERATE_MIN:
        return "MODERATE"
    if intensity >= INTENSITY_LIGHT_MIN:
        return "LIGHT"
    return None


# ---------------------------------------------------------------------------
# Estimación provisional de zonas (spec §22): mientras el SGC no
# publique intensidad instrumental, un modelo propio SIMPLE preselecciona
# usuarios potencialmente afectados. Se etiqueta PROVISIONAL_ESTIMATE y
# jamás se presenta como dato oficial. Cuando llegue la grilla
# instrumental, estas zonas se reemplazan (spec §23) conservando ambas.
# ---------------------------------------------------------------------------

# Atenuación simplificada: radios crecen con la magnitud y se reducen
# con la profundidad. Es una preselección prudente, no sismología.
_DEPTH_REFERENCE_KM = 300.0


def provisional_zone_radii_km(
    magnitude: float, depth_km: float | None
) -> dict[SeverityLevel, float]:
    """Radios (km) por severidad; vacío si el sismo es demasiado
    pequeño para percibirse."""
    factor = 1.0
    if depth_km is not None and depth_km > 0:
        factor = max(0.4, 1.0 - (depth_km / _DEPTH_REFERENCE_KM))
    radii: dict[SeverityLevel, float] = {}
    if magnitude >= 4.5:
        radii["STRONG"] = round((4 + 8 * (magnitude - 4.5)) * factor, 1)
    if magnitude >= 3.5:
        radii["MODERATE"] = round((6 + 18 * (magnitude - 3.5)) * factor, 1)
    if magnitude >= 2.5:
        radii["LIGHT"] = round((8 + 30 * (magnitude - 2.5)) * factor, 1)
    return radii


_EARTH_RADIUS_KM = 6371.0


def circle_polygon(
    latitude: float,
    longitude: float,
    radius_km: float,
    points: int = 48,
) -> list[list[float]]:
    """Anillo GeoJSON [lon, lat] aproximando un círculo geodésico. Es
    la forma del POLÍGONO provisional, no una afirmación de que la
    sacudida sea circular (spec §13)."""
    ring: list[list[float]] = []
    lat_rad = math.radians(latitude)
    for i in range(points):
        theta = 2 * math.pi * i / points
        dlat = (radius_km / _EARTH_RADIUS_KM) * math.cos(theta)
        dlon = (
            (radius_km / _EARTH_RADIUS_KM)
            * math.sin(theta)
            / max(0.01, math.cos(lat_rad))
        )
        ring.append(
            [
                round(longitude + math.degrees(dlon), 6),
                round(latitude + math.degrees(dlat), 6),
            ]
        )
    ring.append(ring[0])
    return ring


def provisional_zones_geojson(
    *,
    magnitude: float,
    depth_km: float | None,
    latitude: float,
    longitude: float,
) -> list[tuple[SeverityLevel, dict[str, Any]]]:
    """Zonas provisionales como MultiPolygon GeoJSON, de mayor a menor
    severidad."""
    radii = provisional_zone_radii_km(magnitude, depth_km)
    zones: list[tuple[SeverityLevel, dict[str, Any]]] = []
    for severity in SEVERITY_ORDER:
        radius = radii.get(severity)
        if radius is None or radius <= 0:
            continue
        zones.append(
            (
                severity,
                {
                    "type": "MultiPolygon",
                    "coordinates": [
                        [circle_polygon(latitude, longitude, radius)]
                    ],
                },
            )
        )
    return zones


# ---------------------------------------------------------------------------
# Anonimización del marcador público (spec §41-42): todos ven que HAY
# una persona potencialmente afectada; nadie sin autorización obtiene
# su coordenada exacta. Redondeo a ~1.1 km.
# ---------------------------------------------------------------------------

def anonymize_coordinate(
    latitude: float, longitude: float
) -> tuple[float, float]:
    return round(latitude, 2), round(longitude, 2)


# ---------------------------------------------------------------------------
# Ingesta del catálogo del SGC (spec §5-8).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NormalizedSgcEvent:
    source_event_id: str
    magnitude: float
    depth_km: float | None
    latitude: float
    longitude: float
    origin_time_utc: datetime
    location_solution_id: str | None
    magnitude_solution_id: str | None
    municipality_code: str | None
    department_code: str | None
    magnitude_source: str | None
    location_source: str | None
    payload: dict[str, Any]
    # CHG-222: lugar en palabras tal como lo publica el SGC («Istmina -
    # Chocó, Colombia»); None cuando la fuente no lo trae.
    description: str | None = None


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_sgc_feature(
    attributes: dict[str, Any],
) -> NormalizedSgcEvent | None:
    """Normaliza una fila del FeatureServer del catálogo de sismos.
    Devuelve None ante filas incompletas (se registran y se saltan;
    jamás tumban el ciclo)."""
    event_id = _text_or_none(attributes.get("ESP_ID_EVENTO_TXT"))
    raw_magnitude = attributes.get("ESP_MAGNITUD")
    raw_lat = attributes.get("ESP_LATITUD")
    raw_lon = attributes.get("ESP_LONGITUD")
    raw_date = attributes.get("ESP_FECHA")
    if event_id is None or raw_magnitude is None:
        return None
    if raw_lat is None or raw_lon is None or raw_date is None:
        return None
    try:
        magnitude = float(raw_magnitude)
        latitude = float(raw_lat)
        longitude = float(raw_lon)
        # ArcGIS entrega épocas en milisegundos.
        origin = datetime.fromtimestamp(float(raw_date) / 1000.0, tz=UTC)
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    if not (-2 <= magnitude <= 10):
        return None
    depth: float | None
    try:
        depth = float(attributes["ESP_PROFUNDIDAD"])
        if depth < 0 or depth > 800:
            depth = None
    except (KeyError, TypeError, ValueError):
        depth = None
    return NormalizedSgcEvent(
        source_event_id=event_id,
        magnitude=magnitude,
        depth_km=depth,
        latitude=latitude,
        longitude=longitude,
        origin_time_utc=origin,
        location_solution_id=_text_or_none(
            attributes.get("ESP_ID_SOL_LOCALIZACION")
        ),
        magnitude_solution_id=_text_or_none(
            attributes.get("ESP_ID_SOL_MAGNITUD")
        ),
        municipality_code=_text_or_none(attributes.get("MUN_CODIGO")),
        department_code=_text_or_none(attributes.get("DEPT_CODIGO")),
        magnitude_source=_text_or_none(
            attributes.get("ESP_FUENTE_MAGNITUD")
        ),
        location_source=_text_or_none(
            attributes.get("ESP_FUENTE_LOCALIZACION")
        ),
        payload=dict(attributes),
        description=_text_or_none(attributes.get("ESP_LUGAR")),
    )


@dataclass(frozen=True)
class StoredEventSolution:
    """Lo mínimo del evento guardado para decidir idempotencia."""

    location_solution_id: str | None
    magnitude_solution_id: str | None
    magnitude: float
    depth_km: float | None
    latitude: float
    longitude: float


def is_revision(
    stored: StoredEventSolution, incoming: NormalizedSgcEvent
) -> bool:
    """Spec §8: un cambio de solución o de valores es una REVISIÓN del
    mismo terremoto, jamás un terremoto nuevo."""
    if stored.location_solution_id != incoming.location_solution_id:
        return True
    if stored.magnitude_solution_id != incoming.magnitude_solution_id:
        return True
    if abs(stored.magnitude - incoming.magnitude) >= 0.05:
        return True
    if (stored.depth_km is None) != (incoming.depth_km is None):
        return True
    if (
        stored.depth_km is not None
        and incoming.depth_km is not None
        and abs(stored.depth_km - incoming.depth_km) >= 0.5
    ):
        return True
    if abs(stored.latitude - incoming.latitude) >= 0.005:
        return True
    if abs(stored.longitude - incoming.longitude) >= 0.005:
        return True
    return False


class SgcEventProvider(Protocol):
    """Adaptador de la fuente (spec §12): la plataforma no se acopla a
    una única URL."""

    async def fetch_recent(
        self, since: datetime
    ) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Textos de notificación (spec §57-59): la plataforma solo conoce
# sismo + ubicación + zona + ausencia de confirmación. Nada de «está
# herido», «está atrapado» ni «está en peligro».
# ---------------------------------------------------------------------------

def alert_notification_texts(
    affected_display_name: str, magnitude: float
) -> tuple[str, str]:
    return (
        "🚨 Alerta sísmica",
        (
            f"Se registró un sismo (M {magnitude:.1f}) y "
            f"{affected_display_name} se encontraba dentro del área de "
            "sacudida estimada. Todavía no ha confirmado que se "
            "encuentre bien. Consulta su última ubicación conocida en "
            "la plataforma."
        ),
    )


def safe_notification_texts(
    affected_display_name: str, confirmed_at_local: str
) -> tuple[str, str]:
    return (
        "✅ Confirmación recibida",
        (
            f"{affected_display_name} confirmó que se encuentra bien. "
            f"Hora de confirmación: {confirmed_at_local}."
        ),
    )


# ---------------------------------------------------------------------------
# Simulacros (spec §63-67).
# ---------------------------------------------------------------------------

def simulation_event_id(now: datetime, token_hex: str) -> str:
    """SIM-2026-XXXXXXXX; el token llega de secrets.token_hex(4)."""
    return f"SIM-{now.year}-{token_hex[:8].upper()}"


def validate_manual_zone_geometry(
    geometry: dict[str, Any],
) -> str | None:
    """Valida un MultiPolygon GeoJSON dibujado a mano por el admin
    (spec §65). Devuelve el motivo del rechazo o None si es válido."""
    if geometry.get("type") != "MultiPolygon":
        return "La geometría debe ser un MultiPolygon GeoJSON."
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        return "El MultiPolygon no trae coordenadas."
    for polygon in coordinates:
        if not isinstance(polygon, list) or not polygon:
            return "Cada polígono debe traer al menos un anillo."
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4:
                return "Cada anillo necesita al menos cuatro vértices."
            for position in ring:
                if (
                    not isinstance(position, list)
                    or len(position) < 2
                    or not all(
                        isinstance(v, (int, float)) for v in position[:2]
                    )
                ):
                    return "Cada vértice debe ser [longitud, latitud]."
                lon, lat = position[0], position[1]
                if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                    return "Hay vértices fuera del rango geográfico."
            if ring[0][:2] != ring[-1][:2]:
                return "Cada anillo debe cerrarse en su primer vértice."
    return None
