"""Notificaciones de avance de reportes con cuenta (CHG-054).

Cuando una decisión administrativa avanza un envío hecho con cuenta,
se avisa a identity-service (dueño de los correos) para que entregue la
novedad por el mail-service. El fallo de la notificación JAMÁS revierte
ni bloquea la decisión: es un aviso de cortesía, no parte de la
transacción.
"""

from typing import Protocol
from uuid import UUID

import httpx

# Etiquetas humanas por tipo administrativo; sin PII.
REPORT_LABELS: dict[str, str] = {
    "missing_person_report": "Reporte de persona desaparecida",
    "unverified_building_report": "Reporte de edificio sin verificar",
    "person_status_report": "Novedad de persona",
    "aid_location_rating": "Valoración de lugar de ayuda",
    "community_meal_offer": "Oferta de comida comunitaria",
    "temporary_shelter_offer": "Oferta de alojamiento temporal",
}

STATUS_LABELS: dict[str, str] = {
    "accept": "Fue revisado y aceptado por el equipo.",
    "reject": (
        "Fue revisado y no pudo aceptarse en esta ocasión. Puedes "
        "enviar un nuevo reporte con más información."
    ),
    "request_changes": (
        "El equipo necesita información adicional para continuar la "
        "revisión."
    ),
}


class ReportNotifier(Protocol):
    async def notify_report_status(
        self,
        account_id: UUID,
        report_label: str,
        tracking_code: str,
        status_label: str,
    ) -> None: ...


class HttpReportNotifier:
    def __init__(
        self,
        identity_service_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._endpoint = (
            f"{identity_service_url.rstrip('/')}"
            "/internal/v1/notifications/report-status"
        )
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def notify_report_status(
        self,
        account_id: UUID,
        report_label: str,
        tracking_code: str,
        status_label: str,
    ) -> None:
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            # Mejor esfuerzo: cualquier error se ignora aguas arriba.
            await client.post(
                self._endpoint,
                json={
                    "accountId": str(account_id),
                    "reportLabel": report_label,
                    "trackingCode": tracking_code,
                    "statusLabel": status_label,
                },
            )
