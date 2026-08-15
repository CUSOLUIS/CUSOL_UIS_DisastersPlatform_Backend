"""Entrega SMTP (CHG-043).

Dos modos según configuración: con credenciales exige STARTTLS + login
(Brevo, `smtp-relay.brevo.com:587`); sin ellas entrega en claro
(Mailpit en desarrollo). La contraseña nunca se registra en logs.
"""

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

import anyio

from .config import Settings
from .templates import RenderedEmail


class DeliveryError(Exception):
    """El proveedor SMTP rechazó o no completó la entrega."""


class Deliverer(Protocol):
    async def deliver(self, recipient: str, email: RenderedEmail) -> None: ...


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
        try:
            await anyio.to_thread.run_sync(self._send_sync, message)
        except smtplib.SMTPException as error:
            raise DeliveryError(
                f"SMTP rechazó la entrega: {type(error).__name__}"
            ) from error
        except OSError as error:
            raise DeliveryError(
                f"No hay conexión con el SMTP: {type(error).__name__}"
            ) from error
