"""Tests del mail-service (CHG-043)."""

import smtplib
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from app import delivery
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
    attempts = {"count": 0}

    def boom(*args, **kwargs):
        attempts["count"] += 1
        raise smtplib.SMTPServerDisconnected("adiós")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    # CHG-135: sin esperas reales en pruebas.
    monkeypatch.setattr(delivery, "RETRY_DELAYS_SECONDS", (0, 0))
    settings = Settings()
    email = render_verification_email(
        settings.public_base_url, TOKEN, 24
    )

    with pytest.raises(DeliveryError) as excinfo:
        await SmtpDeliverer(settings).deliver(
            "persona@example.com", email
        )
    # La desconexión es transitoria: se agotan los reintentos acotados.
    assert attempts["count"] == 3
    assert excinfo.value.transient is True


# CHG-135 — Reintentos acotados y clasificación de errores del
# proveedor: los transitorios reintentan con backoff, los permanentes
# no; el código SMTP real queda en el error (y en los logs).
@pytest.mark.anyio
async def test_transient_smtp_code_retries_then_succeeds(monkeypatch):
    attempts = {"count": 0}

    class FlakySmtp(FakeSmtp):
        def send_message(self, message):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise smtplib.SMTPResponseException(
                    451, b"4.3.0 Temporary system problem"
                )
            super().send_message(message)

    monkeypatch.setattr(smtplib, "SMTP", FlakySmtp)
    monkeypatch.setattr(delivery, "RETRY_DELAYS_SECONDS", (0, 0))
    FakeSmtp.instances = []
    settings = Settings()
    email = render_verification_email(
        settings.public_base_url, TOKEN, 24
    )

    await SmtpDeliverer(settings).deliver("persona@example.com", email)

    assert attempts["count"] == 2


@pytest.mark.anyio
async def test_permanent_smtp_code_does_not_retry(monkeypatch):
    attempts = {"count": 0}

    class RejectingSmtp(FakeSmtp):
        def send_message(self, message):
            attempts["count"] += 1
            raise smtplib.SMTPResponseException(
                525, b"5.7.1 Unauthorized IP address"
            )

    monkeypatch.setattr(smtplib, "SMTP", RejectingSmtp)
    monkeypatch.setattr(delivery, "RETRY_DELAYS_SECONDS", (0, 0))
    FakeSmtp.instances = []
    settings = Settings()
    email = render_verification_email(
        settings.public_base_url, TOKEN, 24
    )

    with pytest.raises(DeliveryError) as excinfo:
        await SmtpDeliverer(settings).deliver(
            "persona@example.com", email
        )

    assert attempts["count"] == 1
    assert excinfo.value.smtp_code == 525
    assert excinfo.value.transient is False


# CHG-135 — Reply-To configurado: las respuestas van al buzón vigilado.
@pytest.mark.anyio
async def test_reply_to_header_when_configured(fake_smtp):
    settings = Settings(mail_reply_to="operador@example.com")
    email = render_verification_email(
        settings.public_base_url, TOKEN, 24
    )

    await SmtpDeliverer(settings).deliver("persona@example.com", email)

    message = fake_smtp.instances[0].messages[0]
    assert message["Reply-To"] == "operador@example.com"

    fake_smtp.instances = []
    await SmtpDeliverer(Settings()).deliver("persona@example.com", email)
    assert fake_smtp.instances[0].messages[0]["Reply-To"] is None


# CHG-135 — Los logs identifican intento, plantilla y dominio, nunca la
# dirección completa ni el cuerpo.
@pytest.mark.anyio
async def test_delivery_log_uses_domain_only(fake_smtp, caplog):
    settings = Settings()
    email = render_verification_email(
        settings.public_base_url, TOKEN, 24
    )

    with caplog.at_level("INFO", logger="mail-service.delivery"):
        await SmtpDeliverer(settings).deliver(
            "persona@example.com", email
        )

    record = caplog.records[-1].getMessage()
    assert "example.com" in record
    assert "persona@example.com" not in record
    assert "verificacion-cuenta" in record
    assert TOKEN not in record


# CHG-054 — Novedad del avance de un reporte hecho con cuenta.


def report_status_payload(**overrides):
    payload = {
        "recipient": "persona@example.com",
        "reportLabel": "Reporte de edificio sin verificar",
        "trackingCode": "BR-2026-AAAA1111",
        "statusLabel": "Fue revisado y aceptado por el equipo.",
    }
    payload.update(overrides)
    return payload


def test_report_status_email_accepted():
    deliverer = RecordingDeliverer()
    client = make_client(deliverer)

    response = client.post(
        "/internal/v1/report-status-emails", json=report_status_payload()
    )

    assert response.status_code == 202
    recipient, email = deliverer.sent[0]
    assert recipient == "persona@example.com"
    assert "BR-2026-AAAA1111" in email.subject
    assert "aceptado" in email.text_body
    assert "BR-2026-AAAA1111" in email.html_body
    assert "prioridad de revisión" in email.text_body


def test_report_status_email_invalid_recipient():
    client = make_client(RecordingDeliverer())

    response = client.post(
        "/internal/v1/report-status-emails",
        json=report_status_payload(recipient="no-es-correo"),
    )

    assert response.status_code == 422


def test_report_status_email_delivery_error_is_503():
    client = make_client(
        RecordingDeliverer(error=DeliveryError("SMTP caído"))
    )

    response = client.post(
        "/internal/v1/report-status-emails", json=report_status_payload()
    )

    assert response.status_code == 503
