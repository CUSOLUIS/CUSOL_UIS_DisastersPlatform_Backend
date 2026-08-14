"""Reglas de la consola de superadministración (CHG-036).

Estado unificado, acciones disponibles, allowlists de edición por tipo y
tokens temporales de evidencia. Estas reglas son puras para que la capa
HTTP, la persistencia y las pruebas compartan una única fuente. Nada de
lo que sale de aquí incluye secretos ni valores privados sin clasificar.
"""

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID


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
AdminAction = Literal[
    "accept", "reject", "request_changes", "archive", "restore"
]

# Tipos con expediente persistido hoy; los registros de centros/puntos
# se incorporarán cuando exista su flujo de captura.
ACTIVE_KINDS: tuple[str, ...] = (
    "missing_person_report",
    "unverified_building_report",
    "person_status_report",
    "aid_location_rating",
)


def unified_status(
    domain_status: str,
    needs_information: bool,
    archived_at: datetime | None,
) -> AdminModerationStatus:
    """Proyección de estados del dominio al estado administrativo.

    `withdrawn` se presenta como `archived` (retirado del flujo); el
    archivo lógico es una capa superpuesta que nunca destruye el estado
    del dominio.
    """
    if archived_at is not None or domain_status == "withdrawn":
        return "archived"
    if needs_information:
        return "needs_information"
    if domain_status in ("verified", "accepted"):
        return "accepted"
    if domain_status == "rejected":
        return "rejected"
    return "under_review"


def available_actions(
    domain_status: str,
    needs_information: bool,
    archived_at: datetime | None,
) -> list[AdminAction]:
    """Acciones legales desde el estado actual; nada más se acepta."""
    if archived_at is not None:
        return ["restore"]
    if domain_status == "withdrawn":
        # Retirado por el dominio: solo puede archivarse formalmente.
        return ["archive"]
    status = unified_status(domain_status, needs_information, None)
    if status == "under_review":
        return ["accept", "reject", "request_changes", "archive"]
    if status == "needs_information":
        return ["accept", "reject", "archive"]
    return ["archive"]


@dataclass(frozen=True)
class EditableField:
    """Campo editable: columna destino y validación mínima."""

    column: str
    max_length: int
    input_kind: str = "text"
    min_length: int = 1
    pattern: str | None = None


# Allowlists por tipo. La evidencia ciudadana (novedades y valoraciones)
# no se edita jamás: su integridad respalda la decisión de moderación.
EDITABLE_FIELDS: dict[str, dict[str, EditableField]] = {
    "missing_person_report": {
        "department": EditableField("department", 100),
        "municipality": EditableField("municipality", 100),
        "lastSeenArea": EditableField("last_seen_area", 300),
        "clothingDescription": EditableField(
            "clothing_description", 1000, "multiline"
        ),
        "circumstances": EditableField(
            "circumstances", 2000, "multiline"
        ),
        "additionalDescription": EditableField(
            "additional_description", 2000, "multiline", min_length=0
        ),
    },
    "unverified_building_report": {
        "buildingReference": EditableField(
            "building_reference", 180, min_length=2
        ),
        "department": EditableField("department", 100),
        "municipality": EditableField("municipality", 100),
        "sector": EditableField("sector", 160),
    },
    "person_status_report": {},
    "aid_location_rating": {},
}


class AdminEditError(Exception):
    """Cambio rechazado; el detalle es seguro para el cliente."""


def validate_changes(
    kind: str, changes: dict[str, str | None]
) -> dict[str, str | None]:
    """Valida las ediciones contra la allowlist del tipo.

    Devuelve un mapa columna -> valor listo para persistir; cualquier
    campo fuera de la allowlist o valor inválido se rechaza completo.
    """
    allowlist = EDITABLE_FIELDS.get(kind, {})
    if not changes:
        raise AdminEditError("No hay cambios que aplicar.")
    resolved: dict[str, str | None] = {}
    for key, value in changes.items():
        field = allowlist.get(key)
        if field is None:
            raise AdminEditError(
                f"El campo {key} no es editable para este tipo."
            )
        if value is None:
            if field.min_length > 0:
                raise AdminEditError(
                    f"El campo {key} no admite vaciarse."
                )
            resolved[field.column] = None
            continue
        trimmed = value.strip()
        if len(trimmed) < field.min_length:
            raise AdminEditError(f"El campo {key} es demasiado corto.")
        if len(trimmed) > field.max_length:
            raise AdminEditError(f"El campo {key} es demasiado largo.")
        if field.pattern and not re.fullmatch(field.pattern, trimmed):
            raise AdminEditError(f"El campo {key} tiene formato inválido.")
        resolved[field.column] = trimmed
    return resolved


# --- Acceso temporal a evidencia (≤ 300 segundos) ---

EVIDENCE_GRANT_MAX_TTL_SECONDS = 300


def _grant_key(secret: str) -> bytes:
    return hashlib.sha256(f"evidence-grant:{secret}".encode()).digest()


def make_evidence_grant_token(
    secret: str,
    submission_id: UUID,
    evidence_id: UUID,
    actor_account_id: UUID,
    expires_at: datetime,
) -> str:
    """Token opaco firmado, ligado al actor; sin nombre original."""
    payload = (
        f"{submission_id}:{evidence_id}:{actor_account_id}:"
        f"{int(expires_at.timestamp())}"
    )
    signature = hmac.new(
        _grant_key(secret), payload.encode(), hashlib.sha256
    ).hexdigest()
    raw = f"{payload}:{signature}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


@dataclass(frozen=True)
class EvidenceGrant:
    submission_id: UUID
    evidence_id: UUID
    actor_account_id: UUID
    expires_at: datetime


def parse_evidence_grant_token(
    secret: str, token: str, now: datetime | None = None
) -> EvidenceGrant | None:
    """Valida firma y vigencia; None ante cualquier anomalía."""
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        parts = raw.split(":")
        if len(parts) != 5:
            return None
        submission_raw, evidence_raw, actor_raw, epoch_raw, signature = (
            parts
        )
        payload = (
            f"{submission_raw}:{evidence_raw}:{actor_raw}:{epoch_raw}"
        )
        expected = hmac.new(
            _grant_key(secret), payload.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return None
        expires_at = datetime.fromtimestamp(int(epoch_raw), tz=UTC)
        if (now or datetime.now(UTC)) >= expires_at:
            return None
        return EvidenceGrant(
            submission_id=UUID(submission_raw),
            evidence_id=UUID(evidence_raw),
            actor_account_id=UUID(actor_raw),
            expires_at=expires_at,
        )
    except (ValueError, UnicodeDecodeError):
        return None
