import base64
import hashlib
import json
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .models import (
    AccountRegistrationReceipt,
    AccountRole,
    AidOfferKind,
    AidOfferModerationStatus,
    AidOfferOwnerPage,
    AidOfferOwnerSummary,
    AidOfferReceipt,
    AdminVisitorPresencePage,
    VisitorPresenceReceipt,
    AdminAccountDetail,
    AdminAccountPage,
    AdminAccountStatus,
    AdminAuditPage,
    AdminEvidenceAccessGrant,
    AdminModerationStatus,
    AdminMutationReceipt,
    AdminOverview,
    AdminSubmissionDetail,
    AdminSubmissionKind,
    AdminSubmissionPage,
    AidLocationAvailability,
    AuthenticatedAccount,
    CommunityContributionReceipt,
    DisasterEventList,
    EmailVerificationEnvelope,
    EmailVerificationReceipt,
    HealthStatus,
    HumanImpactOverview,
    HumanitarianDirectoryKind,
    HumanitarianDirectorySearchResponse,
    HumanMapOverview,
    HumanStatus,
    MissingPersonReportReceipt,
    MissingPersonSearchResponse,
    OperationalMapOverview,
    PeopleRecordPage,
    PublicPersonStatus,
    SessionEnvelope,
    UnverifiedBuildingReportReceipt,
    VerificationStatus,
)
from .ratelimit import SlidingWindowRateLimiter


SESSION_COOKIE = "cusol_session"


def problem_response(
    detail: str,
    title: str = "Servicio de desastres no disponible",
    status_code: int = 503,
    problem_type: str = "upstream-service-unavailable",
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": problem_type,
            "title": title,
            "status": status_code,
            "detail": detail,
        },
    )


def rate_limited_response(detail: str) -> JSONResponse:
    return problem_response(
        detail,
        title="Límite de solicitudes excedido",
        status_code=429,
        problem_type="rate-limit-exceeded",
    )


