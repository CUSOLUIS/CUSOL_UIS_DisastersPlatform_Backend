"""Primitivas de seguridad del identity-service (ADR-004).

Contraseñas: Argon2id con parámetros configurables. Tokens: 32 bytes
aleatorios; en la base solo se guarda su SHA-256. Nunca se registran
secretos ni PII en logs.
"""

import hashlib
import hmac
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from argon2.low_level import Type


PASSWORD_RULES = (
    (re.compile(r"[A-ZÁÉÍÓÚÑ]"), "una letra mayúscula"),
    (re.compile(r"[a-záéíóúñ]"), "una letra minúscula"),
    (re.compile(r"\d"), "un número"),
    (re.compile(r"[^\w\s]|_"), "un símbolo"),
)

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def build_password_hasher(
    time_cost: int, memory_cost: int, parallelism: int
) -> PasswordHasher:
    return PasswordHasher(
        time_cost=time_cost,
        memory_cost=memory_cost,
        parallelism=parallelism,
        type=Type.ID,
    )


def password_policy_errors(password: str) -> list[str]:
    errors: list[str] = []
    if not 12 <= len(password) <= 128:
        errors.append("entre 12 y 128 caracteres")
    for pattern, description in PASSWORD_RULES:
        if not pattern.search(password):
            errors.append(description)
    return errors


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_PATTERN.match(email)) and len(email) <= 254


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    visible = local[0] if local else "*"
    return f"{visible}***@{domain}"


def generate_token() -> str:
    """Token aleatorio urlsafe de 32 bytes (43 caracteres)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def verify_password(
    hasher: PasswordHasher, password_hash: str, password: str
) -> bool:
    try:
        return hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, ValueError):
        return False
