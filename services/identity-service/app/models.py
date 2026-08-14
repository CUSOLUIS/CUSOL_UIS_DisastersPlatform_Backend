from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


RequestedAccountType = Literal[
    "citizen", "volunteer", "organization_representative"
]
AccountRole = Literal["user", "moderator", "super_admin"]
AccountStatus = Literal["pending_verification", "active", "suspended"]


def to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class AccountRegistrationInput(ApiModel):
    """Entrada del contrato: additionalProperties false (extra=forbid).

    `confirmPassword` no pertenece al contrato y se rechaza. La política
    de contraseña se valida además en el servidor (security.py).
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    first_names: str = Field(min_length=1, max_length=80)
    last_names: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=254)
    phone: str | None = Field(default=None, min_length=7, max_length=30)
    department: str = Field(min_length=1, max_length=100)
    municipality: str = Field(min_length=1, max_length=100)
    requested_account_type: RequestedAccountType
    organization_name: str | None = Field(default=None, max_length=160)
    organization_role: str | None = Field(default=None, max_length=120)
    password: str = Field(min_length=1, max_length=128)
    terms_accepted: Literal[True]
    privacy_accepted: Literal[True]
    accuracy_confirmed: Literal[True]

    @model_validator(mode="after")
    def _organization_required(self):
        if self.requested_account_type == "organization_representative":
            name = (self.organization_name or "").strip()
            if not name:
                raise ValueError(
                    "organizationName es obligatorio para "
                    "organization_representative"
                )
        return self


class AccountRegistrationReceipt(ApiModel):
    request_id: UUID
    status: Literal["email_verification_required"]
    email_masked: str = Field(min_length=3)
    verification_expires_at: datetime
    assigned_role: Literal["user"]
    created_at: datetime


class EmailVerificationInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    token: str = Field(min_length=32, max_length=512)


class EmailVerificationReceipt(ApiModel):
    status: Literal["active"]
    verified_at: datetime


class AccountSessionInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)


class AuthenticatedAccount(ApiModel):
    id: UUID
    display_name: str = Field(min_length=1, max_length=161)
    email: str = Field(max_length=254)
    assigned_role: AccountRole
    status: Literal["active"]
    session_expires_at: datetime


class SessionEnvelope(ApiModel):
    """Respuesta interna de login: el gateway convierte el token en la
    cookie `cusol_session` y nunca lo devuelve en el cuerpo público."""

    account: AuthenticatedAccount
    session_token: str
    session_expires_at: datetime


# CHG-036 — Administración de cuentas (espejo del contrato). Nunca
# exponen hash de contraseña, tokens ni teléfono.
class AdminAccountSummary(ApiModel):
    id: UUID
    display_name: str = Field(min_length=1, max_length=161)
    email: str = Field(max_length=254)
    assigned_role: AccountRole
    status: AccountStatus
    active_sessions: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class AdminAccountDetail(AdminAccountSummary):
    department: str = Field(min_length=1, max_length=100)
    municipality: str = Field(min_length=1, max_length=100)
    requested_account_type: RequestedAccountType
    organization_name: str | None = Field(default=None, max_length=160)
    organization_role: str | None = Field(default=None, max_length=120)


class AdminAccountPage(ApiModel):
    items: list[AdminAccountSummary] = Field(max_length=50)
    total: int = Field(ge=0)
    limit: Literal[10, 25, 50]
    offset: int = Field(ge=0)
    generated_at: datetime


class AdminAccountUpdateInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=500)
    assigned_role: AccountRole | None = None
    status: AccountStatus | None = None

    @model_validator(mode="after")
    def _at_least_one_change(self):
        if self.assigned_role is None and self.status is None:
            raise ValueError(
                "se requiere assignedRole o status"
            )
        return self


class AdminReasonInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    reason: str = Field(min_length=10, max_length=500)


class HealthStatus(BaseModel):
    status: Literal["ok"]
    service: str
