"""Correo de verificación (ADR-004).

En desarrollo se entrega a Mailpit por SMTP. El token viaja únicamente en
el cuerpo del correo: jamás en respuestas HTTP ni en logs.
"""

import smtplib
from email.message import EmailMessage
from typing import Protocol

import anyio


class Mailer(Protocol):
    async def send_verification(
        self, recipient: str, token: str, expires_hours: int
    ) -> None: ...


class SmtpMailer:
    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        public_base_url: str,
    ):
        self._host = host
        self._port = port
        self._sender = sender
        self._public_base_url = public_base_url

    async def send_verification(
        self, recipient: str, token: str, expires_hours: int
    ) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = recipient
        message["Subject"] = (
            "Verifica tu correo — Plataforma CUSOL Desastres"
        )
        link = (
            f"{self._public_base_url}/verificar-correo?token={token}"
        )
        message.set_content(
            "Hola:\n\n"
            "Recibimos una solicitud de registro con este correo.\n"
            f"Para activar la cuenta abre el enlace (vence en "
            f"{expires_hours} horas):\n\n{link}\n\n"
            f"Si el enlace no funciona, usa este código de "
            f"verificación:\n{token}\n\n"
            "Si no solicitaste la cuenta, ignora este mensaje.\n"
        )

        def _send() -> None:
            with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
                smtp.send_message(message)

        await anyio.to_thread.run_sync(_send)
