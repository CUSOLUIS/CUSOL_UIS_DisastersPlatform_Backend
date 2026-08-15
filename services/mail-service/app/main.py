"""mail-service (CHG-043).

Propietario exclusivo de la entrega de correo de la plataforma. Expone
solo endpoints internos (nunca via gateway público): identity-service le
delega el correo de verificación de cuentas. El token de verificación
jamás aparece en logs ni en respuestas.
"""

import re

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings
from .delivery import Deliverer, DeliveryError, SmtpDeliverer
from .templates import (
    render_report_status_email,
    render_verification_email,
)

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class VerificationEmailInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    recipient: str = Field(min_length=6, max_length=254)
    token: str = Field(min_length=16, max_length=256)
    expires_hours: int = Field(
        alias="expiresHours", ge=1, le=168
    )

    @field_validator("recipient")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        normalized = value.strip()
        if not EMAIL_PATTERN.match(normalized):
            raise ValueError("recipient no es un correo válido")
        return normalized


class VerificationEmailReceipt(BaseModel):
    status: str = "accepted"


# CHG-054 — Novedad del avance de un reporte hecho con cuenta.
class ReportStatusEmailInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    recipient: str = Field(min_length=6, max_length=254)
    report_label: str = Field(
        alias="reportLabel", min_length=3, max_length=120
    )
    tracking_code: str = Field(
        alias="trackingCode", min_length=3, max_length=80
    )
    status_label: str = Field(
        alias="statusLabel", min_length=3, max_length=300
    )

    @field_validator("recipient")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        normalized = value.strip()
        if not EMAIL_PATTERN.match(normalized):
            raise ValueError("recipient no es un correo válido")
        return normalized


def problem_response(
    detail: str,
    *,
    title: str,
    status_code: int = 503,
    problem_type: str = "service-unavailable",
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": f"https://cusol.uis.edu.co/problems/{problem_type}",
            "title": title,
            "status": status_code,
            "detail": detail,
        },
    )


def create_app(
    settings: Settings | None = None,
    deliverer: Deliverer | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_deliverer = deliverer or SmtpDeliverer(resolved_settings)

    application = FastAPI(
        title="CUSOL Mail Service",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.get("/health/live")
    async def live():
        return {"status": "live"}

    @application.get("/health/ready")
    async def ready():
        return {"status": "ready"}

    @application.post(
        "/internal/v1/verification-emails",
        status_code=202,
        response_model=VerificationEmailReceipt,
        responses={
            422: {"description": "Datos inválidos"},
            503: {"description": "Proveedor SMTP no disponible"},
        },
    )
    async def send_verification_email(payload: VerificationEmailInput):
        email = render_verification_email(
            resolved_settings.public_base_url,
            payload.token,
            payload.expires_hours,
        )
        try:
            await resolved_deliverer.deliver(payload.recipient, email)
        except DeliveryError:
            return problem_response(
                "No fue posible entregar el correo de verificación; "
                "el registro puede reintentarse.",
                title="Proveedor de correo no disponible",
            )
        return VerificationEmailReceipt()

    @application.post(
        "/internal/v1/report-status-emails",
        status_code=202,
        response_model=VerificationEmailReceipt,
        responses={
            422: {"description": "Datos inválidos"},
            503: {"description": "Proveedor SMTP no disponible"},
        },
    )
    async def send_report_status_email(payload: ReportStatusEmailInput):
        email = render_report_status_email(
            resolved_settings.public_base_url,
            payload.report_label,
            payload.tracking_code,
            payload.status_label,
        )
        try:
            await resolved_deliverer.deliver(payload.recipient, email)
        except DeliveryError:
            return problem_response(
                "No fue posible entregar la novedad del reporte.",
                title="Proveedor de correo no disponible",
            )
        return VerificationEmailReceipt()

    return application


app = create_app()
