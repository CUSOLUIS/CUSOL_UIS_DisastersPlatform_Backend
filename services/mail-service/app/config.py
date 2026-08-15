"""Configuración del servicio de correo (CHG-043).

Las credenciales SMTP jamás viven en el repositorio: llegan por entorno
(interpoladas desde el `.env` local o el compose del VPS). Sin
usuario/contraseña el servicio entrega en claro (Mailpit en desarrollo);
con ellos exige STARTTLS + login (Brevo en producción).
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_timeout_seconds: float = 15.0
    mail_from: str = "no-reply@cusol.local"
    mail_from_name: str = "Plataforma CUSOL Desastres"
    public_base_url: str = "http://localhost:3100"

    @property
    def authenticated(self) -> bool:
        return bool(self.smtp_username and self.smtp_password)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            smtp_host=os.getenv("SMTP_HOST", "mailpit"),
            smtp_port=int(os.getenv("SMTP_PORT", "1025")),
            smtp_username=os.getenv("SMTP_USERNAME", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_timeout_seconds=float(
                os.getenv("SMTP_TIMEOUT_SECONDS", "15")
            ),
            mail_from=os.getenv("MAIL_FROM", "no-reply@cusol.local"),
            mail_from_name=os.getenv(
                "MAIL_FROM_NAME", "Plataforma CUSOL Desastres"
            ),
            public_base_url=os.getenv(
                "PUBLIC_BASE_URL", "http://localhost:3100"
            ).rstrip("/"),
        )
