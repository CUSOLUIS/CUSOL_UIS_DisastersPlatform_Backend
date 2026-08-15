"""Reglas de ofertas comunitarias de comida y alojamiento (CHG-044).

Reglas puras compartidas por la capa HTTP, la persistencia y las
pruebas: transiciones de disponibilidad del propietario, cierre por
capacidad cero, huella de idempotencia y carga estricta de la clave de
cifrado exclusiva de ofertas. La aceptación/publicación permanece
BLOQUEADA mientras DEC-020 y DEC-021 sigan pendientes.
"""

import hashlib
import hmac
import os
import secrets
import stat
from datetime import datetime

AID_OFFER_LEGAL_TEXT_VERSION = "chg-044/legal-v1"

# Estados terminales del propietario: ninguna actualización posterior.
TERMINAL_AVAILABILITY = ("fulfilled", "withdrawn", "expired")

# Transiciones operativas permitidas al propietario (FR-008/FR-009).
OWNER_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "scheduled": ("active", "paused", "withdrawn", "fulfilled"),
    "active": ("paused", "fulfilled", "withdrawn"),
    "paused": ("active", "fulfilled", "withdrawn"),
}

CAPACITY_UNIT_BY_KIND = {
    "community_meal": "servings",
    "temporary_shelter": "spaces",
}
MAX_UNITS_BY_KIND = {
    "community_meal": 100_000,
    "temporary_shelter": 1_000,
}


class OwnerTransitionError(Exception):
    """La transición de disponibilidad no es válida (409)."""


class OwnerUpdateInvalidError(Exception):
    """La actualización es incoherente (422)."""


def generate_tracking_code(now: datetime) -> str:
    """Código privado del expediente, aleatorio y no secuencial."""
    return f"AID-{now.year}-{secrets.token_hex(4).upper()}"


def generate_public_offer_code(now: datetime) -> str:
    """Código de la proyección pública, distinto al del expediente."""
    return f"OFR-{now.year}-{secrets.token_hex(4).upper()}"


def idempotency_key_hash(key: str) -> str:
    """La llave jamás se persiste ni registra en claro."""
    return hashlib.sha256(key.encode()).hexdigest()


def request_fingerprint(body: bytes) -> str:
    """Huella del cuerpo para detectar reuso de llave con otro contenido."""
    return hashlib.sha256(body).hexdigest()


def same_fingerprint(stored: str, incoming: str) -> bool:
    return hmac.compare_digest(stored, incoming)


def resolve_owner_update(
    kind: str,
    current_availability: str,
    requested_availability: str | None,
    requested_units: int | None,
) -> tuple[str | None, int | None]:
    """Disponibilidad y unidades resultantes de un PATCH de propietario.

    Capacidad cero SIEMPRE cierra como `fulfilled` (FR-008); pedir cero
    con otro estado explícito es incoherente. Un estado terminal no
    admite más actualizaciones.
    """
    if current_availability in TERMINAL_AVAILABILITY:
        raise OwnerTransitionError(
            f"La oferta ya está {current_availability} y no admite "
            "cambios."
        )
    if requested_units is not None:
        maximum = MAX_UNITS_BY_KIND[kind]
        if not 0 <= requested_units <= maximum:
            raise OwnerUpdateInvalidError(
                f"Las unidades deben estar entre 0 y {maximum}."
            )
    resolved_availability = requested_availability
    if requested_units == 0:
        if requested_availability not in (None, "fulfilled"):
            raise OwnerUpdateInvalidError(
                "Capacidad cero cierra la oferta como fulfilled; el "
                "estado solicitado es incoherente."
            )
        resolved_availability = "fulfilled"
    if (
        resolved_availability is not None
        and resolved_availability != current_availability
    ):
        allowed = OWNER_TRANSITIONS.get(current_availability, ())
        if resolved_availability not in allowed:
            raise OwnerTransitionError(
                f"No es válido pasar de {current_availability} a "
                f"{resolved_availability}."
            )
    return resolved_availability, requested_units


def publication_visible(availability_status: str) -> bool:
    """Solo una oferta operativamente activa aparece en el directorio."""
    return availability_status == "active"


class AidOfferKeyError(Exception):
    """La clave de cifrado de ofertas no está disponible o es insegura."""


def load_aid_offer_key(path: str) -> str:
    """Clave exclusiva de ofertas desde un secreto montado.

    Falla si el archivo no existe, está vacío, es demasiado corto o
    tiene permisos legibles por otros usuarios (readiness debe caer y
    las escrituras rechazarse con 503).
    """
    try:
        info = os.stat(path)
    except OSError as error:
        raise AidOfferKeyError(
            "El secreto AID_OFFER_ENCRYPTION_KEY_FILE no existe."
        ) from error
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise AidOfferKeyError(
            "El secreto de ofertas tiene permisos inseguros; se "
            "requiere 0600."
        )
    try:
        with open(path, encoding="utf-8") as handle:
            key = handle.read().strip()
    except OSError as error:
        raise AidOfferKeyError(
            "No fue posible leer el secreto de ofertas."
        ) from error
    if len(key) < 16:
        raise AidOfferKeyError(
            "La clave de ofertas debe tener al menos 16 caracteres."
        )
    return key
