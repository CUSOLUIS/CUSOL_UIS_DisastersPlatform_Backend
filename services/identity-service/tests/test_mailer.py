"""Tests del HttpMailer hacia el mail-service (CHG-043)."""

import httpx
import pytest

from app.mailer import HttpMailer, MailDeliveryUnavailable

TOKEN = "tok-0123456789abcdef0123456789abcdef"


def make_mailer(handler):
    return HttpMailer(
        "http://mail-service:8003",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.anyio
async def test_send_verification_posts_expected_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(202, json={"status": "accepted"})

    await make_mailer(handler).send_verification(
        "persona@example.com", TOKEN, 24
    )

    assert (
        seen["url"]
        == "http://mail-service:8003/internal/v1/verification-emails"
    )
    assert "persona@example.com" in seen["body"]
    assert TOKEN in seen["body"]
    assert "24" in seen["body"]


@pytest.mark.anyio
async def test_non_202_raises_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"title": "SMTP caído"})

    with pytest.raises(MailDeliveryUnavailable):
        await make_mailer(handler).send_verification(
            "persona@example.com", TOKEN, 24
        )


@pytest.mark.anyio
async def test_connection_error_raises_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin ruta al mail-service")

    with pytest.raises(MailDeliveryUnavailable):
        await make_mailer(handler).send_verification(
            "persona@example.com", TOKEN, 24
        )
