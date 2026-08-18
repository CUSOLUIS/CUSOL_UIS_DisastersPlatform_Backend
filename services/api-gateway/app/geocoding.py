"""Geocodificación por el gateway (CHG-147).

El navegador en producción no puede llamar directo a nominatim.org
(bloqueo CORS/política de uso), así que el gateway expone
`/api/v1/geocode/search` y `/api/v1/geocode/reverse` y consulta a
Nominatim del lado del servidor con User-Agent propio, caché corta en
memoria y límite por origen. Aquí viven la caché y el moldeado de las
respuestas; los endpoints están en `main.py`.
"""

import time
from collections import OrderedDict
from typing import Any


class TtlCache:
    """Caché TTL mínima con desalojo LRU, para una instancia única.

    Igual que el rate limiter: un despliegue con réplicas debe
    sustituirla por un almacén compartido manteniendo la interfaz.
    """

    def __init__(self, ttl_seconds: float, max_entries: int):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str, default: Any = None) -> Any:
        # `default` permite distinguir un miss de un valor guardado que
        # es None (p. ej. "punto sin dirección conocida", que también
        # se recuerda).
        entry = self._entries.get(key)
        if entry is None:
            return default
        stored_at, value = entry
        if time.monotonic() - stored_at > self._ttl:
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return value

    def put(self, key: str, value: Any) -> None:
        self._entries[key] = (time.monotonic(), value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)


def shape_search_payload(payload: Any) -> list[dict[str, Any]]:
    """Reduce la respuesta de /search a las candidatas del contrato."""
    if not isinstance(payload, list):
        return []
    candidates: list[dict[str, Any]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        label = row.get("display_name")
        try:
            latitude = float(row.get("lat"))
            longitude = float(row.get("lon"))
        except (TypeError, ValueError):
            continue
        if not isinstance(label, str) or not label.strip():
            continue
        candidates.append(
            {
                "label": label,
                "latitude": latitude,
                "longitude": longitude,
            }
        )
        if len(candidates) >= 5:
            break
    return candidates


def _first_string(address: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = address.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# CHG-156: piezas administrativas del display_name; la dirección corta
# termina donde empieza la primera de ellas.
_ADMINISTRATIVE_KEYS = (
    "municipality",
    "city",
    "town",
    "village",
    "county",
    "state_district",
    "state",
    "region",
    "postcode",
    "country",
)


def _short_address_line(label: str, address: dict[str, Any]) -> str | None:
    administrative = {
        value.casefold()
        for key in _ADMINISTRATIVE_KEYS
        if isinstance((value := address.get(key)), str) and value.strip()
    }
    kept: list[str] = []
    for part in label.split(", "):
        if part.strip().casefold() in administrative:
            break
        kept.append(part)
    line = ", ".join(kept).strip()
    return line or None


def shape_reverse_payload(payload: Any) -> dict[str, Any] | None:
    """Reduce la respuesta de /reverse; None si el punto no tiene dirección."""
    if not isinstance(payload, dict):
        return None
    label = payload.get("display_name")
    if not isinstance(label, str) or not label.strip():
        return None
    address = payload.get("address")
    if not isinstance(address, dict):
        address = {}
    return {
        "label": label,
        # CHG-156: dirección corta (vía, barrio, comuna) para el campo
        # Dirección; el label completo se conserva por compatibilidad.
        "address_line": _short_address_line(label, address),
        # CHG-156: en Colombia el municipio real es `county`
        # (admin_level 6); `city` suele ser "Perímetro Urbano X".
        "municipality": _first_string(
            address, "county", "municipality", "city", "town", "village"
        ),
        "department": _first_string(address, "state", "region"),
    }
