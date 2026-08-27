"""CHG-215 — ID compartible por cuenta (CUSOL-XXXXXX).

Alfabeto sin caracteres ambiguos (sin 0/O/1/I/L) para que el código se
pueda dictar por teléfono sin errores. El código identifica la cuenta al
vincular contactos de emergencia; es aleatorio y no enumerable.
"""

import secrets

SHARE_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
SHARE_CODE_LENGTH = 6
SHARE_CODE_PREFIX = "CUSOL-"


def generate_share_code() -> str:
    body = "".join(
        secrets.choice(SHARE_CODE_ALPHABET)
        for _ in range(SHARE_CODE_LENGTH)
    )
    return SHARE_CODE_PREFIX + body


def normalize_share_code(raw: str) -> str | None:
    """Acepta el código con o sin prefijo, en cualquier caja y con
    espacios alrededor; devuelve la forma canónica o None si no tiene
    la forma de un código."""
    cleaned = raw.strip().upper().replace(" ", "")
    if cleaned.startswith(SHARE_CODE_PREFIX):
        cleaned = cleaned[len(SHARE_CODE_PREFIX):]
    if len(cleaned) != SHARE_CODE_LENGTH:
        return None
    if any(ch not in SHARE_CODE_ALPHABET for ch in cleaned):
        return None
    return SHARE_CODE_PREFIX + cleaned
