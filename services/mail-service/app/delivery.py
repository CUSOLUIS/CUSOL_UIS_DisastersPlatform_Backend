"""Entrega SMTP (CHG-043, observabilidad y reintentos CHG-135).

Dos modos según configuración: con credenciales exige STARTTLS + login
(Brevo, `smtp-relay.brevo.com:587`); sin ellas entrega en claro
(Mailpit en desarrollo). La contraseña nunca se registra en logs.

CHG-135: cada intento queda en el log con id propio, proveedor, dominio
del destinatario (nunca la dirección completa), tipo de plantilla y el
código/mensaje SMTP real del proveedor. Los fallos transitorios (4xx de
cortesía, desconexiones, timeouts) se reintentan con backoff acotado;
los permanentes (5xx, credenciales) no. "Aceptado" significa que el
PROVEEDOR aceptó el mensaje: la entrega final al buzón ocurre después,
del lado del proveedor, y solo es visible en su consola/webhooks.
"""

import logging
import smtplib
import ssl
import uuid
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

import anyio

from .config import Settings
from .templates import RenderedEmail

logger = logging.getLogger("mail-service.delivery")

# Códigos SMTP de fallo transitorio: el proveedor pide reintentar.
TRANSIENT_SMTP_CODES = {421, 450, 451, 452}
# Reintentos acotados con backoff; misma conexión nueva por intento. El
# mensaje solo se reintenta tras una EXCEPCIÓN (nunca tras un envío
# aceptado), así que no puede duplicarse.
RETRY_DELAYS_SECONDS = (0.5, 2.0)


class DeliveryError(Exception):
    """El proveedor SMTP rechazó o no completó la entrega."""

    def __init__(
        self,
        detail: str,
        *,
        smtp_code: int | None = None,
        transient: bool = False,
    ):
        super().__init__(detail)
        self.smtp_code = smtp_code
        self.transient = transient


class Deliverer(Protocol):
    async def deliver(
        self, recipient: str, email: RenderedEmail
    ) -> None: ...


def recipient_domain(recipient: str) -> str:
    """Solo el dominio viaja a los logs; la dirección completa no."""
    _, _, domain = recipient.partition("@")
    return domain or "invalido"


def _classify(error: Exception) -> tuple[str, int | None, bool]:
    """(detalle sin secretos, código SMTP, ¿transitorio?)."""
    if isinstance(error, smtplib.SMTPResponseException):
        code = error.smtp_code
        raw = error.smtp_error
        text = (
            raw.decode("utf-8", "replace")
            if isinstance(raw, bytes)
            else str(raw)
        )
        return (
            f"SMTP {code}: {text[:300]}",
            code,
            code in TRANSIENT_SMTP_CODES,
        )
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        # {destinatario: (código, mensaje)} — se registra solo el
        # primer código; la dirección no se propaga al detalle.
        first = next(iter(error.recipients.values()), (None, b""))
        code = first[0]
        return (
            f"destinatario rechazado por el proveedor (SMTP {code})",
            code,
            bool(code) and code in TRANSIENT_SMTP_CODES,
        )
    if isinstance(error, smtplib.SMTPServerDisconnected):
        return ("el proveedor cerró la conexión", None, True)
    if isinstance(error, smtplib.SMTPException):
        return (f"SMTP rechazó la entrega: {type(error).__name__}", None, False)
    # OSError: DNS caído, timeout, red — transitorio por naturaleza.
    return (
        f"sin conexión con el SMTP: {type(error).__name__}",
        None,
        True,
    )


class SmtpDeliverer:
    def __init__(self, settings: Settings):
        self._settings = settings

    def _build_message(
        self, recipient: str, email: RenderedEmail
    ) -> EmailMessage:
        message = EmailMessage()
        message["From"] = formataddr(
            (self._settings.mail_from_name, self._settings.mail_from)
        )
        message["To"] = recipient
        message["Subject"] = email.subject
        # CHG-135: las respuestas de la gente llegan a un buzón vigilado
        # aunque el From sea del dominio de la plataforma.
        if self._settings.mail_reply_to:
            message["Reply-To"] = self._settings.mail_reply_to
        message.set_content(email.text_body)
        message.add_alternative(email.html_body, subtype="html")
        return message

    def _send_sync(self, message: EmailMessage) -> None:
        settings = self._settings
        with smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
        ) as smtp:
            if settings.authenticated:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(
                    settings.smtp_username, settings.smtp_password
                )
            smtp.send_message(message)

    async def deliver(
        self, recipient: str, email: RenderedEmail
    ) -> None:
        message = self._build_message(recipient, email)
        # Id propio del intento: correlaciona los logs de un mismo
        # mensaje sin exponer destinatario ni contenido.
        message_id = uuid.uuid4().hex[:12]
        domain = recipient_domain(recipient)
        provider = f"{self._settings.smtp_host}:{self._settings.smtp_port}"
        attempts = 1 + len(RETRY_DELAYS_SECONDS)

        for attempt in range(1, attempts + 1):
            try:
                await anyio.to_thread.run_sync(self._send_sync, message)
            except Exception as error:  # noqa: BLE001 — se clasifica abajo
                detail, smtp_code, transient = _classify(error)
                logger.warning(
                    "correo %s NO aceptado por el proveedor: "
                    "provider=%s dominio=%s plantilla=%s intento=%d/%d "
                    "codigo=%s transitorio=%s detalle=%s",
                    message_id,
                    provider,
                    domain,
                    email.kind,
                    attempt,
                    attempts,
                    smtp_code,
                    transient,
                    detail,
                )
                if transient and attempt < attempts:
                    await anyio.sleep(
                        RETRY_DELAYS_SECONDS[attempt - 1]
                    )
                    continue
                raise DeliveryError(
                    detail, smtp_code=smtp_code, transient=transient
                ) from error
            logger.info(
                "correo %s ACEPTADO por el proveedor (la entrega final "
                "al buzón la decide el proveedor): provider=%s "
                "dominio=%s plantilla=%s intento=%d/%d",
                message_id,
                provider,
                domain,
                email.kind,
                attempt,
                attempts,
            )
            return