def create_app(
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    identity_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        async with AsyncExitStack() as stack:
            if client is not None:
                application.state.upstream_client = client
            else:
                application.state.upstream_client = (
                    await stack.enter_async_context(
                        httpx.AsyncClient(
                            base_url=resolved_settings.disaster_service_url,
                            timeout=(
                                resolved_settings.upstream_timeout_seconds
                            ),
                        )
                    )
                )
            if identity_client is not None:
                application.state.identity_client = identity_client
            else:
                application.state.identity_client = (
                    await stack.enter_async_context(
                        httpx.AsyncClient(
                            base_url=resolved_settings.identity_service_url,
                            timeout=(
                                resolved_settings.upstream_timeout_seconds
                            ),
                        )
                    )
                )
            yield

    application = FastAPI(
        title="CUSOL UIS Disasters API Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CHG-022: allowlist explícita con credenciales; nunca comodín.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.allowed_origins),
        allow_credentials=True,
        # CHG-044: PATCH para la gestión de ofertas del propietario.
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )

    search_limiter = SlidingWindowRateLimiter(
        resolved_settings.search_rate_limit_per_minute
    )
    reports_limiter = SlidingWindowRateLimiter(
        resolved_settings.reports_rate_limit_per_minute
    )
    registration_limiter = SlidingWindowRateLimiter(
        resolved_settings.registration_rate_limit_per_minute
    )
    verification_limiter = SlidingWindowRateLimiter(
        resolved_settings.verification_rate_limit_per_minute
    )
    login_limiter = SlidingWindowRateLimiter(
        resolved_settings.login_rate_limit_per_minute
    )
    # CHG-034: límites separados por búsqueda, aporte anónimo y cuenta.
    directory_search_limiter = SlidingWindowRateLimiter(
        resolved_settings.directory_search_rate_limit_per_minute
    )
    anonymous_contribution_limiter = SlidingWindowRateLimiter(
        resolved_settings.anonymous_contribution_rate_limit_per_minute
    )
    account_contribution_limiter = SlidingWindowRateLimiter(
        resolved_settings.account_contribution_rate_limit_per_minute
    )
    # CHG-035: límite separado para reportes de edificio sin verificar.
    building_reports_limiter = SlidingWindowRateLimiter(
        resolved_settings.building_reports_rate_limit_per_minute
    )
    # CHG-036: límites administrativos por cuenta.
    admin_limiter = SlidingWindowRateLimiter(
        resolved_settings.admin_rate_limit_per_minute
    )
    admin_evidence_limiter = SlidingWindowRateLimiter(
        resolved_settings.admin_evidence_rate_limit_per_minute
    )
    # CHG-044: límites separados de ofertas comunitarias por cuenta.
    aid_offer_write_limiter = SlidingWindowRateLimiter(
        resolved_settings.aid_offer_write_rate_limit_per_minute
    )
    # CHG-066: presencia de visitantes.
    presence_limiter = SlidingWindowRateLimiter(
        resolved_settings.presence_rate_limit_per_minute
    )
    aid_offer_read_limiter = SlidingWindowRateLimiter(
        resolved_settings.aid_offer_read_rate_limit_per_minute
    )

    def get_client(request: Request) -> httpx.AsyncClient:
        return request.app.state.upstream_client

    def get_identity_client(request: Request) -> httpx.AsyncClient:
        return request.app.state.identity_client

    def client_key(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def auth_key(request: Request, body: bytes) -> str:
        """Origen + hash del correo normalizado, sin registrar el correo."""
        email_hash = ""
        try:
            payload = json.loads(body)
            email = payload.get("email")
            if isinstance(email, str):
                email_hash = hashlib.sha256(
                    email.strip().casefold().encode()
                ).hexdigest()[:16]
        except (ValueError, AttributeError):
            pass
        return f"{client_key(request)}:{email_hash}"

    def identity_unavailable() -> JSONResponse:
        return problem_response(
            "No fue posible procesar la solicitud de cuenta en este "
            "momento.",
            title="Servicio de identidad no disponible",
        )

    def expire_session_cookie(response: Response) -> None:
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            httponly=True,
            samesite="lax",
            secure=resolved_settings.session_cookie_secure,
        )

    def passthrough(upstream_response: httpx.Response) -> JSONResponse:
        return JSONResponse(
            status_code=upstream_response.status_code,
            media_type=upstream_response.headers.get(
                "content-type", "application/json"
            ),
            content=upstream_response.json(),
        )

    @application.get(
        "/health/live",
        response_model=HealthStatus,
        tags=["Platform"],
    )
    async def liveness() -> HealthStatus:
        return HealthStatus(status="ok", service="api-gateway")

    @application.get(
        "/health/ready",
        response_model=HealthStatus,
        responses={503: {"description": "Dependencia no disponible"}},
        tags=["Platform"],
    )
    async def readiness(
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        try:
            response = await upstream.get("/health/ready")
            response.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException):
            return problem_response("El servicio de desastres no está preparado.")
        try:
            response = await identity.get("/health/ready")
            response.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException):
            return problem_response(
                "El servicio de identidad no está preparado."
            )

        return HealthStatus(status="ok", service="api-gateway")

    @application.post(
        "/api/v1/auth/registrations",
        status_code=202,
        response_model=AccountRegistrationReceipt,
        response_model_by_alias=True,
        responses={
            422: {"description": "Campos o contraseña inválidos"},
            429: {"description": "Límite de registros excedido"},
            503: {"description": "Servicio de identidad no disponible"},
        },
        tags=["Authentication"],
    )
    async def register_account(
        request: Request,
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        body = await request.body()
        if not registration_limiter.allow(auth_key(request, body)):
            return rate_limited_response(
                "Se superó el límite de registros por minuto."
            )
        try:
            response = await identity.post(
                "/internal/v1/auth/registrations",
                content=body,
                headers={"content-type": "application/json"},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=202,
                content=AccountRegistrationReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return identity_unavailable()

    @application.post(
        "/api/v1/auth/email-verifications",
        response_model=EmailVerificationReceipt,
        response_model_by_alias=True,
        responses={
            400: {"description": "Token inválido, vencido o consumido"},
            429: {"description": "Límite de intentos excedido"},
            503: {"description": "Servicio de identidad no disponible"},
        },
        tags=["Authentication"],
    )
    async def verify_account_email(
        request: Request,
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        body = await request.body()
        if not verification_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de verificaciones por minuto."
            )
        try:
            response = await identity.post(
                "/internal/v1/auth/email-verifications",
                content=body,
                headers={"content-type": "application/json"},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            envelope = EmailVerificationEnvelope.model_validate(
                response.json()
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return identity_unavailable()

        # CHG-051: la verificación probó la propiedad del correo, así
        # que la sesión de bienvenida se materializa como cookie igual
        # que en el login; el token jamás viaja en el cuerpo público.
        public = JSONResponse(
            status_code=200,
            content=EmailVerificationReceipt(
                status=envelope.status,
                verified_at=envelope.verified_at,
                account=envelope.account,
            ).model_dump(mode="json", by_alias=True),
        )
        now = datetime.now(UTC)
        max_age = max(
            0,
            int((envelope.session_expires_at - now).total_seconds()),
        )
        public.set_cookie(
            SESSION_COOKIE,
            envelope.session_token,
            max_age=max_age,
            path="/",
            httponly=True,
            samesite="lax",
            secure=resolved_settings.session_cookie_secure,
        )
        return public

    @application.post(
        "/api/v1/auth/sessions",
        response_model=AuthenticatedAccount,
        response_model_by_alias=True,
        responses={
            401: {"description": "Correo o contraseña incorrectos"},
            429: {"description": "Límite de intentos excedido"},
            503: {"description": "Servicio de identidad no disponible"},
        },
        tags=["Authentication"],
    )
    async def create_account_session(
        request: Request,
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        body = await request.body()
        if not login_limiter.allow(auth_key(request, body)):
            return rate_limited_response(
                "Se superó el límite de intentos por minuto."
            )
        try:
            response = await identity.post(
                "/internal/v1/auth/sessions",
                content=body,
                headers={"content-type": "application/json"},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            envelope = SessionEnvelope.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return identity_unavailable()

        public = JSONResponse(
            status_code=200,
            content=envelope.account.model_dump(
                mode="json", by_alias=True
            ),
        )
        now = datetime.now(UTC)
        max_age = max(
            0,
            int((envelope.session_expires_at - now).total_seconds()),
        )
        # Cookie opaca (ADR-004): rotada en cada login.
        public.set_cookie(
            SESSION_COOKIE,
            envelope.session_token,
            max_age=max_age,
            path="/",
            httponly=True,
            samesite="lax",
            secure=resolved_settings.session_cookie_secure,
        )
        return public

    @application.delete(
        "/api/v1/auth/sessions/current",
        status_code=204,
        responses={
            403: {"description": "Origen no permitido"},
            503: {"description": "Servicio de identidad no disponible"},
        },
        tags=["Authentication"],
    )
    async def delete_current_account_session(
        request: Request,
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        # Operación autenticada que muta: validar Origin (CHG-022).
        origin = request.headers.get("origin")
        if origin is not None and origin.rstrip("/") not in (
            resolved_settings.allowed_origins
        ):
            return problem_response(
                "El origen de la solicitud no está permitido.",
                title="Origen no permitido",
                status_code=403,
                problem_type="origin-not-allowed",
            )
        token = request.cookies.get(SESSION_COOKIE)
        response = Response(status_code=204)
        if not token:
            expire_session_cookie(response)
            return response
        try:
            upstream_response = await identity.delete(
                "/internal/v1/auth/sessions/current",
                headers={"X-Session-Token": token},
            )
            upstream_response.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException):
            return identity_unavailable()
        expire_session_cookie(response)
        return response

    @application.get(
        "/api/v1/auth/me",
        response_model=AuthenticatedAccount,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            503: {"description": "Servicio de identidad no disponible"},
        },
        tags=["Authentication"],
    )
    async def get_authenticated_account(
        request: Request,
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return problem_response(
                "La sesión está ausente, vencida o revocada.",
                title="Sesión requerida",
                status_code=401,
                problem_type="session-required",
            )
        try:
            response = await identity.get(
                "/internal/v1/auth/me",
                headers={"X-Session-Token": token},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return AuthenticatedAccount.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return identity_unavailable()

    @application.get(
        "/api/v1/disasters",
        response_model=DisasterEventList,
        response_model_by_alias=True,
        responses={503: {"description": "Servicio no disponible"}},
        tags=["Disasters"],
    )
    async def list_disasters(
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        disaster_type: Annotated[
            str | None, Query(alias="disasterType", min_length=1)
        ] = None,
        verification_status: Annotated[
            Literal[
                "unverified", "under_review", "verified", "rejected"
            ]
            | None,
            Query(alias="verificationStatus"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        params: dict[str, str | int] = {"limit": limit, "offset": offset}
        if disaster_type is not None:
            params["disasterType"] = disaster_type
        if verification_status is not None:
            params["verificationStatus"] = verification_status

        try:
            response = await upstream.get(
                "/internal/v1/disasters", params=params
            )
            response.raise_for_status()
            return DisasterEventList.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar los eventos en este momento."
            )

    @application.get(
        "/api/v1/people/overview",
        response_model=HumanImpactOverview,
        response_model_by_alias=True,
        responses={503: {"description": "Servicio no disponible"}},
        tags=["People"],
    )
    async def people_overview(
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        recent_limit: Annotated[
            int, Query(alias="recentLimit", ge=10, le=50)
        ] = 10,
    ):
        try:
            response = await upstream.get(
                "/internal/v1/people/overview",
                params={"recentLimit": recent_limit},
            )
            response.raise_for_status()
            return HumanImpactOverview.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar el resumen humano en este momento.",
                title="Servicio de personas no disponible",
            )

    @application.get(
        "/api/v1/people/records",
        response_model=PeopleRecordPage,
        response_model_by_alias=True,
        responses={503: {"description": "Servicio no disponible"}},
        tags=["People"],
    )
    async def list_people_records(
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
        offset: Annotated[int, Query(ge=0)] = 0,
        statuses: Annotated[
            list[HumanStatus] | None, Query()
        ] = None,
        q: Annotated[str | None, Query(max_length=100)] = None,
    ):
        if limit not in (10, 25, 50):
            return problem_response(
                "El tamaño de página debe ser 10, 25 o 50.",
                title="Tamaño de página inválido",
                status_code=422,
                problem_type="invalid-parameters",
            )
        params: list[tuple[str, str]] = [
            ("limit", str(limit)),
            ("offset", str(offset)),
        ]
        for status in statuses or []:
            params.append(("statuses", status))
        if q is not None:
            params.append(("q", q))
        try:
            response = await upstream.get(
                "/internal/v1/people/records",
                params=params,
            )
            if response.status_code == 422:
                return passthrough(response)
            response.raise_for_status()
            return PeopleRecordPage.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar los registros de personas "
                "en este momento.",
                title="Servicio de personas no disponible",
            )

    @application.get(
        "/api/v1/people/map-overview",
        response_model=HumanMapOverview,
        response_model_by_alias=True,
        responses={503: {"description": "Servicio no disponible"}},
        tags=["People"],
    )
    async def human_map_overview(
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        west: Annotated[float, Query(ge=-180, le=180)],
        south: Annotated[float, Query(ge=-90, le=90)],
        east: Annotated[float, Query(ge=-180, le=180)],
        north: Annotated[float, Query(ge=-90, le=90)],
        zoom: Annotated[int, Query(ge=3, le=19)],
        statuses: Annotated[
            list[HumanStatus] | None, Query()
        ] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
        cursor: Annotated[str | None, Query(max_length=100)] = None,
    ):
        if west >= east or south >= north:
            return problem_response(
                "El bbox requiere west < east y south < north.",
                title="Área inválida",
                status_code=422,
                problem_type="invalid-parameters",
            )
        params: list[tuple[str, str]] = [
            ("west", str(west)),
            ("south", str(south)),
            ("east", str(east)),
            ("north", str(north)),
            ("zoom", str(zoom)),
            ("limit", str(limit)),
        ]
        for status in statuses or []:
            params.append(("statuses", status))
        if cursor is not None:
            params.append(("cursor", cursor))
        try:
            response = await upstream.get(
                "/internal/v1/people/map-overview",
                params=params,
            )
            if response.status_code == 422:
                return passthrough(response)
            response.raise_for_status()
            return HumanMapOverview.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar el mapa de situación humana "
                "en este momento.",
                title="Servicio de personas no disponible",
            )

    @application.get(
        "/api/v1/operational-map/overview",
        response_model=OperationalMapOverview,
        response_model_by_alias=True,
        responses={503: {"description": "Servicio no disponible"}},
        tags=["OperationalMap"],
    )
    async def operational_map_overview(
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ):
        try:
            response = await upstream.get(
                "/internal/v1/operational-map/overview",
                params={"limit": limit},
            )
            response.raise_for_status()
            return OperationalMapOverview.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar el mapa operativo en este momento.",
                title="Servicio del mapa operacional no disponible",
            )

    @application.get(
        "/api/v1/missing-persons/search",
        response_model=MissingPersonSearchResponse,
        response_model_by_alias=True,
        responses={
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["MissingPersons"],
    )
    async def search_missing_persons(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        q: Annotated[str, Query(min_length=2, max_length=100)],
        limit: Annotated[int, Query(ge=1, le=20)] = 10,
    ):
        if not search_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de búsquedas por minuto."
            )
        try:
            response = await upstream.get(
                "/internal/v1/missing-persons/search",
                params={"q": q, "limit": limit},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return MissingPersonSearchResponse.model_validate(
                response.json()
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar la búsqueda en este momento.",
                title="Servicio de búsqueda no disponible",
            )

    @application.post(
        "/api/v1/missing-person-reports",
        status_code=201,
        response_model=MissingPersonReportReceipt,
        response_model_by_alias=True,
        responses={
            413: {"description": "Carga demasiado grande"},
            415: {"description": "Formato no permitido"},
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite de reportes excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["MissingPersons"],
    )
    async def create_missing_person_report(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        if not reports_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de reportes por minuto."
            )

        idempotency_key = request.headers.get(
            "idempotency-key", ""
        ).strip()
        if not 16 <= len(idempotency_key) <= 128:
            return problem_response(
                "Idempotency-Key debe tener entre 16 y 128 caracteres.",
                title="Encabezado requerido",
                status_code=422,
                problem_type="validation-error",
            )

        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit():
            if int(declared) > resolved_settings.max_report_body_bytes:
                return problem_response(
                    "El envío supera el máximo total permitido.",
                    title="Carga demasiado grande",
                    status_code=413,
                    problem_type="payload-too-large",
                )

        # CHG-054: si hay sesión válida, el reporte queda vinculado a
        # la cuenta (notificaciones y prioridad); sin sesión sigue
        # siendo anónimo y el canal jamás exige autenticación.
        account = await resolve_optional_account(request, identity)
        actor_headers_optional = (
            {"x-actor-kind": "authenticated", "x-account-id": str(account.id)}
            if account is not None
            else {"x-actor-kind": "anonymous"}
        )
        body = await request.body()
        try:
            response = await upstream.post(
                "/internal/v1/missing-person-reports",
                content=body,
                headers={
                    "content-type": request.headers.get(
                        "content-type", ""
                    ),
                    "idempotency-key": idempotency_key,
                    **actor_headers_optional,
                },
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=201,
                content=MissingPersonReportReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible recibir el reporte en este momento; "
                "ningún dato quedó registrado.",
                title="Servicio de reportes no disponible",
            )

    # CHG-034 — Directorio humanitario y aportes con evidencia.

    def origin_not_allowed(request: Request) -> JSONResponse | None:
        """CSRF (CHG-022): mutaciones con cookie validan Origin."""
        origin = request.headers.get("origin")
        if origin is not None and origin.rstrip("/") not in (
            resolved_settings.allowed_origins
        ):
            return problem_response(
                "El origen de la solicitud no está permitido.",
                title="Origen no permitido",
                status_code=403,
                problem_type="origin-not-allowed",
            )
        return None

    async def resolve_optional_account(
        request: Request, identity: httpx.AsyncClient
    ) -> AuthenticatedAccount | None:
        """CHG-054: cuenta de la sesión si existe y es válida; None en
        cualquier otro caso. Jamás convierte un canal público en uno
        que exija sesión ni falla el envío por problemas de identidad."""
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        try:
            response = await identity.get(
                "/internal/v1/auth/me",
                headers={"X-Session-Token": token},
            )
            if response.status_code != 200:
                return None
            return AuthenticatedAccount.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return None

    async def resolve_account(
        request: Request, identity: httpx.AsyncClient
    ) -> AuthenticatedAccount | JSONResponse:
        """Cuenta de la sesión o 401; jamás degrada a ruta anónima."""
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return problem_response(
                "La sesión está ausente, vencida o revocada.",
                title="Sesión requerida",
                status_code=401,
                problem_type="session-required",
            )
        try:
            response = await identity.get(
                "/internal/v1/auth/me",
                headers={"X-Session-Token": token},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return AuthenticatedAccount.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return identity_unavailable()

    async def forward_contribution(
        request: Request,
        upstream: httpx.AsyncClient,
        path: str,
        actor_kind: Literal["anonymous", "authenticated"],
        unavailable_title: str,
        unavailable_detail: str,
        account_id: UUID | None = None,
    ) -> JSONResponse:
        idempotency_key = request.headers.get(
            "idempotency-key", ""
        ).strip()
        if not 16 <= len(idempotency_key) <= 128:
            return problem_response(
                "Idempotency-Key debe tener entre 16 y 128 caracteres.",
                title="Encabezado requerido",
                status_code=422,
                problem_type="validation-error",
            )
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit():
            if int(declared) > resolved_settings.max_report_body_bytes:
                return problem_response(
                    "El envío supera el máximo total permitido.",
                    title="Carga demasiado grande",
                    status_code=413,
                    problem_type="payload-too-large",
                )
        # El actor lo declara el gateway, nunca el cliente final; la
        # cookie y demás encabezados no se reenvían al upstream.
        headers = {
            "content-type": request.headers.get("content-type", ""),
            "idempotency-key": idempotency_key,
            "x-actor-kind": actor_kind,
        }
        if account_id is not None:
            headers["x-account-id"] = str(account_id)
        body = await request.body()
        try:
            response = await upstream.post(
                path, content=body, headers=headers
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=202,
                content=CommunityContributionReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                unavailable_detail, title=unavailable_title
            )

    # CHG-066 — Presencia de visitantes con consentimiento explícito.

    @application.post(
        "/api/v1/presence",
        status_code=202,
        response_model=VisitorPresenceReceipt,
        response_model_by_alias=True,
        responses={
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite de reportes excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Presence"],
    )
    async def report_visitor_presence(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        if not presence_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de reportes de presencia."
            )
        # CHG-066: la presencia en vivo exige sesión de usuario
        # registrado; los visitantes anónimos jamás reportan en vivo.
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        headers = {
            "content-type": "application/json",
            "x-actor-kind": "authenticated",
            "x-account-id": str(account.id),
        }
        body = await request.body()
        try:
            response = await upstream.post(
                "/internal/v1/presence", content=body, headers=headers
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=202,
                content=VisitorPresenceReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible registrar la presencia en este momento.",
                title="Servicio de presencia no disponible",
            )

    @application.get(
        "/api/v1/admin/visitor-presence",
        response_model=AdminVisitorPresencePage,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol insuficiente"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_visitor_presence(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
        limit: Annotated[int, Query(ge=1, le=200)] = 200,
    ):
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        return await admin_forward(
            upstream,
            "GET",
            "/internal/v1/admin/visitor-presence",
            account,
            AdminVisitorPresencePage,
            params={"limit": limit},
        )

    # CHG-036 — Consola de superadministración. Toda ruta /admin exige
    # cookie válida y rol super_admin resuelto contra identity en cada
    # solicitud; los encabezados internos de actor los escribe SOLO el
    # gateway y jamás se aceptan del cliente.

    def role_insufficient() -> JSONResponse:
        return problem_response(
            "La cuenta no tiene el rol administrativo requerido.",
            title="Rol insuficiente",
            status_code=403,
            problem_type="admin-role-required",
        )

    async def require_super_admin(
        request: Request, identity: httpx.AsyncClient
    ) -> AuthenticatedAccount | JSONResponse:
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if account.assigned_role != "super_admin":
            return role_insufficient()
        if not admin_limiter.allow(f"admin:{account.id}"):
            return rate_limited_response(
                "Se superó el límite administrativo por minuto."
            )
        return account

    def actor_headers(account: AuthenticatedAccount) -> dict[str, str]:
        return {
            "x-actor-account-id": str(account.id),
            "x-actor-role": account.assigned_role,
            "x-actor-display": base64.b64encode(
                account.display_name.encode()
            ).decode(),
        }

    def admin_unavailable() -> JSONResponse:
        return problem_response(
            "El servicio administrativo no está disponible en este "
            "momento.",
            title="Servicio administrativo no disponible",
        )

    async def admin_forward(
        client: httpx.AsyncClient,
        method: str,
        path: str,
        account: AuthenticatedAccount,
        model,
        params=None,
        body: bytes | None = None,
        success_status: int = 200,
    ):
        headers = actor_headers(account)
        if body is not None:
            headers["content-type"] = "application/json"
        try:
            response = await client.request(
                method,
                path,
                params=params,
                content=body,
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            if response.status_code == 204 or model is None:
                return Response(status_code=response.status_code)
            return JSONResponse(
                status_code=success_status,
                content=model.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return admin_unavailable()

    @application.get(
        "/api/v1/admin/overview",
        response_model=AdminOverview,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Rol insuficiente"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_overview(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        headers = actor_headers(account)
        try:
            submissions = await upstream.get(
                "/internal/v1/admin/submissions-overview",
                headers=headers,
            )
            submissions.raise_for_status()
            accounts = await identity.get(
                "/internal/v1/admin/accounts-overview",
                headers=headers,
            )
            accounts.raise_for_status()
            merged = {
                **submissions.json(),
                **accounts.json(),
            }
            return AdminOverview.model_validate(merged)
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return admin_unavailable()

    @application.get(
        "/api/v1/admin/submissions",
        response_model=AdminSubmissionPage,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol insuficiente"},
            422: {"description": "Filtros inválidos"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_list_submissions(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
        q: Annotated[
            str | None, Query(min_length=2, max_length=100)
        ] = None,
        kind: Annotated[AdminSubmissionKind | None, Query()] = None,
        status: Annotated[AdminModerationStatus | None, Query()] = None,
        received_from: Annotated[
            datetime | None, Query(alias="receivedFrom")
        ] = None,
        received_to: Annotated[
            datetime | None, Query(alias="receivedTo")
        ] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        params: list[tuple[str, str]] = [
            ("limit", str(limit)),
            ("offset", str(offset)),
        ]
        if q is not None:
            params.append(("q", q))
        if kind is not None:
            params.append(("kind", kind))
        if status is not None:
            params.append(("status", status))
        if received_from is not None:
            params.append(("receivedFrom", received_from.isoformat()))
        if received_to is not None:
            params.append(("receivedTo", received_to.isoformat()))
        return await admin_forward(
            upstream,
            "GET",
            "/internal/v1/admin/submissions",
            account,
            AdminSubmissionPage,
            params=params,
        )

    @application.get(
        "/api/v1/admin/submissions/{submission_id}",
        response_model=AdminSubmissionDetail,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol insuficiente"},
            404: {"description": "Expediente no disponible"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_get_submission(
        submission_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        return await admin_forward(
            upstream,
            "GET",
            f"/internal/v1/admin/submissions/{submission_id}",
            account,
            AdminSubmissionDetail,
        )

    async def admin_mutation(
        request: Request,
        upstream: httpx.AsyncClient,
        identity: httpx.AsyncClient,
        method: str,
        path: str,
        model,
        success_status: int = 200,
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        body = await request.body()
        return await admin_forward(
            upstream,
            method,
            path,
            account,
            model,
            body=body,
            success_status=success_status,
        )

    @application.patch(
        "/api/v1/admin/submissions/{submission_id}",
        response_model=AdminSubmissionDetail,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol u origen insuficiente"},
            404: {"description": "Expediente no disponible"},
            409: {"description": "Conflicto de versión"},
            422: {"description": "Campos inválidos"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_edit_submission(
        submission_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await admin_mutation(
            request,
            upstream,
            identity,
            "PATCH",
            f"/internal/v1/admin/submissions/{submission_id}",
            AdminSubmissionDetail,
        )

    @application.delete(
        "/api/v1/admin/submissions/{submission_id}",
        response_model=AdminMutationReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol u origen insuficiente"},
            404: {"description": "Expediente no disponible"},
            409: {"description": "Conflicto de versión o transición"},
            422: {"description": "Motivo inválido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_archive_submission(
        submission_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await admin_mutation(
            request,
            upstream,
            identity,
            "DELETE",
            f"/internal/v1/admin/submissions/{submission_id}",
            AdminMutationReceipt,
        )

    @application.post(
        "/api/v1/admin/submissions/{submission_id}/decisions",
        response_model=AdminMutationReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol u origen insuficiente"},
            404: {"description": "Expediente no disponible"},
            409: {"description": "Conflicto de versión o transición"},
            422: {"description": "Acción o motivo inválidos"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_decide_submission(
        submission_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await admin_mutation(
            request,
            upstream,
            identity,
            "POST",
            f"/internal/v1/admin/submissions/{submission_id}/decisions",
            AdminMutationReceipt,
        )

    @application.post(
        "/api/v1/admin/submissions/{submission_id}/restore",
        response_model=AdminMutationReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol u origen insuficiente"},
            404: {"description": "Expediente no disponible"},
            409: {"description": "Conflicto de versión o transición"},
            422: {"description": "Motivo inválido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_restore_submission(
        submission_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await admin_mutation(
            request,
            upstream,
            identity,
            "POST",
            f"/internal/v1/admin/submissions/{submission_id}/restore",
            AdminMutationReceipt,
        )

    @application.post(
        "/api/v1/admin/submissions/{submission_id}"
        "/evidence/{evidence_id}/access-grants",
        status_code=201,
        response_model=AdminEvidenceAccessGrant,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol u origen insuficiente"},
            404: {"description": "Evidencia no disponible"},
            429: {"description": "Límite administrativo excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_grant_evidence_access(
        submission_id: UUID,
        evidence_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if not admin_evidence_limiter.allow(f"evidence:{account.id}"):
            return rate_limited_response(
                "Se superó el límite de accesos a evidencia por minuto."
            )
        return await admin_forward(
            upstream,
            "POST",
            f"/internal/v1/admin/submissions/{submission_id}"
            f"/evidence/{evidence_id}/access-grants",
            account,
            AdminEvidenceAccessGrant,
            success_status=201,
        )

    @application.get(
        "/api/v1/admin/evidence-access/{token}",
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol insuficiente"},
            404: {"description": "Acceso inválido o vencido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_serve_evidence(
        token: str,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        try:
            response = await upstream.get(
                f"/internal/v1/admin/evidence-access/{token}",
                headers=actor_headers(account),
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return Response(
                content=response.content,
                media_type=response.headers.get(
                    "content-type", "application/octet-stream"
                ),
                headers={"Cache-Control": "no-store, private"},
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return admin_unavailable()

    @application.get(
        "/api/v1/admin/accounts",
        response_model=AdminAccountPage,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol insuficiente"},
            422: {"description": "Filtros inválidos"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_list_accounts(
        request: Request,
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
        q: Annotated[
            str | None, Query(min_length=2, max_length=100)
        ] = None,
        role: Annotated[AccountRole | None, Query()] = None,
        status: Annotated[AdminAccountStatus | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        params: list[tuple[str, str]] = [
            ("limit", str(limit)),
            ("offset", str(offset)),
        ]
        if q is not None:
            params.append(("q", q))
        if role is not None:
            params.append(("role", role))
        if status is not None:
            params.append(("status", status))
        return await admin_forward(
            identity,
            "GET",
            "/internal/v1/admin/accounts",
            account,
            AdminAccountPage,
            params=params,
        )

    @application.get(
        "/api/v1/admin/accounts/{account_id}",
        response_model=AdminAccountDetail,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol insuficiente"},
            404: {"description": "Cuenta no disponible"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_get_account(
        account_id: UUID,
        request: Request,
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        return await admin_forward(
            identity,
            "GET",
            f"/internal/v1/admin/accounts/{account_id}",
            account,
            AdminAccountDetail,
        )

    @application.patch(
        "/api/v1/admin/accounts/{account_id}",
        response_model=AdminAccountDetail,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol u origen insuficiente"},
            404: {"description": "Cuenta no disponible"},
            409: {"description": "Versión, self-action o último admin"},
            422: {"description": "Campos inválidos"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_update_account(
        account_id: UUID,
        request: Request,
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        body = await request.body()
        return await admin_forward(
            identity,
            "PATCH",
            f"/internal/v1/admin/accounts/{account_id}",
            account,
            AdminAccountDetail,
            body=body,
        )

    @application.delete(
        "/api/v1/admin/accounts/{account_id}/sessions",
        status_code=204,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol u origen insuficiente"},
            404: {"description": "Cuenta no disponible"},
            422: {"description": "Motivo inválido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_revoke_account_sessions(
        account_id: UUID,
        request: Request,
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        body = await request.body()
        return await admin_forward(
            identity,
            "DELETE",
            f"/internal/v1/admin/accounts/{account_id}/sessions",
            account,
            None,
            body=body,
        )

    @application.get(
        "/api/v1/admin/audit-events",
        response_model=AdminAuditPage,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol insuficiente"},
            422: {"description": "Filtros inválidos"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_list_audit_events(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
        q: Annotated[
            str | None, Query(min_length=2, max_length=100)
        ] = None,
        action: Annotated[str | None, Query(max_length=80)] = None,
        result: Annotated[
            Literal["success", "denied", "failed"] | None, Query()
        ] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        params: list[tuple[str, str]] = [
            ("limit", str(limit)),
            ("offset", str(offset)),
        ]
        if q is not None:
            params.append(("q", q))
        if action is not None:
            params.append(("action", action))
        if result is not None:
            params.append(("result", result))
        return await admin_forward(
            upstream,
            "GET",
            "/internal/v1/admin/audit-events",
            account,
            AdminAuditPage,
            params=params,
        )

    # CHG-035 — Reporte ciudadano de edificio sin verificar.

    @application.post(
        "/api/v1/unverified-building-reports",
        status_code=201,
        response_model=UnverifiedBuildingReportReceipt,
        response_model_by_alias=True,
        responses={
            413: {"description": "Carga demasiado grande"},
            415: {"description": "Fotografía no permitida"},
            422: {"description": "Datos o fotografías inválidos"},
            429: {"description": "Límite de reportes excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["BuildingReports"],
    )
    async def create_unverified_building_report(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        if not building_reports_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de reportes por minuto."
            )
        idempotency_key = request.headers.get(
            "idempotency-key", ""
        ).strip()
        if not 16 <= len(idempotency_key) <= 128:
            return problem_response(
                "Idempotency-Key debe tener entre 16 y 128 caracteres.",
                title="Encabezado requerido",
                status_code=422,
                problem_type="validation-error",
            )
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit():
            if int(declared) > resolved_settings.max_report_body_bytes:
                return problem_response(
                    "El envío supera el máximo total permitido.",
                    title="Carga demasiado grande",
                    status_code=413,
                    problem_type="payload-too-large",
                )
        # CHG-054: cuenta opcional de la sesión; el canal sigue siendo
        # público y jamás exige autenticación.
        account = await resolve_optional_account(request, identity)
        # Reenvío multipart en streaming: el gateway no interpreta el
        # cuerpo, no registra sus partes y no reenvía cookies.
        headers = {
            "content-type": request.headers.get("content-type", ""),
            "idempotency-key": idempotency_key,
            "x-actor-kind": (
                "authenticated" if account is not None else "anonymous"
            ),
        }
        if account is not None:
            headers["x-account-id"] = str(account.id)
        try:
            response = await upstream.post(
                "/internal/v1/unverified-building-reports",
                content=request.stream(),
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=201,
                content=UnverifiedBuildingReportReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible recibir el reporte en este momento; "
                "ningún dato quedó registrado.",
                title="Servicio de reportes no disponible",
            )

    @application.get(
        "/api/v1/humanitarian-directory/search",
        response_model=HumanitarianDirectorySearchResponse,
        response_model_by_alias=True,
        responses={
            422: {"description": "Consulta o filtros inválidos"},
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Directorio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def search_humanitarian_directory(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        kind: Annotated[HumanitarianDirectoryKind, Query()],
        q: Annotated[str, Query(min_length=2, max_length=100)],
        person_status: Annotated[
            PublicPersonStatus | None, Query(alias="personStatus")
        ] = None,
        verification_status: Annotated[
            VerificationStatus | None, Query(alias="verificationStatus")
        ] = None,
        availability_status: Annotated[
            AidLocationAvailability | None,
            Query(alias="availabilityStatus"),
        ] = None,
        open_now: Annotated[bool | None, Query(alias="openNow")] = None,
        department: Annotated[
            str | None, Query(min_length=2, max_length=100)
        ] = None,
        min_rating: Annotated[
            float | None, Query(alias="minRating", ge=1, le=5)
        ] = None,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if not directory_search_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de búsquedas por minuto."
            )
        params: list[tuple[str, str]] = [
            ("kind", kind),
            ("q", q),
            ("limit", str(limit)),
            ("offset", str(offset)),
        ]
        if person_status is not None:
            params.append(("personStatus", person_status))
        if verification_status is not None:
            params.append(("verificationStatus", verification_status))
        if availability_status is not None:
            params.append(("availabilityStatus", availability_status))
        if open_now is not None:
            params.append(("openNow", "true" if open_now else "false"))
        if department is not None:
            params.append(("department", department))
        if min_rating is not None:
            params.append(("minRating", str(min_rating)))
        try:
            response = await upstream.get(
                "/internal/v1/humanitarian-directory/search",
                params=params,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return HumanitarianDirectorySearchResponse.model_validate(
                response.json()
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar el directorio en este momento.",
                title="Directorio no disponible",
            )

    # CHG-044 — Ofertas comunitarias de comida y alojamiento. Siempre
    # autenticadas: el gateway resuelve la cuenta y la declara por
    # encabezados internos; el cuerpo del cliente jamás elige cuenta.

    async def forward_aid_offer(
        upstream: httpx.AsyncClient,
        method: str,
        path: str,
        account_id: UUID,
        model,
        params=None,
        body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
        success_status: int = 200,
    ):
        headers = {
            "x-actor-kind": "authenticated",
            "x-account-id": str(account_id),
            **(extra_headers or {}),
        }
        if body is not None:
            headers["content-type"] = "application/json"
        try:
            response = await upstream.request(
                method,
                path,
                params=params,
                content=body,
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=success_status,
                content=model.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible procesar la oferta en este momento; "
                "ningún dato quedó registrado.",
                title="Servicio de ofertas no disponible",
            )

    @application.post(
        "/api/v1/me/aid-offers",
        status_code=202,
        response_model=AidOfferReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Origen no permitido"},
            409: {"description": "Idempotency-Key con otro contenido"},
            413: {"description": "Cuerpo demasiado grande"},
            415: {"description": "Tipo de contenido no permitido"},
            422: {"description": "Oferta inválida"},
            429: {"description": "Límite de ofertas excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def create_authenticated_aid_offer(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if not aid_offer_write_limiter.allow(
            f"aid-offer:{account.id}"
        ):
            return rate_limited_response(
                "Se superó el límite de ofertas por minuto."
            )
        idempotency_key = request.headers.get(
            "idempotency-key", ""
        ).strip()
        if not 16 <= len(idempotency_key) <= 128:
            return problem_response(
                "Idempotency-Key debe tener entre 16 y 128 caracteres.",
                title="Encabezado requerido",
                status_code=422,
                problem_type="validation-error",
            )
        content_type = request.headers.get("content-type", "")
        if not content_type.strip().lower().startswith(
            "application/json"
        ):
            return problem_response(
                "El cuerpo debe ser application/json.",
                title="Tipo de contenido no permitido",
                status_code=415,
                problem_type="unsupported-media-type",
            )
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit():
            if int(declared) > resolved_settings.max_aid_offer_body_bytes:
                return problem_response(
                    "El envío supera el máximo permitido.",
                    title="Carga demasiado grande",
                    status_code=413,
                    problem_type="payload-too-large",
                )
        body = await request.body()
        if len(body) > resolved_settings.max_aid_offer_body_bytes:
            return problem_response(
                "El envío supera el máximo permitido.",
                title="Carga demasiado grande",
                status_code=413,
                problem_type="payload-too-large",
            )
        return await forward_aid_offer(
            upstream,
            "POST",
            "/internal/v1/aid-offers",
            account.id,
            AidOfferReceipt,
            body=body,
            extra_headers={"idempotency-key": idempotency_key},
            success_status=202,
        )

    @application.get(
        "/api/v1/me/aid-offers",
        response_model=AidOfferOwnerPage,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            422: {"description": "Filtros o paginación inválidos"},
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def list_my_aid_offers(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
        kind: Annotated[AidOfferKind | None, Query()] = None,
        moderation_status: Annotated[
            AidOfferModerationStatus | None,
            Query(alias="moderationStatus"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if not aid_offer_read_limiter.allow(f"aid-offer:{account.id}"):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        if limit not in (10, 25, 50):
            return problem_response(
                "El tamaño de página debe ser 10, 25 o 50.",
                title="Paginación inválida",
                status_code=422,
                problem_type="invalid-parameters",
            )
        params: list[tuple[str, str]] = [
            ("limit", str(limit)),
            ("offset", str(offset)),
        ]
        if kind is not None:
            params.append(("kind", kind))
        if moderation_status is not None:
            params.append(("moderationStatus", moderation_status))
        return await forward_aid_offer(
            upstream,
            "GET",
            "/internal/v1/aid-offers",
            account.id,
            AidOfferOwnerPage,
            params=params,
        )

    @application.patch(
        "/api/v1/me/aid-offers/{offer_id}",
        response_model=AidOfferOwnerSummary,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Origen no permitido"},
            404: {"description": "Oferta inexistente o ajena"},
            409: {"description": "Versión o transición inválidas"},
            422: {"description": "Actualización inválida"},
            429: {"description": "Límite de actualizaciones excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def update_my_aid_offer(
        offer_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if not aid_offer_write_limiter.allow(
            f"aid-offer:{account.id}"
        ):
            return rate_limited_response(
                "Se superó el límite de actualizaciones por minuto."
            )
        body = await request.body()
        return await forward_aid_offer(
            upstream,
            "PATCH",
            f"/internal/v1/aid-offers/{offer_id}",
            account.id,
            AidOfferOwnerSummary,
            body=body,
        )

    @application.post(
        "/api/v1/public/missing-persons/{person_id}/status-reports",
        status_code=202,
        response_model=CommunityContributionReceipt,
        response_model_by_alias=True,
        responses={
            404: {"description": "Persona inexistente o no publicable"},
            413: {"description": "Carga demasiado grande"},
            415: {"description": "Fotografía no permitida"},
            422: {"description": "Evidencia o datos inválidos"},
            429: {"description": "Límite de aportes excedido"},
            503: {"description": "Servicio de evidencia no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def create_anonymous_person_status_report(
        person_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
    ):
        if not anonymous_contribution_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de aportes por minuto."
            )
        return await forward_contribution(
            request,
            upstream,
            f"/internal/v1/missing-persons/{person_id}/status-reports",
            "anonymous",
            "Servicio de evidencia no disponible",
            "No fue posible recibir la novedad en este momento; "
            "ningún dato quedó registrado.",
        )

    @application.post(
        "/api/v1/me/missing-persons/{person_id}/status-reports",
        status_code=202,
        response_model=CommunityContributionReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Origen no permitido"},
            404: {"description": "Persona inexistente o no publicable"},
            413: {"description": "Carga demasiado grande"},
            415: {"description": "Fotografía no permitida"},
            422: {"description": "Evidencia o datos inválidos"},
            429: {"description": "Límite de aportes excedido"},
            503: {"description": "Servicio de evidencia no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def create_authenticated_person_status_report(
        person_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if not account_contribution_limiter.allow(
            f"account:{account.id}"
        ):
            return rate_limited_response(
                "Se superó el límite de aportes por minuto."
            )
        return await forward_contribution(
            request,
            upstream,
            f"/internal/v1/missing-persons/{person_id}/status-reports",
            "authenticated",
            "Servicio de evidencia no disponible",
            "No fue posible recibir la novedad en este momento; "
            "ningún dato quedó registrado.",
            account_id=account.id,
        )

    @application.post(
        "/api/v1/public/aid-locations/{location_id}/ratings",
        status_code=202,
        response_model=CommunityContributionReceipt,
        response_model_by_alias=True,
        responses={
            404: {"description": "Lugar inexistente o no publicable"},
            413: {"description": "Carga demasiado grande"},
            415: {"description": "Fotografía no permitida"},
            422: {"description": "Valoración inválida"},
            429: {"description": "Límite de aportes excedido"},
            503: {"description": "Servicio de valoraciones no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def create_anonymous_aid_location_rating(
        location_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
    ):
        if not anonymous_contribution_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de aportes por minuto."
            )
        return await forward_contribution(
            request,
            upstream,
            f"/internal/v1/aid-locations/{location_id}/ratings",
            "anonymous",
            "Servicio de valoraciones no disponible",
            "No fue posible recibir la valoración en este momento; "
            "ningún dato quedó registrado.",
        )

    @application.post(
        "/api/v1/me/aid-locations/{location_id}/ratings",
        status_code=202,
        response_model=CommunityContributionReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Origen no permitido"},
            404: {"description": "Lugar inexistente o no publicable"},
            413: {"description": "Carga demasiado grande"},
            415: {"description": "Fotografía no permitida"},
            422: {"description": "Valoración inválida"},
            429: {"description": "Límite de aportes excedido"},
            503: {"description": "Servicio de valoraciones no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def create_authenticated_aid_location_rating(
        location_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if not account_contribution_limiter.allow(
            f"account:{account.id}"
        ):
            return rate_limited_response(
                "Se superó el límite de aportes por minuto."
            )
        return await forward_contribution(
            request,
            upstream,
            f"/internal/v1/aid-locations/{location_id}/ratings",
            "authenticated",
            "Servicio de valoraciones no disponible",
            "No fue posible recibir la valoración en este momento; "
            "ningún dato quedó registrado.",
            account_id=account.id,
        )

    return application


app = create_app()
