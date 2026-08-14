"""identity-service (CHG-022 / ADR-004).

Propietario exclusivo de cuentas, verificación de correo y sesiones
opacas. Solo expone rutas internas: el api-gateway es la única frontera
pública y quien materializa la cookie `cusol_session`.

Reglas clave: mismo 202 exista o no la cuenta (anti-enumeración), 401
genérico con verificación Argon2id ficticia para correos inexistentes,
tokens solo hasheados en base y jamás en respuestas o logs.
"""

import base64
import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

import asyncpg
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from .config import Settings
from .mailer import Mailer, SmtpMailer
from .models import (
    AccountRegistrationInput,
    AccountRegistrationReceipt,
    AccountSessionInput,
    AdminAccountDetail,
    AdminAccountPage,
    AdminAccountSummary,
    AdminAccountUpdateInput,
    AdminReasonInput,
    AuthenticatedAccount,
    EmailVerificationInput,
    EmailVerificationReceipt,
    HealthStatus,
    SessionEnvelope,
)
from .repository import (
    IdentityRepository,
    NewAccount,
    PostgresIdentityRepository,
)
from .security import (
    build_password_hasher,
    generate_token,
    hash_token,
    is_valid_email,
    mask_email,
    normalize_email,
    password_policy_errors,
    verify_password,
)


def problem(status_code: int, title: str, detail: str) -> JSONResponse:
    """`application/problem+json` sin datos sensibles ni PII."""
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": title,
            "status": status_code,
            "detail": detail,
        },
    )


def invalid_credentials() -> JSONResponse:
    return problem(
        401,
        "Credenciales inválidas",
        "Correo o contraseña incorrectos.",
    )


def validation_problem(error: ValidationError) -> JSONResponse:
    # Nunca se devuelven ni registran los valores enviados.
    fields = sorted(
        {
            str(item["loc"][0]) if item["loc"] else "payload"
            for item in error.errors()
        }
    )
    return problem(
        422,
        "Datos inválidos",
        "Revisa los campos: " + ", ".join(fields) + ".",
    )


def authenticated_account(
    account, session_expires_at: datetime
) -> AuthenticatedAccount:
    return AuthenticatedAccount(
        id=account.id,
        display_name=f"{account.first_names} {account.last_names}"[:161],
        email=account.email,
        assigned_role=account.assigned_role,
        status=account.status,
        session_expires_at=session_expires_at,
    )


