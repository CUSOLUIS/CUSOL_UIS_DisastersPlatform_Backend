"""Correo de verificación (ADR-004, CHG-043).

La entrega real es responsabilidad exclusiva del mail-service (dueño de
las credenciales SMTP y de la plantilla); aquí solo se delega por HTTP
interno. El token viaja únicamente hacia el mail-service y dentro del
correo resultante: jamás en respuestas HTTP públicas ni en logs.
"""

from typing import Protocol

import httpx


class Mailer(Protocol):
    async def send_verification(
        self, recipient: str, token: str, expires_hours: int
    ) -> None: ...

    async def send_report_status(
        self,
        recipient: str,
        report_label: str,
        tracking_code: str,
        status_label: str,
    ) -> None: ...


class MailDeliveryUnavailable(Exception):
    """El mail-service no aceptó el correo de verificación."""


class HttpMailer:
    def __init__(
        self,
        mail_service_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = mail_service_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def _post(self, path: str, payload: dict) -> None:
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            try:
                response = await client.post(
                    f"{self._base_url}{path}", json=payload
                )
            except httpx.HTTPError as error:
                raise MailDeliveryUnavailable(
                    f"mail-service inalcanzable: {type(error).__name__}"
                ) from error
        if response.status_code != 202:
            raise MailDeliveryUnavailable(
                f"mail-service respondió {response.status_code}"
            )

    async def send_verification(
        self, recipient: str, token: str, expires_hours: int
    ) -> None:
        await self._post(
            "/internal/v1/verification-emails",
            {
                "recipient": recipient,
                "token": token,
                "expiresHours": expires_hours,
            },
        )

    async def send_report_status(
        self,
        recipient: str,
        report_label: str,
        tracking_code: str,
        status_label: str,
    ) -> None:
        # CHG-054: novedad del avance de un reporte hecho con cuenta.
        await self._post(
            "/internal/v1/report-status-emails",
            {
                "recipient": recipient,
                "reportLabel": report_label,
                "trackingCode": tracking_code,
                "statusLabel": status_label,
            },
        )
