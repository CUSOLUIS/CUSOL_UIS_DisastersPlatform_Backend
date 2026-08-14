"""Reglas de moderación de aportes ciudadanos (CHG-034).

Un aporte (novedad de persona o valoración de lugar) nace `under_review`
y solo una decisión humana con rol autorizado lo convierte en dato que
afecta proyecciones públicas. DEC-009 sigue pendiente: mientras no se
resuelva quién aprueba, ningún endpoint del cliente expone estas
operaciones; las reglas viven aquí para que la capa de persistencia y
las pruebas compartan una única fuente.
"""

from typing import Literal

ModerationDecision = Literal["accepted", "rejected", "withdrawn"]


class ModerationNotAllowedError(Exception):
    """El rol no está autorizado para decidir moderación."""


class InvalidModerationTransitionError(Exception):
    """La decisión no es válida desde el estado actual del aporte."""


def ensure_moderator_role(role: str | None) -> None:
    """Rechaza decidir con rol vacío o `user` (regla provisional DEC-009)."""
    normalized = (role or "").strip().casefold()
    if not normalized or normalized == "user":
        raise ModerationNotAllowedError(
            "La moderación exige un rol distinto de `user`."
        )


def can_decide(current_status: str, decision: ModerationDecision) -> bool:
    """Transiciones válidas de un aporte.

    `under_review` puede aceptarse o rechazarse; solo un aporte aceptado
    puede retirarse (y su efecto público se recalcula al hacerlo).
    """
    if current_status == "under_review":
        return decision in ("accepted", "rejected")
    if current_status == "accepted":
        return decision == "withdrawn"
    return False


def ensure_transition(
    current_status: str, decision: ModerationDecision
) -> None:
    if not can_decide(current_status, decision):
        raise InvalidModerationTransitionError(
            f"No es válido pasar de {current_status} a {decision}."
        )


def public_status_from_accepted_outcomes(
    accepted_outcomes: list[str],
) -> str:
    """Estado público canónico tras moderación.

    El último resultado aceptado (la lista llega ordenada de más antiguo
    a más reciente) define el estado; sin aceptados, la persona sigue
    `missing`. Crear una novedad jamás pasa por aquí.
    """
    return accepted_outcomes[-1] if accepted_outcomes else "missing"


def recompute_rating_aggregate(
    accepted_ratings: list[int],
) -> tuple[float | None, int]:
    """Promedio y conteo públicos: únicamente valoraciones aceptadas."""
    if not accepted_ratings:
        return None, 0
    average = sum(accepted_ratings) / len(accepted_ratings)
    return round(average, 2), len(accepted_ratings)


# CHG-035 — Proyección `building_pending` posterior a aceptación.
# Estas reglas existen para cuando DEC-014 apruebe publicar; mientras
# tanto la proyección permanece DESACTIVADA (los `decide_*` reciben
# proyección None y no tocan el mapa).


def degrade_coordinates(
    latitude: float, longitude: float
) -> tuple[float, float]:
    """Precisión pública degradada (~1 km); nunca la coordenada exacta."""
    return round(latitude, 2), round(longitude, 2)


def build_building_projection_labels(
    sector: str, municipality: str, department: str
) -> tuple[str, str]:
    """Título y etiqueta públicos saneados del inmueble.

    Solo zona: jamás dirección exacta, referencia privada, contacto,
    descripción libre ni identidad del reportante.
    """
    title = (
        "Edificio sin inspección registrada — "
        f"{sector.strip()}"
    )
    location_label = f"{municipality.strip()}, {department.strip()}"
    return title, location_label
