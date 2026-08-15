"""Tests del mail-service (CHG-043)."""

import smtplib
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.delivery import DeliveryError, SmtpDeliverer
from app.main import create_app
from app.templates import render_verification_email

TOKEN = "tok-0123456789abcdef0123456789abcdef"


class RecordingDeliverer:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.sent: list[tuple[str, object]] = []

    async def deliver(self, recipient, email):
        if self.error is not None:
            raise self.error
        self.sent.append((recipient, email))


def make_client(deliverer):
    application = create_app(
        settings=Settings(public_base_url="https://cusol.example"),
        deliverer=deliverer,
    )
    return TestClient(application)


def valid_payload(**overrides):
    payload = {
        "recipient": "persona@example.com",
        "token": TOKEN,
        "expiresHours": 24,
    }
    payload.update(overrides)
    return payload


def test_send_verification_email_accepted():
    deliverer = RecordingDeliverer()
    client = make_client(deliverer)

    response = client.post(
        "/internal/v1/verification-emails", json=valid_payload()
    )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert len(deliverer.sent) == 1
    recipient, email = deliverer.sent[0]
    assert recipient == "persona@example.com"
    assert TOKEN in email.text_body
    assert TOKEN in email.html_body


def test_invalid_recipient_rejected():
    client = make_client(RecordingDeliverer())

    response = client.post(
        "/internal/v1/verification-emails",
        json=valid_payload(recipient="no-es-un-correo"),
    )

    assert response.status_code == 422


def test_short_token_rejected():
    client = make_client(RecordingDeliverer())

    response = client.post(
        "/internal/v1/verification-emails",
        json=valid_payload(token="corto"),
    )

    assert response.status_code == 422


def test_delivery_error_returns_503_problem():
    deliverer = RecordingDeliverer(error=DeliveryError("SMTP caído"))
    client = make_client(deliverer)

    response = client.post(
        "/internal/v1/verification-emails", json=valid_payload()
    )

    assert response.status_code == 503
    body = response.json()
    assert body["title"] == "Proveedor de correo no disponible"
    assert TOKEN not in response.text


def test_template_contains_link_and_expiry():
    email = render_verification_email(
        "https://cusol.example", TOKEN, 24
    )
    link = f"https://cusol.example/verificar-correo?token={TOKEN}"
    assert link in email.text_body
    assert link in email.html_body
    assert "24 horas" in email.text_body
    assert "24 horas" in email.html_body
    assert "Confirmar mi correo" in email.html_body


class FakeSmtp:
    """Reemplazo de smtplib.SMTP que registra la secuencia usada."""

    instances: list["FakeSmtp"] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.calls: list[str] = []
        self.messages: list[EmailMessage] = []
        FakeSmtp.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, username, password):
        self.calls.append(f"login:{username}")

    def send_message(self, message):
        self.calls.append("send")
        self.messages.append(message)


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSmtp.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    return FakeSmtp


@pytest.mark.anyio
async def test_authenticated_delivery_uses_starttls_and_login(fake_smtp):
    settings = Settings(
        smtp_host="smtp-relay.brevo.com",
        smtp_port=587,
        smtp_username="usuario@example.com",
        smtp_password="xsmtpsib-clave",
    )
    email = render_verification_email(
        settings.public_base_url, TOKEN, 24
    )

    await SmtpDeliverer(settings).deliver("persona@example.com", email)

    smtp = fake_smtp.instances[0]
    assert smtp.calls == [
        "starttls",
        "login:usuario@example.com",
        "send",
    ]
    message = smtp.messages[0]
    assert message["To"] == "persona@example.com"
    assert "Plataforma CUSOL Desastres" in message["From"]


@pytest.mark.anyio
async def test_unauthenticated_delivery_skips_starttls(fake_smtp):
    settings = Settings()
    email = render_verification_email(
        settings.public_base_url, TOKEN, 24
    )

    await SmtpDeliverer(settings).deliver("persona@example.com", email)

    assert fake_smtp.instances[0].calls == ["send"]


@pytest.mark.anyio
async def test_smtp_failure_raises_delivery_error(monkeypatch):
    def boom(*args, **kwargs):
        raise smtplib.SMTPServerDisconnected("adiós")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    settings = Settings()
    email = render_verification_email(
        settings.public_base_url, TOKEN, 24
    )

    with pytest.raises(DeliveryError):
        await SmtpDeliverer(settings).deliver(
            "persona@example.com", email
        )