def create_app(
    settings: Settings | None = None,
    repository: IdentityRepository | None = None,
    mailer: Mailer | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_environment()
    password_hasher = build_password_hasher(
        resolved_settings.argon2_time_cost,
        resolved_settings.argon2_memory_cost,
        resolved_settings.argon2_parallelism,
    )
    # Hash ficticio para igualar tiempos cuando el correo no existe.
    dummy_password_hash = password_hasher.hash(
        "cusol-dummy-timing-equalizer"
    )
    outbound_mailer = mailer or SmtpMailer(
        resolved_settings.smtp_host,
        resolved_settings.smtp_port,
        resolved_settings.mail_from,
        resolved_settings.public_base_url,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if repository is not None:
            application.state.repository = repository
            yield
            return

        pool = await asyncpg.create_pool(
            resolved_settings.database_url,
            min_size=resolved_settings.database_pool_min_size,
            max_size=resolved_settings.database_pool_max_size,
            command_timeout=5,
        )
        application.state.repository = PostgresIdentityRepository(pool)
        try:
            yield
        finally:
            await pool.close()

    application = FastAPI(
        title="CUSOL UIS Identity Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    def get_repository(request: Request) -> IdentityRepository:
        return request.app.state.repository

    @application.get(
        "/health/live", response_model=HealthStatus, tags=["Platform"]
    )
    async def liveness() -> HealthStatus:
        return HealthStatus(status="ok", service="identity-service")

    @application.get(
        "/health/ready",
        response_model=HealthStatus,
        responses={503: {"description": "Base de datos no disponible"}},
        tags=["Platform"],
    )
    async def readiness(
        data: Annotated[IdentityRepository, Depends(get_repository)],
    ):
        try:
            ready = await data.ping()
        except Exception:
            ready = False
        if not ready:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "service": "identity-service",
                },
            )
        return HealthStatus(status="ok", service="identity-service")

    @application.post(
        "/internal/v1/auth/registrations",
        status_code=202,
        response_model=AccountRegistrationReceipt,
        response_model_by_alias=True,
        tags=["Authentication"],
    )
    async def register_account(
        request: Request,
        data: Annotated[IdentityRepository, Depends(get_repository)],
    ):
        try:
            payload = AccountRegistrationInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return validation_problem(error)

        email = normalize_email(payload.email)
        if not is_valid_email(email):
            return problem(
                422,
                "Datos inválidos",
                "Revisa los campos: email.",
            )
        policy_errors = password_policy_errors(payload.password)
        if policy_errors:
            return problem(
                422,
                "Contraseña insuficiente",
                "La contraseña requiere " + ", ".join(policy_errors) + ".",
            )

        # El hash se calcula siempre, exista o no la cuenta, para que la
        # latencia no revele si el correo ya estaba registrado.
        password_hash = password_hasher.hash(payload.password)
        now = datetime.now(UTC)
        expires_at = now + timedelta(
            hours=resolved_settings.verification_ttl_hours
        )
        account_id = uuid4()
        created = await data.create_account(
            NewAccount(
                id=account_id,
                email=email,
                first_names=payload.first_names.strip(),
                last_names=payload.last_names.strip(),
                phone=payload.phone,
                department=payload.department.strip(),
                municipality=payload.municipality.strip(),
                requested_account_type=payload.requested_account_type,
                organization_name=(
                    payload.organization_name.strip()
                    if payload.organization_name
                    else None
                ),
                organization_role=(
                    payload.organization_role.strip()
                    if payload.organization_role
                    else None
                ),
                password_hash=password_hash,
            )
        )
        if created:
            token = generate_token()
            await data.create_verification_token(
                account_id, hash_token(token), expires_at
            )
            try:
                await outbound_mailer.send_verification(
                    email,
                    token,
                    resolved_settings.verification_ttl_hours,
                )
            except Exception:
                # El correo de desarrollo no debe romper el alta ni
                # filtrar información; el token podrá reemitirse.
                pass
        # Cuenta existente: sin reenvío automático (anti-abuso) y con la
        # misma constancia para impedir enumeración.
        return AccountRegistrationReceipt(
            request_id=uuid4(),
            status="email_verification_required",
            email_masked=mask_email(email),
            verification_expires_at=expires_at,
            assigned_role="user",
            created_at=now,
        )

    @application.post(
        "/internal/v1/auth/email-verifications",
        response_model=EmailVerificationReceipt,
        response_model_by_alias=True,
        tags=["Authentication"],
    )
    async def verify_email(
        request: Request,
        data: Annotated[IdentityRepository, Depends(get_repository)],
    ):
        try:
            payload = EmailVerificationInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return validation_problem(error)

        verified_at = await data.consume_verification_token(
            hash_token(payload.token), datetime.now(UTC)
        )
        if verified_at is None:
            return problem(
                400,
                "Verificación no válida",
                "El token es inválido, venció o ya fue utilizado.",
            )
        return EmailVerificationReceipt(
            status="active", verified_at=verified_at
        )

    @application.post(
        "/internal/v1/auth/sessions",
        response_model=SessionEnvelope,
        response_model_by_alias=True,
        tags=["Authentication"],
    )
    async def create_session(
        request: Request,
        data: Annotated[IdentityRepository, Depends(get_repository)],
    ):
        try:
            payload = AccountSessionInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return validation_problem(error)

        email = normalize_email(payload.email)
        account = await data.get_account_by_email(email)
        if account is None:
            # Verificación ficticia para igualar tiempos.
            verify_password(
                password_hasher, dummy_password_hash, payload.password
            )
            return invalid_credentials()
        if not verify_password(
            password_hasher, account.password_hash, payload.password
        ):
            return invalid_credentials()
        if account.status != "active":
            return invalid_credentials()

        now = datetime.now(UTC)
        expires_at = now + timedelta(
            hours=resolved_settings.session_ttl_hours
        )
        token = generate_token()
        await data.create_session(
            account.id, hash_token(token), expires_at
        )
        return SessionEnvelope(
            account=authenticated_account(account, expires_at),
            session_token=token,
            session_expires_at=expires_at,
        )

    @application.delete(
        "/internal/v1/auth/sessions/current",
        status_code=204,
        tags=["Authentication"],
    )
    async def delete_current_session(
        data: Annotated[IdentityRepository, Depends(get_repository)],
        x_session_token: Annotated[
            str | None, Header(alias="X-Session-Token")
        ] = None,
    ) -> Response:
        if x_session_token:
            await data.revoke_session(hash_token(x_session_token))
        return Response(status_code=204)

    @application.get(
        "/internal/v1/auth/me",
        response_model=AuthenticatedAccount,
        response_model_by_alias=True,
        tags=["Authentication"],
    )
    async def get_me(
        data: Annotated[IdentityRepository, Depends(get_repository)],
        x_session_token: Annotated[
            str | None, Header(alias="X-Session-Token")
        ] = None,
    ):
        if not x_session_token:
            return problem(
                401,
                "Sesión requerida",
                "La sesión está ausente, vencida o revocada.",
            )
        found = await data.get_session_account(
            hash_token(x_session_token), datetime.now(UTC)
        )
        if found is None:
            return problem(
                401,
                "Sesión requerida",
                "La sesión está ausente, vencida o revocada.",
            )
        return authenticated_account(
            found.account, found.session_expires_at
        )

    # CHG-036 — Administración de cuentas (rutas internas). El gateway
    # autentica la cookie; aquí se revalida el rol de los encabezados
    # internos como defensa en profundidad.

    audit_fernet = Fernet(
        base64.urlsafe_b64encode(
            hashlib.sha256(
                resolved_settings.report_encryption_key.encode()
            ).digest()
        )
    )

    def encrypt_reason(value: str) -> bytes:
        return audit_fernet.encrypt(value.encode())

    def admin_actor(
        request: Request,
    ) -> tuple[UUID, str] | JSONResponse:
        role = request.headers.get("x-actor-role", "").strip()
        if role != "super_admin":
            return problem(
                403,
                "Rol insuficiente",
                "La operación exige rol super_admin.",
            )
        try:
            account_id = UUID(
                request.headers.get("x-actor-account-id", "").strip()
            )
        except ValueError:
            return problem(
                403, "Actor inválido", "Actor administrativo ausente."
            )
        display_raw = request.headers.get("x-actor-display", "").strip()
        try:
            display_name = base64.b64decode(
                display_raw.encode()
            ).decode() or "Superadministración"
        except Exception:
            display_name = "Superadministración"
        return account_id, display_name[:161]

    def account_summary(row: dict) -> AdminAccountSummary:
        return AdminAccountSummary(
            id=row["id"],
            display_name=(
                f"{row['first_names']} {row['last_names']}"[:161]
            ),
            email=row["email"],
            assigned_role=row["assigned_role"],
            status=row["status"],
            active_sessions=row["active_sessions"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version=row["version"],
        )

    def account_detail(row: dict) -> AdminAccountDetail:
        summary = account_summary(row)
        return AdminAccountDetail(
            **summary.model_dump(by_alias=False),
            department=row["department"],
            municipality=row["municipality"],
            requested_account_type=row["requested_account_type"],
            organization_name=row["organization_name"],
            organization_role=row["organization_role"],
        )

    def account_not_found() -> JSONResponse:
        return problem(
            404,
            "Cuenta no disponible",
            "La cuenta no existe o no es visible.",
        )

    @application.get(
        "/internal/v1/admin/accounts-overview",
        tags=["Administration"],
    )
    async def admin_accounts_overview(
        request: Request,
        data: Annotated[IdentityRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        overview = await data.admin_accounts_overview()
        return JSONResponse(
            content={
                "activeAccounts": overview["active_accounts"],
                "suspendedAccounts": overview["suspended_accounts"],
            }
        )

    @application.get(
        "/internal/v1/admin/accounts",
        response_model=AdminAccountPage,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_list_accounts(
        request: Request,
        data: Annotated[IdentityRepository, Depends(get_repository)],
        q: Annotated[
            str | None, Query(min_length=2, max_length=100)
        ] = None,
        role: Annotated[
            Literal["user", "moderator", "super_admin"] | None, Query()
        ] = None,
        status: Annotated[
            Literal["pending_verification", "active", "suspended"]
            | None,
            Query(),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        if limit not in (10, 25, 50):
            return problem(
                422,
                "Tamaño de página inválido",
                "El tamaño de página debe ser 10, 25 o 50.",
            )
        rows, total = await data.admin_list_accounts(
            q, role, status, limit, offset
        )
        return AdminAccountPage(
            items=[account_summary(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
            generated_at=datetime.now(UTC),
        )

    @application.get(
        "/internal/v1/admin/accounts/{account_id}",
        response_model=AdminAccountDetail,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_get_account(
        account_id: UUID,
        request: Request,
        data: Annotated[IdentityRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        row = await data.admin_get_account(account_id)
        if row is None:
            return account_not_found()
        return account_detail(row)

    @application.patch(
        "/internal/v1/admin/accounts/{account_id}",
        response_model=AdminAccountDetail,
        response_model_by_alias=True,
        tags=["Administration"],
    )
    async def admin_update_account(
        account_id: UUID,
        request: Request,
        data: Annotated[IdentityRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display = actor
        try:
            payload = AdminAccountUpdateInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return validation_problem(error)
        if account_id == actor_account_id:
            # Separación de funciones: nadie cambia su propio rol o
            # estado desde la consola.
            await data.admin_write_audit(
                actor_account_id,
                actor_display,
                "account_updated",
                account_id,
                "denied",
            )
            return problem(
                409,
                "Operación sobre la propia cuenta",
                "No puedes cambiar tu propio rol o estado.",
            )
        outcome, _audit_id = await data.admin_update_account(
            account_id,
            payload.expected_version,
            payload.assigned_role,
            payload.status,
            actor_account_id,
            actor_display,
            encrypt_reason(payload.reason),
        )
        if outcome == "not_found":
            return account_not_found()
        if outcome == "conflict":
            return problem(
                409,
                "Conflicto de versión",
                "La cuenta cambió; recarga y reintenta con la versión "
                "vigente.",
            )
        if outcome == "last_admin":
            return problem(
                409,
                "Último superadministrador",
                "No es posible degradar o suspender al último "
                "super_admin activo.",
            )
        row = await data.admin_get_account(account_id)
        return account_detail(row)

    @application.delete(
        "/internal/v1/admin/accounts/{account_id}/sessions",
        status_code=204,
        tags=["Administration"],
    )
    async def admin_revoke_account_sessions(
        account_id: UUID,
        request: Request,
        data: Annotated[IdentityRepository, Depends(get_repository)],
    ):
        actor = admin_actor(request)
        if isinstance(actor, JSONResponse):
            return actor
        actor_account_id, actor_display = actor
        try:
            payload = AdminReasonInput.model_validate_json(
                await request.body()
            )
        except ValidationError as error:
            return validation_problem(error)
        found, _audit_id = await data.admin_revoke_sessions(
            account_id,
            actor_account_id,
            actor_display,
            encrypt_reason(payload.reason),
        )
        if not found:
            return account_not_found()
        return Response(status_code=204)

    return application


app = create_app()
