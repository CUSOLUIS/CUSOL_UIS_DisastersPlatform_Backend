"""Pruebas CHG-022 — identity-service."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.repository import NewAccount
from app.security import hash_token


FAST_SETTINGS = Settings(
    database_url="postgresql://unused",
    verification_ttl_hours=24,
    session_ttl_hours=24,
    argon2_time_cost=1,
    argon2_memory_cost=8192,
    argon2_parallelism=1,
)


class FakeIdentityRepository:
    """Implementación en memoria del contrato del repositorio."""

    def __init__(self):
        self.accounts: dict[str, NewAccount] = {}
        self.status: dict[str, str] = {}
        self.tokens: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}

    async def ping(self) -> bool:
        return True

    async def create_account(self, account: NewAccount) -> bool:
        if account.email in self.accounts:
            return False
        self.accounts[account.email] = account
        self.status[account.email] = "pending_verification"
        return True

    async def create_verification_token(
        self, account_id, token_hash, expires_at
    ) -> None:
        self.tokens[token_hash] = {
            "account_id": account_id,
            "expires_at": expires_at,
            "consumed_at": None,
        }

    async def consume_verification_token(self, token_hash, now):
        token = self.tokens.get(token_hash)
        if (
            token is None
            or token["consumed_at"] is not None
            or token["expires_at"] <= now
        ):
            return None
        token["consumed_at"] = now
        for email, account in self.accounts.items():
            if account.id == token["account_id"]:
                self.status[email] = "active"
        # CHG-051: devuelve la cuenta para emitir la sesión de bienvenida.
        return token["account_id"]

    async def get_account_by_id(self, account_id):
        for email, account in self.accounts.items():
            if account.id == account_id:
                from app.repository import AccountRecord

                return AccountRecord(
                    id=account.id,
                    email=account.email,
                    first_names=account.first_names,
                    last_names=account.last_names,
                    assigned_role="user",
                    status=self.status[email],
                    password_hash=account.password_hash,
                )
        return None

    async def get_account_by_email(self, email):
        stored = self.accounts.get(email)
        if stored is None:
            return None
        from app.repository import AccountRecord

        return AccountRecord(
            id=stored.id,
            email=stored.email,
            first_names=stored.first_names,
            last_names=stored.last_names,
            assigned_role="user",
            status=self.status[email],
            password_hash=stored.password_hash,
        )

    async def create_session(
        self, account_id, token_hash, expires_at
    ) -> None:
        self.sessions[token_hash] = {
            "account_id": account_id,
            "expires_at": expires_at,
            "revoked_at": None,
        }

    async def revoke_session(self, token_hash) -> None:
        session = self.sessions.get(token_hash)
        if session and session["revoked_at"] is None:
            session["revoked_at"] = datetime.now(UTC)

    async def get_session_account(self, token_hash, now):
        session = self.sessions.get(token_hash)
        if (
            session is None
            or session["revoked_at"] is not None
            or session["expires_at"] <= now
        ):
            return None
        for email, account in self.accounts.items():
            if (
                account.id == session["account_id"]
                and self.status[email] == "active"
            ):
                from app.repository import AccountRecord, SessionAccount

                return SessionAccount(
                    account=AccountRecord(
                        id=account.id,
                        email=account.email,
                        first_names=account.first_names,
                        last_names=account.last_names,
                        assigned_role="user",
                        status="active",
                        password_hash=account.password_hash,
                        # CHG-077: misma regla que el SQL real.
                        is_health_sector=bool(
                            account.health_profession
                            and account.health_license_number
                        ),
                    ),
                    session_expires_at=session["expires_at"],
                )
        return None


class CaptureMailer:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_verification(
        self, recipient, token, expires_hours
    ) -> None:
        self.sent.append(
            {
                "recipient": recipient,
                "token": token,
                "expires_hours": expires_hours,
            }
        )

    async def send_report_status(
        self, recipient, report_label, tracking_code, status_label
    ) -> None:
        # CHG-054: novedades de reportes con cuenta.
        self.sent.append(
            {
                "recipient": recipient,
                "report_label": report_label,
                "tracking_code": tracking_code,
                "status_label": status_label,
            }
        )


def build_app(repository=None, mailer=None):
    return (
        create_app(
            settings=FAST_SETTINGS,
            repository=repository or FakeIdentityRepository(),
            mailer=mailer or CaptureMailer(),
        )
    )


async def request(app, method, path, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)


def registration_payload(**overrides) -> dict:
    payload = {
        "firstNames": "Ana",
        "lastNames": "Rojas",
        "email": "Ana.Rojas@Example.com",
        "department": "Santander",
        "municipality": "Bucaramanga",
        "requestedAccountType": "citizen",
        "password": "ClaveSegura#2026",
        "termsAccepted": True,
        "privacyAccepted": True,
        "accuracyConfirmed": True,
    }
    payload.update(overrides)
    return payload


async def register_and_activate(app, repository, mailer, **overrides):
    response = await request(
        app,
        "POST",
        "/internal/v1/auth/registrations",
        json=registration_payload(**overrides),
    )
    assert response.status_code == 202
    token = mailer.sent[-1]["token"]
    verified = await request(
        app,
        "POST",
        "/internal/v1/auth/email-verifications",
        json={"token": token},
    )
    assert verified.status_code == 200
    return token


# --- Registro ---


@pytest.mark.anyio
async def test_registration_accepts_three_account_types():
    for account_type in (
        "citizen",
        "volunteer",
        "organization_representative",
    ):
        repository = FakeIdentityRepository()
        mailer = CaptureMailer()
        app = build_app(repository, mailer)
        overrides = {"requestedAccountType": account_type}
        if account_type == "organization_representative":
            overrides["organizationName"] = "Cruz Roja Santander"
        response = await request(
            app,
            "POST",
            "/internal/v1/auth/registrations",
            json=registration_payload(**overrides),
        )
        assert response.status_code == 202, account_type
        body = response.json()
        assert body["status"] == "email_verification_required"
        assert body["assignedRole"] == "user"
        assert body["emailMasked"] == "a***@example.com"
        assert "email" not in body
        assert "token" not in response.text
        assert "password" not in response.text


@pytest.mark.anyio
async def test_registration_requires_organization_name_conditionally():
    app = build_app()

    missing = await request(
        app,
        "POST",
        "/internal/v1/auth/registrations",
        json=registration_payload(
            requestedAccountType="organization_representative"
        ),
    )
    volunteer = await request(
        app,
        "POST",
        "/internal/v1/auth/registrations",
        json=registration_payload(requestedAccountType="volunteer"),
    )

    assert missing.status_code == 422
    assert volunteer.status_code == 202


@pytest.mark.anyio
async def test_registration_normalizes_email_and_avoids_enumeration():
    repository = FakeIdentityRepository()
    mailer = CaptureMailer()
    app = build_app(repository, mailer)

    first = await request(
        app,
        "POST",
        "/internal/v1/auth/registrations",
        json=registration_payload(email="  Ana.Rojas@Example.com "),
    )
    duplicate = await request(
        app,
        "POST",
        "/internal/v1/auth/registrations",
        json=registration_payload(email="ana.rojas@example.com"),
    )

    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert len(repository.accounts) == 1
    assert "ana.rojas@example.com" in repository.accounts
    # Mismo cuerpo estructural: ninguna pista de existencia previa.
    assert set(first.json().keys()) == set(duplicate.json().keys())
    assert duplicate.json()["status"] == "email_verification_required"
    # Sin reenvío automático para la cuenta existente.
    assert len(mailer.sent) == 1


@pytest.mark.anyio
async def test_registration_rejects_each_password_rule():
    app = build_app()
    weak_passwords = [
        "Corta#1a",  # longitud
        "solo-minusculas#2026",  # sin mayúscula
        "SOLO-MAYUSCULAS#2026",  # sin minúscula
        "SinNumeros#Aqui",  # sin número
        "SinSimbolos2026Aa",  # sin símbolo
    ]
    for password in weak_passwords:
        response = await request(
            app,
            "POST",
            "/internal/v1/auth/registrations",
            json=registration_payload(password=password),
        )
        assert response.status_code == 422, password
        assert password not in response.text


@pytest.mark.anyio
async def test_registration_rejects_additional_properties():
    app = build_app()

    response = await request(
        app,
        "POST",
        "/internal/v1/auth/registrations",
        json=registration_payload(confirmPassword="ClaveSegura#2026"),
    )

    assert response.status_code == 422
    assert "ClaveSegura" not in response.text


@pytest.mark.anyio
async def test_password_stored_as_argon2id_hash_only():
    repository = FakeIdentityRepository()
    app = build_app(repository)

    await request(
        app,
        "POST",
        "/internal/v1/auth/registrations",
        json=registration_payload(),
    )

    stored = repository.accounts["ana.rojas@example.com"]
    assert stored.password_hash.startswith("$argon2id$")
    assert "ClaveSegura#2026" not in stored.password_hash


# --- Verificación de correo ---


@pytest.mark.anyio
async def test_email_verification_consumes_token_once():
    repository = FakeIdentityRepository()
    mailer = CaptureMailer()
    app = build_app(repository, mailer)

    token = await register_and_activate(app, repository, mailer)
    assert repository.status["ana.rojas@example.com"] == "active"

    reused = await request(
        app,
        "POST",
        "/internal/v1/auth/email-verifications",
        json={"token": token},
    )
    assert reused.status_code == 400


@pytest.mark.anyio
async def test_email_verification_issues_welcome_session():
    # CHG-051: verificar el correo emite la sesión de bienvenida.
    repository = FakeIdentityRepository()
    mailer = CaptureMailer()
    app = build_app(repository, mailer)

    await request(
        app,
        "POST",
        "/internal/v1/auth/registrations",
        json=registration_payload(),
    )
    token = mailer.sent[-1]["token"]
    verified = await request(
        app,
        "POST",
        "/internal/v1/auth/email-verifications",
        json={"token": token},
    )

    assert verified.status_code == 200
    body = verified.json()
    assert body["status"] == "active"
    assert body["account"]["email"] == "ana.rojas@example.com"
    assert body["account"]["status"] == "active"
    assert body["sessionToken"]
    # La sesión emitida es utilizable de inmediato.
    me = await request(
        app,
        "GET",
        "/internal/v1/auth/me",
        headers={"X-Session-Token": body["sessionToken"]},
    )
    assert me.status_code == 200
    assert me.json()["email"] == "ana.rojas@example.com"


@pytest.mark.anyio
async def test_email_verification_session_probe():
    repository = FakeIdentityRepository()
    mailer = CaptureMailer()
    app = build_app(repository, mailer)

    token = await register_and_activate(app, repository, mailer)
    assert repository.status["ana.rojas@example.com"] == "active"

    reused = await request(
        app,
        "POST",
        "/internal/v1/auth/email-verifications",
        json={"token": token},
    )
    assert reused.status_code == 400


@pytest.mark.anyio
async def test_email_verification_rejects_expired_and_random_tokens():
    repository = FakeIdentityRepository()
    mailer = CaptureMailer()
    app = build_app(repository, mailer)
    await request(
        app,
        "POST",
        "/internal/v1/auth/registrations",
        json=registration_payload(),
    )
    token = mailer.sent[-1]["token"]
    # Vencer el token manualmente.
    repository.tokens[hash_token(token)]["expires_at"] = datetime.now(
        UTC
    ) - timedelta(seconds=1)

    expired = await request(
        app,
        "POST",
        "/internal/v1/auth/email-verifications",
        json={"token": token},
    )
    random_token = await request(
        app,
        "POST",
        "/internal/v1/auth/email-verifications",
        json={"token": "x" * 43},
    )

    assert expired.status_code == 400
    assert random_token.status_code == 400


# --- Sesiones ---


@pytest.mark.anyio
async def test_login_success_returns_envelope_and_rotates_tokens():
    repository = FakeIdentityRepository()
    mailer = CaptureMailer()
    app = build_app(repository, mailer)
    await register_and_activate(app, repository, mailer)

    credentials = {
        "email": "ana.rojas@example.com",
        "password": "ClaveSegura#2026",
    }
    first = await request(
        app, "POST", "/internal/v1/auth/sessions", json=credentials
    )
    second = await request(
        app, "POST", "/internal/v1/auth/sessions", json=credentials
    )

    assert first.status_code == 200 and second.status_code == 200
    body = first.json()
    assert body["account"]["displayName"] == "Ana Rojas"
    assert body["account"]["assignedRole"] == "user"
    assert body["account"]["status"] == "active"
    # Rotación: cada login emite un token distinto.
    assert body["sessionToken"] != second.json()["sessionToken"]
    # CHG-051: la verificación del correo también emitió una sesión de
    # bienvenida, además de los dos logins.
    assert len(repository.sessions) == 3


@pytest.mark.anyio
async def test_login_generic_401_for_all_failure_modes():
    repository = FakeIdentityRepository()
    mailer = CaptureMailer()
    app = build_app(repository, mailer)
    await request(
        app,
        "POST",
        "/internal/v1/auth/registrations",
        json=registration_payload(),
    )  # cuenta pendiente, sin verificar

    unknown = await request(
        app,
        "POST",
        "/internal/v1/auth/sessions",
        json={"email": "nadie@example.com", "password": "Aa#123456789"},
    )
    wrong = await request(
        app,
        "POST",
        "/internal/v1/auth/sessions",
        json={
            "email": "ana.rojas@example.com",
            "password": "Incorrecta#2026x",
        },
    )
    pending = await request(
        app,
        "POST",
        "/internal/v1/auth/sessions",
        json={
            "email": "ana.rojas@example.com",
            "password": "ClaveSegura#2026",
        },
    )

    for response in (unknown, wrong, pending):
        assert response.status_code == 401
        assert response.json()["detail"] == (
            "Correo o contraseña incorrectos."
        )


@pytest.mark.anyio
async def test_me_valid_expired_and_revoked_sessions():
    repository = FakeIdentityRepository()
    mailer = CaptureMailer()
    app = build_app(repository, mailer)
    await register_and_activate(app, repository, mailer)
    login = await request(
        app,
        "POST",
        "/internal/v1/auth/sessions",
        json={
            "email": "ana.rojas@example.com",
            "password": "ClaveSegura#2026",
        },
    )
    token = login.json()["sessionToken"]

    valid = await request(
        app,
        "GET",
        "/internal/v1/auth/me",
        headers={"X-Session-Token": token},
    )
    assert valid.status_code == 200
    assert valid.json()["email"] == "ana.rojas@example.com"
    assert set(valid.json().keys()) == {
        "id", "displayName", "email", "assignedRole", "status",
        "sessionExpiresAt", "isHealthSector",
    }
    # CHG-077: sin datos de salud declarados, la bandera es falsa.
    assert valid.json()["isHealthSector"] is False

    # Vencida
    repository.sessions[hash_token(token)]["expires_at"] = (
        datetime.now(UTC) - timedelta(seconds=1)
    )
    expired = await request(
        app,
        "GET",
        "/internal/v1/auth/me",
        headers={"X-Session-Token": token},
    )
    assert expired.status_code == 401

    # Revocada (nueva sesión, luego logout)
    login2 = await request(
        app,
        "POST",
        "/internal/v1/auth/sessions",
        json={
            "email": "ana.rojas@example.com",
            "password": "ClaveSegura#2026",
        },
    )
    token2 = login2.json()["sessionToken"]
    logout = await request(
        app,
        "DELETE",
        "/internal/v1/auth/sessions/current",
        headers={"X-Session-Token": token2},
    )
    logout_again = await request(
        app,
        "DELETE",
        "/internal/v1/auth/sessions/current",
        headers={"X-Session-Token": token2},
    )
    revoked = await request(
        app,
        "GET",
        "/internal/v1/auth/me",
        headers={"X-Session-Token": token2},
    )
    assert logout.status_code == 204
    assert logout_again.status_code == 204  # idempotente
    assert revoked.status_code == 401

    missing = await request(app, "GET", "/internal/v1/auth/me")
    assert missing.status_code == 401


# CHG-054 — Notificación del avance de un reporte con cuenta.


@pytest.mark.anyio
async def test_report_status_notification_uses_account_email():
    repository = FakeIdentityRepository()
    mailer = CaptureMailer()
    app = build_app(repository, mailer)
    await register_and_activate(app, repository, mailer)
    account = await repository.get_account_by_email(
        "ana.rojas@example.com"
    )

    response = await request(
        app,
        "POST",
        "/internal/v1/notifications/report-status",
        json={
            "accountId": str(account.id),
            "reportLabel": "Reporte de edificio sin verificar",
            "trackingCode": "BR-2026-AAAA1111",
            "statusLabel": "Fue revisado y aceptado por el equipo.",
        },
    )

    assert response.status_code == 202
    notice = mailer.sent[-1]
    assert notice["recipient"] == "ana.rojas@example.com"
    assert notice["tracking_code"] == "BR-2026-AAAA1111"
    assert "aceptado" in notice["status_label"]


@pytest.mark.anyio
async def test_report_status_notification_unknown_account_is_404():
    repository = FakeIdentityRepository()
    mailer = CaptureMailer()
    app = build_app(repository, mailer)

    response = await request(
        app,
        "POST",
        "/internal/v1/notifications/report-status",
        json={
            "accountId": "99999999-9999-4999-8999-999999999999",
            "reportLabel": "Reporte de edificio sin verificar",
            "trackingCode": "BR-2026-AAAA1111",
            "statusLabel": "Fue revisado y aceptado por el equipo.",
        },
    )

    assert response.status_code == 404
    assert all("tracking_code" not in item for item in mailer.sent)


# --- CHG-077: cuentas del sector salud ---


@pytest.mark.anyio
async def test_registration_stores_health_sector_fields():
    repository = FakeIdentityRepository()
    mailer = CaptureMailer()
    app = build_app(repository, mailer)

    response = await request(
        app,
        "POST",
        "/internal/v1/auth/registrations",
        json=registration_payload(
            healthProfession="Médica general",
            healthLicenseNumber="RM-12345",
            healthInstitution="Hospital Universitario de Santander",
        ),
    )

    assert response.status_code == 202
    stored = repository.accounts["ana.rojas@example.com"]
    assert stored.health_profession == "Médica general"
    assert stored.health_license_number == "RM-12345"
    assert stored.health_institution == (
        "Hospital Universitario de Santander"
    )


@pytest.mark.anyio
async def test_registration_requires_health_pair():
    repository = FakeIdentityRepository()
    mailer = CaptureMailer()
    app = build_app(repository, mailer)

    missing_license = await request(
        app,
        "POST",
        "/internal/v1/auth/registrations",
        json=registration_payload(healthProfession="Enfermero"),
    )
    orphan_institution = await request(
        app,
        "POST",
        "/internal/v1/auth/registrations",
        json=registration_payload(healthInstitution="Clínica Norte"),
    )

    assert missing_license.status_code == 422
    assert orphan_institution.status_code == 422
    assert repository.accounts == {}


@pytest.mark.anyio
async def test_me_exposes_health_sector_flag():
    repository = FakeIdentityRepository()
    mailer = CaptureMailer()
    app = build_app(repository, mailer)
    await register_and_activate(
        app,
        repository,
        mailer,
        healthProfession="Médica general",
        healthLicenseNumber="RM-12345",
    )
    login = await request(
        app,
        "POST",
        "/internal/v1/auth/sessions",
        json={
            "email": "ana.rojas@example.com",
            "password": "ClaveSegura#2026",
        },
    )

    me = await request(
        app,
        "GET",
        "/internal/v1/auth/me",
        headers={"X-Session-Token": login.json()["sessionToken"]},
    )

    assert me.status_code == 200
    assert me.json()["isHealthSector"] is True
