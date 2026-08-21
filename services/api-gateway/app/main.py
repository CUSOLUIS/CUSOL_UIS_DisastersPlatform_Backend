import asyncio
import base64
import hashlib
import json
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

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
    MyReportsPage,
    VolunteerAlert,
    VolunteerAlertPage,
    VisitorPresenceReceipt,
    AdminAccountDetail,
    AdminAccountPage,
    AdminAccountStatus,
    AdminAuditPage,
    AdminEvidenceAccessGrant,
    AdminModerationStatus,
    AdminMutationReceipt,
    AdminSubmissionDeleteReceipt,
    DamagedHomeReportReceipt,
    DamagedHomePage,
    DamagedHomeComplaintReceipt,
    DamagedHomeDeleteReceipt,
    MyDamagedHomesResponse,
    HumanitarianTransportReceipt,
    ActiveTransportsResponse,
    # CHG-174: aceptación de ruta Centro Local ↔ Mulera.
    MyTransportsResponse,
    RouteAcceptanceReceipt,
    RouteCodeValidationReceipt,
    TransportCenterRequestsResponse,
    TransportRequestDecisionReceipt,
    TransportRouteCodeReceipt,
    TransportRouteStatesResponse,
    TransportCitiesResponse,
    TransportJourneyReceipt,
    AdminSubmissionTheme,
    AdminOverview,
    AdminPeoplePage,
    AdminPeopleVisibility,
    AdminPersonRecord,
    AdminSubmissionDetail,
    AdminSubmissionKind,
    AdminHelpRequestDeleteReceipt,
    AdminHelpRequestPage,
    AdminHelpRequestVolunteerPage,
    AdminPlatformResetInput,
    AdminPlatformResetReceipt,
    AdminSubmissionPage,
    AdminSystemMetrics,
    AdminAidLocationActionReceipt,
    AdminAidLocationDeleteReceipt,
    AdminAidLocationVerificationsResponse,
    AidLocationAvailability,
    AidLocationComment,
    AidLocationCommentDeleteReceipt,
    FoodOfferDeleteReceipt,
    FoodOfferReportReceipt,
    HelpRequestReportReceipt,
    AidLocationCommentsResponse,
    AidLocationParentCandidatesResponse,
    AidLocationReceipt,
    AID_LOCATION_KINDS_REQUIRING_ACCOUNT,
    AidLocationReportReceipt,
    AuthenticatedAccount,
    ChangeSignal,
    CommunityContributionReceipt,
    PersonStatusReportsPage,
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
    DisasterEventAutocompleteResponse,
    MissingPersonSearchResponse,
    PersonAutocompleteResponse,
    PersonDuplicateCheckResponse,
    OperationalMapOverview,
    PeopleRecordPage,
    GeocodeCandidate,
    GeocodeCandidateList,
    GeocodeResolvedAddress,
    PlatformVersion,
    PublicPersonStatus,
    ServiceVersion,
    SessionEnvelope,
    UnverifiedBuildingReportReceipt,
    VerificationStatus,
    HelpRequestAttendInput,
    HelpRequestAttendersPage,
    HelpRequestAttendReceipt,
    FoodOfferPage,
    FoodOfferReceipt,
    HelpRequestPage,
    HelpRequestReceipt,
)
from .geocoding import (
    TtlCache,
    shape_reverse_payload,
    shape_search_payload,
)
from .ratelimit import SlidingWindowRateLimiter
from .system_metrics import SystemMetricsSampler


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
    geocode_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_environment()

    # CHG-126: métricas del sistema para la consola admin. El anillo
    # vive en el proceso; la tarea de fondo lo alimenta cada pocos
    # segundos para que las gráficas tengan historia al primer clic.
    metrics_sampler = SystemMetricsSampler(
        history=resolved_settings.system_metrics_history_samples
    )

    async def run_metrics_sampler() -> None:
        while True:
            # La lectura de /proc es bloqueante pero mínima; se manda a
            # un hilo para no ocupar el loop si el disco está lento.
            with suppress(OSError, ValueError, IndexError):
                await asyncio.to_thread(metrics_sampler.sample)
            await asyncio.sleep(
                resolved_settings.system_metrics_sample_seconds
            )

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
            if geocode_client is not None:
                application.state.geocode_client = geocode_client
            else:
                # CHG-147: cliente propio hacia el geocodificador; sale
                # a Internet, con User-Agent identificable como pide la
                # política de uso de Nominatim.
                application.state.geocode_client = (
                    await stack.enter_async_context(
                        httpx.AsyncClient(
                            base_url=resolved_settings.geocode_base_url,
                            timeout=(
                                resolved_settings.geocode_timeout_seconds
                            ),
                            headers={
                                "User-Agent": (
                                    resolved_settings.geocode_user_agent
                                ),
                                "Accept": "application/json",
                            },
                        )
                    )
                )
            sampler_task = asyncio.create_task(run_metrics_sampler())
            try:
                yield
            finally:
                sampler_task.cancel()
                with suppress(asyncio.CancelledError):
                    await sampler_task

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
    # CHG-091: sugerencias mientras se escribe (autocomplete y chequeo
    # de duplicados comparten presupuesto por origen).
    suggestions_limiter = SlidingWindowRateLimiter(
        resolved_settings.suggestions_rate_limit_per_minute
    )
    change_signal_limiter = SlidingWindowRateLimiter(
        resolved_settings.change_signal_rate_limit_per_minute
    )
    # CHG-111: la consulta de revisión es pública y consulta al servicio
    # de desastres, así que lleva su propio presupuesto por origen.
    version_limiter = SlidingWindowRateLimiter(
        resolved_settings.version_rate_limit_per_minute
    )
    anonymous_contribution_limiter = SlidingWindowRateLimiter(
        resolved_settings.anonymous_contribution_rate_limit_per_minute
    )
    account_contribution_limiter = SlidingWindowRateLimiter(
        resolved_settings.account_contribution_rate_limit_per_minute
    )
    # CHG-035: límite separado para reportes de edificio sin verificar.
    # CHG-125: solicitudes «Necesitamos ayuda».
    help_request_limiter = SlidingWindowRateLimiter(
        resolved_settings.help_request_rate_limit_per_minute
    )
    help_request_read_limiter = SlidingWindowRateLimiter(
        resolved_settings.help_request_read_rate_limit_per_minute
    )
    help_attend_limiter = SlidingWindowRateLimiter(
        resolved_settings.help_attend_rate_limit_per_minute
    )
    # CHG-163: lectura de ofertas «Ofrecer comida».
    food_offer_read_limiter = SlidingWindowRateLimiter(
        resolved_settings.food_offer_read_rate_limit_per_minute
    )
    # CHG-171: lecturas de La Mulera y posiciones del conductor.
    transport_read_limiter = SlidingWindowRateLimiter(
        resolved_settings.transport_read_rate_limit_per_minute
    )
    transport_position_limiter = SlidingWindowRateLimiter(
        resolved_settings.transport_position_rate_limit_per_minute
    )
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
    # CHG-147: geocodificación por origen, con caché corta compartida
    # entre orígenes (la misma dirección resuelta sirve para todos).
    geocode_limiter = SlidingWindowRateLimiter(
        resolved_settings.geocode_rate_limit_per_minute
    )
    geocode_search_cache = TtlCache(
        resolved_settings.geocode_cache_seconds,
        resolved_settings.geocode_cache_max_entries,
    )
    geocode_reverse_cache = TtlCache(
        resolved_settings.geocode_cache_seconds,
        resolved_settings.geocode_cache_max_entries,
    )

    def get_client(request: Request) -> httpx.AsyncClient:
        return request.app.state.upstream_client

    def get_identity_client(request: Request) -> httpx.AsyncClient:
        return request.app.state.identity_client

    def get_geocode_client(request: Request) -> httpx.AsyncClient:
        return request.app.state.geocode_client

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

    # CHG-092 — Autocompletado creable de "Evento relacionado".
    @application.get(
        "/api/v1/disaster-events/autocomplete",
        response_model=DisasterEventAutocompleteResponse,
        response_model_by_alias=True,
        responses={
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Disasters"],
    )
    async def autocomplete_disaster_events(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        q: Annotated[str, Query(min_length=2, max_length=160)],
        limit: Annotated[int, Query(ge=1, le=10)] = 5,
    ):
        if not suggestions_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de sugerencias por minuto."
            )
        try:
            response = await upstream.get(
                "/internal/v1/disaster-events/autocomplete",
                params={"q": q, "limit": limit},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return DisasterEventAutocompleteResponse.model_validate(
                response.json()
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar los eventos en este momento.",
                title="Servicio de sugerencias no disponible",
            )

    # CHG-105 — Fotografía pública del caso: se reenvía tal cual, con
    # su tipo de contenido y su cabecera de caché.
    @application.get(
        "/api/v1/public/missing-persons/{case_id}/photo",
        responses={
            404: {"description": "Sin fotografía pública"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["MissingPersons"],
    )
    async def serve_public_person_photo(
        case_id: str,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
    ):
        try:
            response = await upstream.get(
                f"/internal/v1/public/missing-persons/{case_id}/photo",
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            from fastapi.responses import Response as RawResponse

            return RawResponse(
                content=response.content,
                media_type=response.headers.get(
                    "content-type", "image/jpeg"
                ),
                headers={
                    "Cache-Control": response.headers.get(
                        "cache-control", "public, max-age=300"
                    )
                },
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible servir la fotografía en este momento.",
                title="Fotografía no disponible",
            )

    # CHG-091 — Sugerencias en tiempo real para prevenir duplicados.
    @application.get(
        "/api/v1/persons/autocomplete",
        response_model=PersonAutocompleteResponse,
        response_model_by_alias=True,
        responses={
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["MissingPersons"],
    )
    async def autocomplete_persons(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        q: Annotated[str, Query(min_length=2, max_length=100)],
        limit: Annotated[int, Query(ge=1, le=10)] = 5,
    ):
        if not suggestions_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de sugerencias por minuto."
            )
        try:
            response = await upstream.get(
                "/internal/v1/persons/autocomplete",
                params={"q": q, "limit": limit},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return PersonAutocompleteResponse.model_validate(
                response.json()
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar las sugerencias en este momento.",
                title="Servicio de sugerencias no disponible",
            )

    @application.get(
        "/api/v1/persons/check-duplicates",
        response_model=PersonDuplicateCheckResponse,
        response_model_by_alias=True,
        responses={
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["MissingPersons"],
    )
    async def check_person_duplicates(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        first_name: Annotated[
            str, Query(alias="firstName", min_length=1, max_length=120)
        ],
        last_name: Annotated[
            str, Query(alias="lastName", max_length=120)
        ] = "",
        limit: Annotated[int, Query(ge=1, le=10)] = 5,
    ):
        if not suggestions_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de sugerencias por minuto."
            )
        try:
            response = await upstream.get(
                "/internal/v1/persons/check-duplicates",
                params={
                    "firstName": first_name,
                    "lastName": last_name,
                    "limit": limit,
                },
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return PersonDuplicateCheckResponse.model_validate(
                response.json()
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible verificar duplicados en este momento.",
                title="Servicio de sugerencias no disponible",
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
        health_sector: bool = False,
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
            # CHG-077: bandera del sector salud resuelta contra
            # identity; nunca la declara el cliente final.
            if health_sector:
                headers["x-actor-health"] = "true"
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

    # CHG-069 — "Mi espacio": reportes propios y alertas de
    # voluntariado. Siempre autenticado; reutiliza el reenviador de
    # rutas /me (encabezados de actor escritos SOLO por el gateway).

    @application.get(
        "/api/v1/me/reports",
        response_model=MyReportsPage,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["MySpace"],
    )
    async def list_my_reports(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if not aid_offer_read_limiter.allow(f"my-space:{account.id}"):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        return await forward_aid_offer(
            upstream,
            "GET",
            "/internal/v1/me/reports",
            account.id,
            MyReportsPage,
        )

    @application.post(
        "/api/v1/me/volunteer-alerts",
        status_code=201,
        response_model=VolunteerAlert,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Origen no permitido"},
            422: {"description": "Alerta inválida"},
            429: {"description": "Límite de alertas excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["MySpace"],
    )
    async def create_volunteer_alert(
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
            f"my-space:{account.id}"
        ):
            return rate_limited_response(
                "Se superó el límite de alertas por minuto."
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
            "/internal/v1/me/volunteer-alerts",
            account.id,
            VolunteerAlert,
            body=body,
            success_status=201,
        )

    @application.get(
        "/api/v1/me/volunteer-alerts",
        response_model=VolunteerAlertPage,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["MySpace"],
    )
    async def list_my_volunteer_alerts(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if not aid_offer_read_limiter.allow(f"my-space:{account.id}"):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        return await forward_aid_offer(
            upstream,
            "GET",
            "/internal/v1/me/volunteer-alerts",
            account.id,
            VolunteerAlertPage,
        )

    @application.post(
        "/api/v1/me/volunteer-alerts/{alert_id}/resolve",
        response_model=VolunteerAlert,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            404: {"description": "Alerta inexistente o ajena"},
            429: {"description": "Límite de alertas excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["MySpace"],
    )
    async def resolve_volunteer_alert(
        alert_id: UUID,
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
            f"my-space:{account.id}"
        ):
            return rate_limited_response(
                "Se superó el límite de alertas por minuto."
            )
        return await forward_aid_offer(
            upstream,
            "POST",
            f"/internal/v1/me/volunteer-alerts/{alert_id}/resolve",
            account.id,
            VolunteerAlert,
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

    # CHG-126 — Métricas del sistema donde corre el gateway (el VPS en
    # producción). Solo super_admin; la serie sale del anillo del
    # muestreador de fondo y, si aún no hay muestras, se toma una al
    # vuelo para no responder vacío.
    @application.get(
        "/api/v1/admin/system-metrics",
        response_model=AdminSystemMetrics,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol insuficiente"},
            503: {"description": "Métricas no disponibles"},
        },
        tags=["Administration"],
    )
    async def admin_system_metrics(
        request: Request,
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if not metrics_sampler.samples:
            try:
                await asyncio.to_thread(metrics_sampler.sample)
            except (OSError, ValueError, IndexError):
                return problem_response(
                    "No fue posible leer las métricas del sistema "
                    "operativo.",
                    title="Métricas no disponibles",
                )
        series = list(metrics_sampler.samples)
        return AdminSystemMetrics(
            interval_seconds=(
                resolved_settings.system_metrics_sample_seconds
            ),
            latest=series[-1],
            series=series,
            generated_at=datetime.now(UTC),
        )

    # CHG-138 — Gestión de solicitudes de ayuda desde la consola: ver
    # todo lo que llega (activas y expiradas), borrar una a una o
    # vaciarlas. Solo super_admin; las mutaciones validan Origin (CSRF).

    @application.get(
        "/api/v1/admin/help-requests",
        response_model=AdminHelpRequestPage,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol insuficiente"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_list_help_requests(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        return await admin_forward(
            upstream,
            "GET",
            "/internal/v1/admin/help-requests",
            account,
            AdminHelpRequestPage,
            params=[("limit", str(limit)), ("offset", str(offset))],
        )

    # CHG-148 — Voluntarios anónimos de una solicitud (super_admin): la
    # PII descifrada solo para la consola.
    @application.get(
        "/api/v1/admin/help-requests/{request_id}/volunteers",
        response_model=AdminHelpRequestVolunteerPage,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol insuficiente"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_list_help_request_volunteers(
        request_id: UUID,
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
            f"/internal/v1/admin/help-requests/{request_id}/volunteers",
            account,
            AdminHelpRequestVolunteerPage,
        )

    # CHG-148 — Fotografía privada del voluntario (super_admin): nunca
    # cacheada; se reenvía tal cual desde el servicio interno.
    @application.get(
        "/api/v1/admin/help-request-volunteers/{volunteer_id}/photo",
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol insuficiente"},
            404: {"description": "Sin fotografía"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_serve_volunteer_photo(
        volunteer_id: UUID,
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
                f"/internal/v1/admin/help-request-volunteers/{volunteer_id}/photo",
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
                headers={"Cache-Control": "no-store"},
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible obtener la fotografía en este momento.",
                title="Servicio de solicitudes no disponible",
            )

    @application.delete(
        "/api/v1/admin/help-requests/{request_id}",
        response_model=AdminHelpRequestDeleteReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol u origen insuficiente"},
            404: {"description": "Solicitud no encontrada"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_delete_help_request(
        request_id: UUID,
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
            f"/internal/v1/admin/help-requests/{request_id}",
            AdminHelpRequestDeleteReceipt,
        )

    @application.delete(
        "/api/v1/admin/help-requests",
        response_model=AdminHelpRequestDeleteReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol u origen insuficiente"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_purge_help_requests(
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
            "/internal/v1/admin/help-requests",
            AdminHelpRequestDeleteReceipt,
        )

    # CHG-139 — Reinicio absoluto: la operación más destructiva de la
    # plataforma. Exige super_admin, Origin válido y la frase de
    # confirmación exacta escrita por la persona. Orquesta dos pasos:
    # (1) disaster-service vacía datos+fotos+auditoría y deja el acto
    # como primer evento nuevo; (2) identity-service borra todas las
    # cuentas menos la de quien lo ordenó (su sesión sobrevive).
    @application.post(
        "/api/v1/admin/platform-reset",
        response_model=AdminPlatformResetReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol u origen insuficiente"},
            422: {"description": "Frase de confirmación ausente"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_platform_reset(
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
        try:
            AdminPlatformResetInput.model_validate_json(
                await request.body()
            )
        except ValidationError:
            return problem_response(
                "Escribe la frase de confirmación exacta para "
                "reiniciar la plataforma.",
                title="Confirmación requerida",
                status_code=422,
                problem_type="confirmation-required",
            )
        headers = actor_headers(account)
        try:
            data_response = await upstream.request(
                "POST",
                "/internal/v1/admin/platform-reset",
                headers=headers,
            )
            data_response.raise_for_status()
            accounts_response = await identity.request(
                "POST",
                "/internal/v1/admin/platform-reset",
                headers=headers,
            )
            accounts_response.raise_for_status()
        except httpx.HTTPError:
            return problem_response(
                "El reinicio no se completó en su totalidad; consulta "
                "la consola y reintenta.",
                title="Reinicio incompleto",
            )
        return AdminPlatformResetReceipt(
            tables_cleared=data_response.json().get("tablesCleared", 0),
            accounts_deleted=accounts_response.json().get(
                "accountsDeleted", 0
            ),
            generated_at=datetime.now(UTC),
        )

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
        # CHG-159: filtro por tema de la bandeja.
        theme: Annotated[AdminSubmissionTheme | None, Query()] = None,
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
        if theme is not None:
            params.append(("theme", theme))
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

    # CHG-154 — Gestión de registros de personas: listar (con ocultos),
    # ocultar (reversible, nada se borra), restaurar y editar. Solo
    # super_admin; las mutaciones validan Origin (CSRF).
    @application.get(
        "/api/v1/admin/people",
        response_model=AdminPeoplePage,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol insuficiente"},
            422: {"description": "Filtros inválidos"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_list_people(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
        statuses: Annotated[list[HumanStatus] | None, Query()] = None,
        q: Annotated[
            str | None, Query(min_length=2, max_length=100)
        ] = None,
        visibility: Annotated[AdminPeopleVisibility, Query()] = "visible",
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        account = await require_super_admin(request, identity)
        if isinstance(account, JSONResponse):
            return account
        params: list[tuple[str, str]] = [
            ("visibility", visibility),
            ("limit", str(limit)),
            ("offset", str(offset)),
        ]
        for status in statuses or []:
            params.append(("statuses", status))
        if q is not None:
            params.append(("q", q))
        return await admin_forward(
            upstream,
            "GET",
            "/internal/v1/admin/people",
            account,
            AdminPeoplePage,
            params=params,
        )

    @application.patch(
        "/api/v1/admin/people/{person_id}",
        response_model=AdminPersonRecord,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol u origen insuficiente"},
            404: {"description": "Registro no disponible"},
            409: {"description": "Estado gobernado por novedades"},
            422: {"description": "Campos inválidos"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_update_person(
        person_id: UUID,
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
            f"/internal/v1/admin/people/{person_id}",
            AdminPersonRecord,
        )

    @application.post(
        "/api/v1/admin/people/{person_id}/hide",
        response_model=AdminPersonRecord,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol u origen insuficiente"},
            404: {"description": "Registro no disponible"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_hide_person(
        person_id: UUID,
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
            f"/internal/v1/admin/people/{person_id}/hide",
            AdminPersonRecord,
        )

    @application.post(
        "/api/v1/admin/people/{person_id}/restore",
        response_model=AdminPersonRecord,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol u origen insuficiente"},
            404: {"description": "Registro no disponible"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_restore_person(
        person_id: UUID,
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
            f"/internal/v1/admin/people/{person_id}/restore",
            AdminPersonRecord,
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

    # CHG-159 — borrado definitivo de una solicitud (solo desde
    # archived/rejected; 409 en cualquier otro estado).
    @application.delete(
        "/api/v1/admin/submissions/{submission_id}/permanent",
        response_model=AdminSubmissionDeleteReceipt,
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
    async def admin_delete_submission_permanently(
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
            f"/internal/v1/admin/submissions/{submission_id}/permanent",
            AdminSubmissionDeleteReceipt,
        )

    # CHG-107: la retirada rápida de la fotografía pública (CHG-105)
    # vivía solo en el servicio interno, así que la consola no podía
    # ejecutarla: la contraparte de publicar al crear no era alcanzable.
    @application.delete(
        "/api/v1/admin/missing-persons/{case_id}/public-photo",
        responses={
            401: {"description": "Sesión requerida"},
            403: {"description": "Rol u origen insuficiente"},
            404: {"description": "El caso no tiene fotografía pública"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_withdraw_public_photo(
        case_id: UUID,
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
            f"/internal/v1/admin/missing-persons/{case_id}/public-photo",
            None,
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

    # CHG-125 — «Necesitamos ayuda»: solicitudes públicas de
    # emergencia con vigencia; la expiración vive en el
    # disaster-service (DEC-125-02).

    @application.post(
        "/api/v1/help-requests",
        status_code=201,
        response_model=HelpRequestReceipt,
        response_model_by_alias=True,
        responses={
            413: {"description": "Carga demasiado grande"},
            415: {"description": "Fotografía no permitida"},
            422: {"description": "Datos o vigencia inválidos"},
            429: {"description": "Límite de solicitudes excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HelpRequests"],
    )
    async def create_help_request(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        if not help_request_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de solicitudes por minuto."
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
        # Canal público: la cuenta es opcional y jamás se exige sesión.
        account = await resolve_optional_account(request, identity)
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
                "/internal/v1/help-requests",
                content=request.stream(),
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=201,
                content=HelpRequestReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible recibir la solicitud en este momento; "
                "ningún dato quedó registrado.",
                title="Servicio de solicitudes no disponible",
            )

    @application.get(
        "/api/v1/help-requests",
        response_model=HelpRequestPage,
        response_model_by_alias=True,
        responses={
            422: {"description": "Paginación inválida"},
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HelpRequests"],
    )
    async def list_active_help_requests(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if not help_request_read_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        if limit not in (10, 25, 50):
            return problem_response(
                "El tamaño de página debe ser 10, 25 o 50.",
                title="Tamaño de página inválido",
                status_code=422,
                problem_type="invalid-parameters",
            )
        # Con sesión válida el listado marca attendedByMe; sin sesión
        # sigue siendo el mismo canal público.
        account = await resolve_optional_account(request, identity)
        headers = {
            "x-actor-kind": (
                "authenticated" if account is not None else "anonymous"
            ),
        }
        if account is not None:
            headers["x-account-id"] = str(account.id)
        try:
            response = await upstream.get(
                "/internal/v1/help-requests",
                params={"limit": limit, "offset": offset},
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return HelpRequestPage.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar las solicitudes en este "
                "momento.",
                title="Servicio de solicitudes no disponible",
            )

    @application.post(
        "/api/v1/help-requests/{request_id}/attend",
        response_model=HelpRequestAttendReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            404: {"description": "Solicitud inexistente o expirada"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HelpRequests"],
    )
    async def attend_help_request(
        request_id: UUID,
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
        if not help_attend_limiter.allow(str(account.id)):
            return rate_limited_response(
                "Se superó el límite de acciones por minuto."
            )
        # CHG-193: el navegador solo dice si aceptó el aviso; el nombre
        # y el teléfono los pone el gateway desde la sesión, para que
        # nadie pueda figurar con datos que no son suyos. El cuerpo es
        # opcional: un cliente anterior no lo manda y no comparte nada.
        raw_body = await request.body()
        try:
            attend_input = (
                HelpRequestAttendInput.model_validate_json(raw_body)
                if raw_body
                else HelpRequestAttendInput()
            )
        except ValueError:
            return problem_response(
                "El cuerpo de la petición no es válido.",
                title="Datos inválidos",
                status_code=422,
                problem_type="validation-error",
            )
        upstream_payload: dict = {
            "sharesIdentity": attend_input.shares_identity
        }
        if attend_input.shares_identity:
            upstream_payload["name"] = account.display_name
            upstream_payload["phone"] = account.phone

        try:
            response = await upstream.post(
                f"/internal/v1/help-requests/{request_id}/attend",
                json=upstream_payload,
                headers={
                    "x-actor-kind": "authenticated",
                    "x-account-id": str(account.id),
                },
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return HelpRequestAttendReceipt.model_validate(
                response.json()
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible registrar la atención en este momento.",
                title="Servicio de solicitudes no disponible",
            )

    # CHG-193 — Quién atiende MI solicitud. Exige sesión y el servicio
    # interno comprueba además que la solicitud sea de esa cuenta: una
    # solicitud ajena responde lo mismo que una inexistente.
    @application.get(
        "/api/v1/help-requests/{request_id}/attenders",
        response_model=HelpRequestAttendersPage,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión requerida"},
            404: {"description": "Solicitud inexistente o de otra cuenta"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HelpRequests"],
    )
    async def list_help_request_attenders(
        request_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if not help_request_read_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        try:
            response = await upstream.get(
                f"/internal/v1/help-requests/{request_id}/attenders",
                headers={
                    "x-actor-kind": "authenticated",
                    "x-account-id": str(account.id),
                },
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return HelpRequestAttendersPage.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar quién atiende la solicitud en "
                "este momento.",
                title="Servicio de solicitudes no disponible",
            )

    @application.get(
        "/api/v1/help-requests/{request_id}/attenders/{attender_id}/photo",
        responses={
            401: {"description": "Sesión requerida"},
            404: {"description": "Sin fotografía compartida"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HelpRequests"],
    )
    async def serve_help_request_attender_photo(
        request_id: UUID,
        attender_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        try:
            response = await upstream.get(
                f"/internal/v1/help-requests/{request_id}/attenders/"
                f"{attender_id}/photo",
                headers={
                    "x-actor-kind": "authenticated",
                    "x-account-id": str(account.id),
                },
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return Response(
                content=response.content,
                media_type=response.headers.get(
                    "content-type", "image/jpeg"
                ),
                # Es la cara de una persona concreta, visible solo para
                # la dueña de la solicitud: no se cachea en ningún sitio.
                headers={"Cache-Control": "no-store"},
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible entregar la fotografía en este momento.",
                title="Servicio de solicitudes no disponible",
            )

    # CHG-148 — Voluntario ANÓNIMO de una solicitud: canal público (sin
    # sesión), multipart con `payload` (+ foto opcional). Quien tiene
    # cuenta usa /attend; esto es para quien no la tiene. El gateway
    # reenvía tal cual; el disaster-service cifra la PII (super_admin) y
    # aumenta el contador.
    @application.post(
        "/api/v1/help-requests/{request_id}/volunteers",
        response_model=HelpRequestAttendReceipt,
        response_model_by_alias=True,
        responses={
            404: {"description": "Solicitud inexistente o expirada"},
            413: {"description": "Carga demasiado grande"},
            415: {"description": "Fotografía no permitida"},
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HelpRequests"],
    )
    async def volunteer_for_help_request(
        request_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
        if not help_request_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de solicitudes por minuto."
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
        # Canal público: la cuenta es opcional y jamás se exige sesión.
        account = await resolve_optional_account(request, identity)
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
                f"/internal/v1/help-requests/{request_id}/volunteers",
                content=request.stream(),
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return HelpRequestAttendReceipt.model_validate(
                response.json()
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible registrar al voluntario en este momento.",
                title="Servicio de solicitudes no disponible",
            )

    # CHG-125 — Fotografía pública de la solicitud: se reenvía tal
    # cual, con su tipo de contenido y su cabecera de caché (patrón
    # CHG-105).
    @application.get(
        "/api/v1/public/help-requests/{request_id}/photo",
        responses={
            404: {"description": "Sin fotografía o solicitud expirada"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HelpRequests"],
    )
    async def serve_help_request_photo(
        request_id: str,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
    ):
        try:
            response = await upstream.get(
                f"/internal/v1/public/help-requests/{request_id}/photo"
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return Response(
                content=response.content,
                media_type=response.headers.get(
                    "content-type", "application/octet-stream"
                ),
                headers={
                    "Cache-Control": response.headers.get(
                        "cache-control", "public, max-age=300"
                    )
                },
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible obtener la fotografía en este momento.",
                title="Servicio de solicitudes no disponible",
            )

    # CHG-082 — Señal de cambios para el refresco en vivo de la
    # portada.
    @application.get(
        "/api/v1/platform/change-signal",
        response_model=ChangeSignal,
        response_model_by_alias=True,
        responses={
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Señal no disponible"},
        },
        tags=["Platform"],
    )
    async def platform_change_signal(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
    ):
        if not change_signal_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        try:
            response = await upstream.get(
                "/internal/v1/platform/change-signal"
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return ChangeSignal.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar la señal de cambios.",
                title="Señal no disponible",
            )

    # CHG-111 — Qué revisión está sirviendo la plataforma. Existe para
    # que un despliegue no pueda distinguirse de un despliegue que no
    # ocurrió: el pipeline compara esto con el commit desplegado y
    # aborta si no coinciden.
    @application.get(
        "/api/v1/platform/version",
        response_model=PlatformVersion,
        responses={429: {"description": "Límite de consultas excedido"}},
        tags=["Platform"],
    )
    async def platform_version(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
    ):
        if not version_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        detras: ServiceVersion | None = None
        try:
            response = await upstream.get("/health/version")
            response.raise_for_status()
            detras = ServiceVersion.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            detras = None
        return PlatformVersion(
            service="api-gateway",
            revision=resolved_settings.git_revision,
            upstream=detras,
        )

    # CHG-147: el navegador en producción no puede llamar directo a
    # nominatim.org (CORS/política de uso); el gateway consulta
    # server-side con User-Agent propio, caché corta y límite por
    # origen, y devuelve el JSON reducido del contrato.
    @application.get(
        "/api/v1/geocode/search",
        response_model=GeocodeCandidateList,
        responses={
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Geocodificador no disponible"},
        },
        tags=["Geocoding"],
    )
    async def search_geocode(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_geocode_client)],
        q: Annotated[str, Query(min_length=3, max_length=200)],
    ):
        if not geocode_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas de direcciones."
            )
        cache_key = " ".join(q.casefold().split())
        cached = geocode_search_cache.get(cache_key)
        if cached is not None:
            return GeocodeCandidateList(candidates=cached)
        try:
            response = await upstream.get(
                "/search",
                params={
                    "format": "jsonv2",
                    "countrycodes": "co",
                    "limit": 5,
                    "addressdetails": 0,
                    "q": q,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return problem_response(
                "El servicio de direcciones no está disponible. "
                "Intenta de nuevo.",
                title="Geocodificador no disponible",
                status_code=503,
                problem_type="geocoder-unavailable",
            )
        candidates = [
            GeocodeCandidate.model_validate(row)
            for row in shape_search_payload(payload)
        ]
        geocode_search_cache.put(cache_key, candidates)
        return GeocodeCandidateList(candidates=candidates)

    @application.get(
        "/api/v1/geocode/reverse",
        response_model=GeocodeResolvedAddress,
        responses={
            404: {"description": "Punto sin dirección conocida"},
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Geocodificador no disponible"},
        },
        tags=["Geocoding"],
    )
    async def reverse_geocode_point(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_geocode_client)],
        lat: Annotated[float, Query(ge=-90, le=90)],
        lon: Annotated[float, Query(ge=-180, le=180)],
    ):
        if not geocode_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas de direcciones."
            )
        # Redondear a ~11 m junta en la caché los micro-arrastres del
        # muñequito sin cambiar la dirección resultante (zoom=17).
        cache_key = f"{lat:.4f},{lon:.4f}"
        cache_miss = object()
        cached = geocode_reverse_cache.get(cache_key, cache_miss)
        if cached is not cache_miss:
            resolved = cached
        else:
            try:
                response = await upstream.get(
                    "/reverse",
                    params={
                        "format": "jsonv2",
                        "addressdetails": 1,
                        "zoom": 17,
                        "lat": lat,
                        "lon": lon,
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                return problem_response(
                    "El servicio de direcciones no está disponible. "
                    "Intenta de nuevo.",
                    title="Geocodificador no disponible",
                    status_code=503,
                    problem_type="geocoder-unavailable",
                )
            resolved = shape_reverse_payload(payload)
            # Los puntos sin dirección también se recuerdan: repetir el
            # toque en medio del mar no debe reconsultar a Nominatim.
            geocode_reverse_cache.put(cache_key, resolved)
        if resolved is None:
            return problem_response(
                "El punto no corresponde a una dirección conocida.",
                title="Punto sin dirección",
                status_code=404,
                problem_type="geocode-not-found",
            )
        return GeocodeResolvedAddress.model_validate(resolved)

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

    # CHG-077 — Novedades visibles al abrir la tarjeta de la persona.
    @application.get(
        "/api/v1/missing-persons/{person_id}/status-reports",
        response_model=PersonStatusReportsPage,
        response_model_by_alias=True,
        responses={
            404: {"description": "Persona inexistente o no publicable"},
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def list_person_status_reports(
        person_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        if not directory_search_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        try:
            response = await upstream.get(
                f"/internal/v1/missing-persons/{person_id}"
                "/status-reports",
                params={"limit": str(limit)},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return PersonStatusReportsPage.model_validate(
                response.json()
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar las novedades en este "
                "momento.",
                title="Novedades no disponibles",
            )

    @application.post(
        "/api/v1/public/missing-persons/{person_id}/status-reports",
        status_code=202,
        response_model=CommunityContributionReceipt,
        response_model_by_alias=True,
        responses={
            404: {"description": "Persona inexistente o no publicable"},
            409: {
                "description": "Caso verificado por el sector salud "
                "o desenlace fallecido definitivo"
            },
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
            409: {
                "description": "Caso verificado por el sector salud "
                "o desenlace fallecido definitivo"
            },
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
            health_sector=account.is_health_sector,
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

    # CHG-153 — Alta de un punto logístico. Canal público: cuenta
    # opcional (anónimos y registrados), Idempotency-Key + rate-limit.
    # La dependencia y la ciudad las valida el disaster-service.
    @application.post(
        "/api/v1/aid-locations",
        status_code=201,
        response_model=AidLocationReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Tipo que exige sesión, sin sesión"},
            413: {"description": "Carga demasiado grande"},
            422: {"description": "Datos o dependencia inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def create_aid_location(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
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
        account = await resolve_optional_account(request, identity)
        if account is not None:
            if not account_contribution_limiter.allow(
                f"account:{account.id}"
            ):
                return rate_limited_response(
                    "Se superó el límite de registros por minuto."
                )
        elif not anonymous_contribution_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de registros por minuto."
            )
        body = await request.body()
        # CHG-161 (F2) — El acopio local y el punto de distribución
        # exigen cuenta: la puerta pública lo corta aquí sin molestar al
        # servicio. Un cuerpo ilegible se deja pasar tal cual para que
        # el disaster-service conteste el 422 con el detalle de campos.
        if account is None:
            try:
                declared_kind = json.loads(body).get("kind")
            except (ValueError, AttributeError):
                declared_kind = None
            if declared_kind in AID_LOCATION_KINDS_REQUIRING_ACCOUNT:
                return problem_response(
                    "Este tipo de punto logístico exige iniciar sesión.",
                    title="Sesión requerida",
                    status_code=401,
                    problem_type="session-required",
                )
        headers = {
            "content-type": request.headers.get(
                "content-type", "application/json"
            ),
            "idempotency-key": idempotency_key,
            "x-actor-kind": (
                "authenticated" if account is not None else "anonymous"
            ),
        }
        if account is not None:
            headers["x-account-id"] = str(account.id)
        try:
            response = await upstream.post(
                "/internal/v1/aid-locations", content=body, headers=headers
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=201,
                content=AidLocationReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible registrar el punto en este momento.",
                title="Servicio no disponible",
            )

    # CHG-161 — Alta de un transporte humanitario («La mulera» /
    # «La lanchera»). SOLO con sesión: la trazabilidad exige un
    # responsable con cuenta.
    @application.post(
        "/api/v1/transports",
        status_code=201,
        response_model=HumanitarianTransportReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Origen no permitido"},
            422: {"description": "Datos, tipo o ciudad inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def create_humanitarian_transport(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
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
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if not account_contribution_limiter.allow(
            f"account:{account.id}"
        ):
            return rate_limited_response(
                "Se superó el límite de registros por minuto."
            )
        headers = {
            "content-type": request.headers.get(
                "content-type", "application/json"
            ),
            "idempotency-key": idempotency_key,
            "x-actor-kind": "authenticated",
            "x-account-id": str(account.id),
        }
        body = await request.body()
        try:
            response = await upstream.post(
                "/internal/v1/humanitarian-transports",
                content=body,
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=201,
                content=HumanitarianTransportReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible registrar el transporte en este momento.",
                title="Servicio no disponible",
            )

    # CHG-171 §50 — Catálogo de ciudades de La Mulera (público).
    @application.get(
        "/api/v1/transports/cities",
        response_model=TransportCitiesResponse,
        response_model_by_alias=True,
        responses={
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def list_transport_cities(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
    ):
        if not transport_read_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        try:
            response = await upstream.get("/internal/v1/transport-cities")
            response.raise_for_status()
            return JSONResponse(
                content=TransportCitiesResponse.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar las ciudades en este momento.",
                title="Servicio no disponible",
            )

    # CHG-171 — Feed público del mapa: viajes vivos con su rastro;
    # nunca datos del conductor (§30).
    @application.get(
        "/api/v1/transports/active",
        response_model=ActiveTransportsResponse,
        response_model_by_alias=True,
        responses={
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def list_active_transports(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
    ):
        if not transport_read_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        try:
            response = await upstream.get(
                "/internal/v1/humanitarian-transports/active"
            )
            response.raise_for_status()
            return JSONResponse(
                content=ActiveTransportsResponse.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar los viajes en este momento.",
                title="Servicio no disponible",
            )

    # CHG-171 (GPS) — Hitos y posiciones del viaje: cuenta del
    # conductor, Origin válido y limitador propio para las posiciones.
    async def transport_journey_action(
        request: Request,
        upstream: httpx.AsyncClient,
        identity: httpx.AsyncClient,
        transport_id: UUID,
        action: str,
        limiter,
        limiter_key_prefix: str,
        body: bytes | None = None,
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if not limiter.allow(f"{limiter_key_prefix}:{account.id}"):
            return rate_limited_response(
                "Se superó el límite de reportes del viaje por minuto."
            )
        headers = {
            "x-actor-kind": "authenticated",
            "x-account-id": str(account.id),
        }
        if body is not None:
            headers["content-type"] = "application/json"
        try:
            response = await upstream.post(
                f"/internal/v1/me/humanitarian-transports/{transport_id}"
                f"/{action}",
                content=body,
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                content=TransportJourneyReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible actualizar el viaje en este momento.",
                title="Servicio no disponible",
            )

    @application.post(
        "/api/v1/me/transports/{transport_id}/start",
        response_model=TransportJourneyReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Cuenta u origen sin permiso"},
            404: {"description": "Transporte inexistente"},
            409: {"description": "Estado del viaje no compatible"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def start_transport_journey(
        transport_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await transport_journey_action(
            request,
            upstream,
            identity,
            transport_id,
            "start",
            transport_read_limiter,
            "transport-journey",
        )

    @application.post(
        "/api/v1/me/transports/{transport_id}/arrive",
        response_model=TransportJourneyReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Cuenta u origen sin permiso"},
            404: {"description": "Transporte inexistente"},
            409: {"description": "Estado del viaje no compatible"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def arrive_transport_journey(
        transport_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await transport_journey_action(
            request,
            upstream,
            identity,
            transport_id,
            "arrive",
            transport_read_limiter,
            "transport-journey",
        )

    @application.post(
        "/api/v1/me/transports/{transport_id}/positions",
        response_model=TransportJourneyReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Cuenta u origen sin permiso"},
            404: {"description": "Transporte inexistente"},
            409: {"description": "Estado del viaje no compatible"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def record_transport_position(
        transport_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await transport_journey_action(
            request,
            upstream,
            identity,
            transport_id,
            "positions",
            transport_position_limiter,
            "transport-position",
            body=await request.body(),
        )

    # ------------------------------------------------------------------
    # CHG-174 — Aceptación inicial de ruta Centro Local ↔ Mulera.
    # Todo exige sesión; la autorización fina (responsable del centro o
    # dueño del transporte) la resuelve disaster-service contra la base
    # de datos, nunca contra un identificador enviado por el cliente
    # (§58-§60).
    # ------------------------------------------------------------------

    async def route_acceptance_call(
        request: Request,
        upstream: httpx.AsyncClient,
        identity: httpx.AsyncClient,
        path: str,
        model,
        *,
        method: str = "GET",
        body: bytes | None = None,
        mutation: bool = False,
    ):
        if mutation:
            forbidden = origin_not_allowed(request)
            if forbidden is not None:
                return forbidden
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if mutation and not account_contribution_limiter.allow(
            f"route-acceptance:{account.id}"
        ):
            return rate_limited_response(
                "Se superó el límite de acciones por minuto."
            )
        headers = {
            "x-actor-kind": "authenticated",
            "x-account-id": str(account.id),
            # El rol viaja para que el super_admin pueda administrar
            # cualquier centro; el responsable real se resuelve abajo.
            "x-actor-role": account.assigned_role,
        }
        if body is not None:
            headers["content-type"] = "application/json"
        try:
            if method == "POST":
                response = await upstream.post(
                    path, content=body, headers=headers
                )
            else:
                response = await upstream.get(path, headers=headers)
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                content=model.model_validate(response.json()).model_dump(
                    mode="json", by_alias=True
                ),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible completar la operación en este momento.",
                title="Servicio no disponible",
            )

    @application.get(
        "/api/v1/me/center-transport-requests",
        response_model=TransportCenterRequestsResponse,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def list_center_transport_requests(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await route_acceptance_call(
            request,
            upstream,
            identity,
            "/internal/v1/me/center-transport-requests",
            TransportCenterRequestsResponse,
        )

    @application.post(
        "/api/v1/me/center-transport-requests/{request_id}/decision",
        response_model=TransportRequestDecisionReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Centro u origen sin permiso"},
            404: {"description": "Solicitud inexistente"},
            409: {"description": "Solicitud ya procesada"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def decide_center_transport_request(
        request_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await route_acceptance_call(
            request,
            upstream,
            identity,
            f"/internal/v1/me/center-transport-requests/{request_id}"
            "/decision",
            TransportRequestDecisionReceipt,
            method="POST",
            body=await request.body(),
            mutation=True,
        )

    @application.get(
        "/api/v1/me/center-route-acceptances",
        response_model=TransportRouteStatesResponse,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def list_center_route_acceptances(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await route_acceptance_call(
            request,
            upstream,
            identity,
            "/internal/v1/me/center-route-acceptances",
            TransportRouteStatesResponse,
        )

    @application.post(
        "/api/v1/me/transports/{transport_id}/route-acceptance",
        response_model=TransportRouteCodeReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Centro u origen sin permiso"},
            404: {"description": "Transporte inexistente"},
            409: {"description": "Aceptaciones pendientes"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def start_local_route_acceptance(
        transport_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await route_acceptance_call(
            request,
            upstream,
            identity,
            f"/internal/v1/me/humanitarian-transports/{transport_id}"
            "/route-acceptance",
            TransportRouteCodeReceipt,
            method="POST",
            mutation=True,
        )

    # CHG-175 — Etapa 2: Mulera ↔ Centro de Acopio Receptor. Espejo de
    # la etapa 1, con su propio código; el orden lo impone el backend.
    @application.post(
        "/api/v1/me/transports/{transport_id}/reception-route-acceptance",
        response_model=TransportRouteCodeReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Centro u origen sin permiso"},
            404: {"description": "Transporte inexistente"},
            409: {
                "description": (
                    "Aceptaciones pendientes o etapa previa incompleta"
                )
            },
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def start_reception_route_acceptance(
        transport_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await route_acceptance_call(
            request,
            upstream,
            identity,
            f"/internal/v1/me/humanitarian-transports/{transport_id}"
            "/reception-route-acceptance",
            TransportRouteCodeReceipt,
            method="POST",
            mutation=True,
        )

    @application.post(
        "/api/v1/me/transports/{transport_id}/reception-route-code/validate",
        response_model=RouteCodeValidationReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Cuenta u origen sin permiso"},
            409: {"description": "Etapa sin código o código ya usado"},
            422: {"description": "Código inválido para esta aceptación"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def validate_reception_route_code(
        transport_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await route_acceptance_call(
            request,
            upstream,
            identity,
            f"/internal/v1/me/humanitarian-transports/{transport_id}"
            "/reception-route-code/validate",
            RouteCodeValidationReceipt,
            method="POST",
            body=await request.body(),
            mutation=True,
        )

    @application.post(
        "/api/v1/me/transports/{transport_id}/reception-route-accept",
        response_model=RouteAcceptanceReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Cuenta u origen sin permiso"},
            409: {"description": "Etapa sin código o código ya usado"},
            422: {"description": "Código inválido para esta aceptación"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def accept_reception_route_by_mule(
        transport_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await route_acceptance_call(
            request,
            upstream,
            identity,
            f"/internal/v1/me/humanitarian-transports/{transport_id}"
            "/reception-route-accept",
            RouteAcceptanceReceipt,
            method="POST",
            body=await request.body(),
            mutation=True,
        )

    @application.get(
        "/api/v1/me/transports",
        response_model=MyTransportsResponse,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def list_my_transports(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await route_acceptance_call(
            request,
            upstream,
            identity,
            "/internal/v1/me/humanitarian-transports",
            MyTransportsResponse,
        )

    @application.post(
        "/api/v1/me/transports/{transport_id}/route-code/validate",
        response_model=RouteCodeValidationReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Cuenta u origen sin permiso"},
            409: {"description": "Ruta sin código o código ya usado"},
            422: {"description": "Código inválido"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def validate_route_code(
        transport_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await route_acceptance_call(
            request,
            upstream,
            identity,
            f"/internal/v1/me/humanitarian-transports/{transport_id}"
            "/route-code/validate",
            RouteCodeValidationReceipt,
            method="POST",
            body=await request.body(),
            mutation=True,
        )

    @application.post(
        "/api/v1/me/transports/{transport_id}/route-accept",
        response_model=RouteAcceptanceReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Cuenta u origen sin permiso"},
            409: {"description": "Ruta sin código o código ya usado"},
            422: {"description": "Código inválido"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def accept_route_by_mule(
        transport_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        return await route_acceptance_call(
            request,
            upstream,
            identity,
            f"/internal/v1/me/humanitarian-transports/{transport_id}"
            "/route-accept",
            RouteAcceptanceReceipt,
            method="POST",
            body=await request.body(),
            mutation=True,
        )

    # CHG-182 — Alta de «Mi casita destruida»: SOLO con cuenta. Aquí se
    # declara un medio para recibir dinero y hay a quién avisarle de los
    # comentarios; sin cuenta no hay responsable ni destinatario.
    @application.post(
        "/api/v1/damaged-homes",
        status_code=201,
        response_model=DamagedHomeReportReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Origen no permitido"},
            413: {"description": "Carga demasiado grande"},
            415: {"description": "Fotografía no procesable"},
            422: {"description": "Informe inválido"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["BuildingReports"],
    )
    async def create_damaged_home_report(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
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
        # CHG-162 (F2) — Desde que el informe admite fotos del daño, el
        # cuerpo puede ser multipart: misma guardia de tamaño que el
        # reporte de edificio, sin interpretar las partes.
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit():
            if int(declared) > resolved_settings.max_report_body_bytes:
                return problem_response(
                    "El envío supera el máximo total permitido.",
                    title="Carga demasiado grande",
                    status_code=413,
                    problem_type="payload-too-large",
                )
        # CHG-182: la cuenta es obligatoria; el 401 sale de aquí sin
        # molestar al servicio.
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        if not account_contribution_limiter.allow(f"account:{account.id}"):
            return rate_limited_response(
                "Se superó el límite de registros por minuto."
            )
        headers = {
            "content-type": request.headers.get(
                "content-type", "application/json"
            ),
            "idempotency-key": idempotency_key,
            "x-actor-kind": "authenticated",
            "x-account-id": str(account.id),
        }
        body = await request.body()
        try:
            response = await upstream.post(
                "/internal/v1/damaged-home-reports",
                content=body,
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=201,
                content=DamagedHomeReportReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible registrar el informe en este momento.",
                title="Servicio no disponible",
            )

    # CHG-163 — «Ofrecer comida»: alta pública (anónima o con cuenta)
    # con las reglas de «Necesitamos ayuda»; sin fotos en F1, cuerpo
    # JSON como «Mi casita partida».
    @application.post(
        "/api/v1/food-offers",
        status_code=201,
        response_model=FoodOfferReceipt,
        response_model_by_alias=True,
        responses={
            422: {"description": "Oferta inválida"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["FoodOffers"],
    )
    async def create_food_offer(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
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
        account = await resolve_optional_account(request, identity)
        if account is not None:
            if not account_contribution_limiter.allow(
                f"account:{account.id}"
            ):
                return rate_limited_response(
                    "Se superó el límite de registros por minuto."
                )
        elif not anonymous_contribution_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de registros por minuto."
            )
        headers = {
            "content-type": request.headers.get(
                "content-type", "application/json"
            ),
            "idempotency-key": idempotency_key,
            "x-actor-kind": (
                "authenticated" if account is not None else "anonymous"
            ),
        }
        if account is not None:
            headers["x-account-id"] = str(account.id)
        body = await request.body()
        try:
            response = await upstream.post(
                "/internal/v1/food-offers",
                content=body,
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=201,
                content=FoodOfferReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible registrar la oferta en este momento.",
                title="Servicio no disponible",
            )

    @application.get(
        "/api/v1/food-offers",
        response_model=FoodOfferPage,
        response_model_by_alias=True,
        responses={
            422: {"description": "Paginación inválida"},
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["FoodOffers"],
    )
    async def list_active_food_offers(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if not food_offer_read_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        if limit not in (10, 25, 50):
            return problem_response(
                "El tamaño de página debe ser 10, 25 o 50.",
                title="Tamaño de página inválido",
                status_code=422,
                problem_type="invalid-parameters",
            )
        try:
            response = await upstream.get(
                "/internal/v1/food-offers",
                params={"limit": limit, "offset": offset},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return FoodOfferPage.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar las ofertas en este momento.",
                title="Servicio de ofertas no disponible",
            )

    # CHG-153 — Candidatos a centro asociado para los formularios de
    # alta de puntos dependientes. Público; comparte el limitador de
    # búsquedas del directorio.
    @application.get(
        "/api/v1/aid-locations/parent-candidates",
        response_model=AidLocationParentCandidatesResponse,
        response_model_by_alias=True,
        responses={
            422: {"description": "Tipo sin dependencia"},
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Consulta no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def list_aid_location_parent_candidates(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        kind: Annotated[str, Query(min_length=1, max_length=40)],
        municipality: Annotated[
            str, Query(min_length=1, max_length=100)
        ],
    ):
        if not directory_search_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        try:
            response = await upstream.get(
                "/internal/v1/aid-locations/parent-candidates",
                params={"kind": kind, "municipality": municipality},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                content=AidLocationParentCandidatesResponse.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible listar los centros asociados.",
                title="Servicio no disponible",
            )

    # CHG-153 — Denuncia sobre un lugar de ayuda. El gateway resuelve la
    # clave de denunciante (`x-denouncer-key`) para dedup y umbral: la
    # cuenta si hay sesión, o el fingerprint/IP hasheado si es anónimo
    # (P1: anónimos cuentan con antiabuso por fingerprint/IP).
    def _denouncer_key_anonymous(request: Request) -> str:
        fingerprint = request.headers.get(
            "x-visitor-fingerprint", ""
        ).strip()
        seed = fingerprint or client_key(request)
        digest = hashlib.sha256(seed.encode()).hexdigest()[:32]
        return f"fp:{digest}"

    async def _forward_aid_location_report(
        location_id: UUID,
        request: Request,
        upstream: httpx.AsyncClient,
        actor_kind: str,
        denouncer_key: str,
        account_id: UUID | None,
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
        headers = {
            "content-type": request.headers.get(
                "content-type", "application/json"
            ),
            "idempotency-key": idempotency_key,
            "x-actor-kind": actor_kind,
            "x-denouncer-key": denouncer_key,
        }
        if account_id is not None:
            headers["x-account-id"] = str(account_id)
        body = await request.body()
        try:
            response = await upstream.post(
                f"/internal/v1/aid-locations/{location_id}/reports",
                content=body,
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=202,
                content=AidLocationReportReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible registrar la denuncia en este momento.",
                title="Servicio no disponible",
            )

    @application.post(
        "/api/v1/public/aid-locations/{location_id}/reports",
        status_code=202,
        response_model=AidLocationReportReceipt,
        response_model_by_alias=True,
        responses={
            404: {"description": "Lugar inexistente"},
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def create_anonymous_aid_location_report(
        location_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
    ):
        if not anonymous_contribution_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de denuncias por minuto."
            )
        return await _forward_aid_location_report(
            location_id,
            request,
            upstream,
            "anonymous",
            _denouncer_key_anonymous(request),
            None,
        )

    @application.post(
        "/api/v1/me/aid-locations/{location_id}/reports",
        status_code=202,
        response_model=AidLocationReportReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Origen no permitido"},
            404: {"description": "Lugar inexistente"},
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def create_authenticated_aid_location_report(
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
        if not account_contribution_limiter.allow(f"account:{account.id}"):
            return rate_limited_response(
                "Se superó el límite de denuncias por minuto."
            )
        return await _forward_aid_location_report(
            location_id,
            request,
            upstream,
            "authenticated",
            f"account:{account.id}",
            account.id,
        )

    # CHG-165 §4-8 — Comentarios públicos de un Centro de Acopio Local:
    # los lee cualquiera; publican anónimos y cuentas por igual (sin
    # obligar a crear cuenta, §5-B).
    @application.get(
        "/api/v1/aid-locations/{location_id}/comments",
        response_model=AidLocationCommentsResponse,
        response_model_by_alias=True,
        responses={
            404: {"description": "Lugar inexistente"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def list_aid_location_comments(
        location_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ):
        if not directory_search_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        try:
            response = await upstream.get(
                f"/internal/v1/aid-locations/{location_id}/comments",
                params={"limit": limit},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return AidLocationCommentsResponse.model_validate(
                response.json()
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar los comentarios en este "
                "momento.",
                title="Servicio no disponible",
            )

    @application.post(
        "/api/v1/aid-locations/{location_id}/comments",
        status_code=201,
        response_model=AidLocationComment,
        response_model_by_alias=True,
        responses={
            404: {"description": "Lugar inexistente"},
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def create_aid_location_comment(
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
        account = await resolve_optional_account(request, identity)
        if account is not None:
            if not account_contribution_limiter.allow(
                f"account:{account.id}"
            ):
                return rate_limited_response(
                    "Se superó el límite de comentarios por minuto."
                )
        elif not anonymous_contribution_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de comentarios por minuto."
            )
        headers = {
            "content-type": request.headers.get(
                "content-type", "application/json"
            ),
            "idempotency-key": idempotency_key,
            "x-actor-kind": (
                "authenticated" if account is not None else "anonymous"
            ),
        }
        if account is not None:
            headers["x-account-id"] = str(account.id)
            # Solo el nombre público de la cuenta; jamás correo ni
            # datos privados (§5-A).
            headers["x-actor-display"] = base64.b64encode(
                account.display_name.encode()
            ).decode()
        body = await request.body()
        try:
            response = await upstream.post(
                f"/internal/v1/aid-locations/{location_id}/comments",
                content=body,
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=201,
                content=AidLocationComment.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible publicar el comentario en este momento.",
                title="Servicio no disponible",
            )

    # ------------------------------------------------------------------
    # CHG-176 — La misma comunidad para «Ofrecer comida». Autorización
    # idéntica a la de acopios: leer es público, comentar exige Origin e
    # Idempotency-Key (anónimo permitido), denunciar tiene su variante
    # anónima y con cuenta, y borrar exige super_admin.
    # ------------------------------------------------------------------

    async def _forward_food_offer_report(
        food_offer_id: UUID,
        request: Request,
        upstream: httpx.AsyncClient,
        actor_kind: str,
        denouncer_key: str,
        account_id: UUID | None,
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
        headers = {
            "content-type": request.headers.get(
                "content-type", "application/json"
            ),
            "idempotency-key": idempotency_key,
            "x-actor-kind": actor_kind,
            "x-denouncer-key": denouncer_key,
        }
        if account_id is not None:
            headers["x-account-id"] = str(account_id)
        body = await request.body()
        try:
            response = await upstream.post(
                f"/internal/v1/food-offers/{food_offer_id}/reports",
                content=body,
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=202,
                content=FoodOfferReportReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible registrar la denuncia en este momento.",
                title="Servicio no disponible",
            )

    @application.post(
        "/api/v1/public/food-offers/{food_offer_id}/reports",
        status_code=202,
        response_model=FoodOfferReportReceipt,
        response_model_by_alias=True,
        responses={
            404: {"description": "Oferta inexistente"},
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def create_anonymous_food_offer_report(
        food_offer_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
    ):
        if not anonymous_contribution_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de denuncias por minuto."
            )
        return await _forward_food_offer_report(
            food_offer_id,
            request,
            upstream,
            "anonymous",
            _denouncer_key_anonymous(request),
            None,
        )

    @application.post(
        "/api/v1/me/food-offers/{food_offer_id}/reports",
        status_code=202,
        response_model=FoodOfferReportReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Origen no permitido"},
            404: {"description": "Oferta inexistente"},
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def create_account_food_offer_report(
        food_offer_id: UUID,
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
        if not account_contribution_limiter.allow(f"account:{account.id}"):
            return rate_limited_response(
                "Se superó el límite de denuncias por minuto."
            )
        return await _forward_food_offer_report(
            food_offer_id,
            request,
            upstream,
            "authenticated",
            f"account:{account.id}",
            account.id,
        )

    @application.get(
        "/api/v1/food-offers/{food_offer_id}/comments",
        response_model=AidLocationCommentsResponse,
        response_model_by_alias=True,
        responses={
            404: {"description": "Oferta inexistente"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def list_food_offer_comments(
        food_offer_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ):
        if not directory_search_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        try:
            response = await upstream.get(
                f"/internal/v1/food-offers/{food_offer_id}/comments",
                params={"limit": limit},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return AidLocationCommentsResponse.model_validate(
                response.json()
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar los comentarios en este "
                "momento.",
                title="Servicio no disponible",
            )

    @application.post(
        "/api/v1/food-offers/{food_offer_id}/comments",
        status_code=201,
        response_model=AidLocationComment,
        response_model_by_alias=True,
        responses={
            403: {"description": "Origen no permitido"},
            404: {"description": "Oferta inexistente"},
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HumanitarianDirectory"],
    )
    async def create_food_offer_comment(
        food_offer_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
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
        account = await resolve_optional_account(request, identity)
        if account is not None:
            if not account_contribution_limiter.allow(
                f"account:{account.id}"
            ):
                return rate_limited_response(
                    "Se superó el límite de comentarios por minuto."
                )
        elif not anonymous_contribution_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de comentarios por minuto."
            )
        headers = {
            "content-type": request.headers.get(
                "content-type", "application/json"
            ),
            "idempotency-key": idempotency_key,
            "x-actor-kind": (
                "authenticated" if account is not None else "anonymous"
            ),
        }
        if account is not None:
            headers["x-account-id"] = str(account.id)
            headers["x-actor-display"] = base64.b64encode(
                account.display_name.encode()
            ).decode()
        body = await request.body()
        try:
            response = await upstream.post(
                f"/internal/v1/food-offers/{food_offer_id}/comments",
                content=body,
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=201,
                content=AidLocationComment.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible publicar el comentario en este momento.",
                title="Servicio no disponible",
            )

    @application.delete(
        "/api/v1/admin/food-offers/{food_offer_id}/comments/{comment_id}",
        response_model=AidLocationCommentDeleteReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Rol insuficiente u origen no permitido"},
            404: {"description": "Comentario inexistente"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_delete_food_offer_comment(
        food_offer_id: UUID,
        comment_id: UUID,
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
            f"/internal/v1/admin/food-offers/{food_offer_id}"
            f"/comments/{comment_id}",
            AidLocationCommentDeleteReceipt,
        )

    @application.delete(
        "/api/v1/admin/food-offers/{food_offer_id}",
        response_model=FoodOfferDeleteReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Rol insuficiente u origen no permitido"},
            404: {"description": "Oferta inexistente"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_delete_food_offer(
        food_offer_id: UUID,
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
            f"/internal/v1/admin/food-offers/{food_offer_id}",
            FoodOfferDeleteReceipt,
        )

    # ------------------------------------------------------------------
    # CHG-182 — «Mi casita destruida»: feed público del mapa, fotos
    # públicas, comunidad completa, bandeja de «Mi espacio» y borrado
    # administrativo. Misma autorización que el resto: leer es público,
    # comentar exige Origin e Idempotency-Key (anónimo permitido),
    # denunciar tiene su variante anónima y con cuenta, y borrar exige
    # super_admin. Lo único distinto es publicar: eso sí exige cuenta.
    # ------------------------------------------------------------------

    @application.get(
        "/api/v1/damaged-homes",
        response_model=DamagedHomePage,
        response_model_by_alias=True,
        responses={
            422: {"description": "Tamaño de página inválido"},
            429: {"description": "Límite de consultas excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["BuildingReports"],
    )
    async def list_active_damaged_homes(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        limit: Annotated[int, Query(ge=1, le=50)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if not directory_search_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        try:
            response = await upstream.get(
                "/internal/v1/damaged-homes",
                params={"limit": limit, "offset": offset},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return DamagedHomePage.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar las casitas en este momento.",
                title="Servicio no disponible",
            )

    @application.get(
        "/api/v1/public/damaged-homes/{damaged_home_id}/photos/{photo_id}",
        responses={
            404: {"description": "Fotografía inexistente"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["BuildingReports"],
    )
    async def serve_damaged_home_photo(
        damaged_home_id: str,
        photo_id: str,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
    ):
        try:
            response = await upstream.get(
                f"/internal/v1/public/damaged-homes/{damaged_home_id}"
                f"/photos/{photo_id}"
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return Response(
                content=response.content,
                media_type=response.headers.get(
                    "content-type", "application/octet-stream"
                ),
                headers={
                    "Cache-Control": response.headers.get(
                        "cache-control", "public, max-age=300"
                    )
                },
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible obtener la fotografía en este momento.",
                title="Servicio no disponible",
            )

    async def _forward_damaged_home_complaint(
        damaged_home_id: UUID,
        request: Request,
        upstream: httpx.AsyncClient,
        actor_kind: str,
        denouncer_key: str,
        account_id: UUID | None,
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
        headers = {
            "content-type": request.headers.get(
                "content-type", "application/json"
            ),
            "idempotency-key": idempotency_key,
            "x-actor-kind": actor_kind,
            "x-denouncer-key": denouncer_key,
        }
        if account_id is not None:
            headers["x-account-id"] = str(account_id)
        body = await request.body()
        try:
            response = await upstream.post(
                f"/internal/v1/damaged-homes/{damaged_home_id}/reports",
                content=body,
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=202,
                content=DamagedHomeComplaintReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible registrar la denuncia en este momento.",
                title="Servicio no disponible",
            )

    @application.post(
        "/api/v1/public/damaged-homes/{damaged_home_id}/reports",
        status_code=202,
        response_model=DamagedHomeComplaintReceipt,
        response_model_by_alias=True,
        responses={
            404: {"description": "Casita inexistente"},
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["BuildingReports"],
    )
    async def create_anonymous_damaged_home_complaint(
        damaged_home_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
    ):
        if not anonymous_contribution_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de denuncias por minuto."
            )
        return await _forward_damaged_home_complaint(
            damaged_home_id,
            request,
            upstream,
            "anonymous",
            _denouncer_key_anonymous(request),
            None,
        )

    @application.post(
        "/api/v1/me/damaged-homes/{damaged_home_id}/reports",
        status_code=202,
        response_model=DamagedHomeComplaintReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Origen no permitido"},
            404: {"description": "Casita inexistente"},
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["BuildingReports"],
    )
    async def create_account_damaged_home_complaint(
        damaged_home_id: UUID,
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
        if not account_contribution_limiter.allow(f"account:{account.id}"):
            return rate_limited_response(
                "Se superó el límite de denuncias por minuto."
            )
        return await _forward_damaged_home_complaint(
            damaged_home_id,
            request,
            upstream,
            "authenticated",
            f"account:{account.id}",
            account.id,
        )

    @application.get(
        "/api/v1/damaged-homes/{damaged_home_id}/comments",
        response_model=AidLocationCommentsResponse,
        response_model_by_alias=True,
        responses={
            404: {"description": "Casita inexistente"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["BuildingReports"],
    )
    async def list_damaged_home_comments(
        damaged_home_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ):
        if not directory_search_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        try:
            response = await upstream.get(
                f"/internal/v1/damaged-homes/{damaged_home_id}/comments",
                params={"limit": limit},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return AidLocationCommentsResponse.model_validate(
                response.json()
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar los comentarios en este "
                "momento.",
                title="Servicio no disponible",
            )

    @application.post(
        "/api/v1/damaged-homes/{damaged_home_id}/comments",
        status_code=201,
        response_model=AidLocationComment,
        response_model_by_alias=True,
        responses={
            403: {"description": "Origen no permitido"},
            404: {"description": "Casita inexistente"},
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["BuildingReports"],
    )
    async def create_damaged_home_comment(
        damaged_home_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
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
        account = await resolve_optional_account(request, identity)
        if account is not None:
            if not account_contribution_limiter.allow(
                f"account:{account.id}"
            ):
                return rate_limited_response(
                    "Se superó el límite de comentarios por minuto."
                )
        elif not anonymous_contribution_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de comentarios por minuto."
            )
        headers = {
            "content-type": request.headers.get(
                "content-type", "application/json"
            ),
            "idempotency-key": idempotency_key,
            "x-actor-kind": (
                "authenticated" if account is not None else "anonymous"
            ),
        }
        if account is not None:
            headers["x-account-id"] = str(account.id)
            headers["x-actor-display"] = base64.b64encode(
                account.display_name.encode()
            ).decode()
        body = await request.body()
        try:
            response = await upstream.post(
                f"/internal/v1/damaged-homes/{damaged_home_id}/comments",
                content=body,
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=201,
                content=AidLocationComment.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible publicar el comentario en este momento.",
                title="Servicio no disponible",
            )

    @application.get(
        "/api/v1/me/damaged-homes",
        response_model=MyDamagedHomesResponse,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["BuildingReports"],
    )
    async def list_my_damaged_homes(
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        account = await resolve_account(request, identity)
        if isinstance(account, JSONResponse):
            return account
        try:
            response = await upstream.get(
                "/internal/v1/me/damaged-homes",
                headers={
                    "x-actor-kind": "authenticated",
                    "x-account-id": str(account.id),
                },
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return MyDamagedHomesResponse.model_validate(response.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar tus casitas en este momento.",
                title="Servicio no disponible",
            )

    @application.post(
        "/api/v1/me/damaged-homes/{damaged_home_id}/comments-seen",
        status_code=204,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Origen no permitido"},
            404: {"description": "Casita inexistente o ajena"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["BuildingReports"],
    )
    async def mark_damaged_home_comments_seen(
        damaged_home_id: UUID,
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
        try:
            response = await upstream.post(
                f"/internal/v1/me/damaged-homes/{damaged_home_id}"
                "/comments-seen",
                headers={
                    "x-actor-kind": "authenticated",
                    "x-account-id": str(account.id),
                },
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return Response(status_code=204)
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible marcar los comentarios como leídos.",
                title="Servicio no disponible",
            )

    @application.delete(
        "/api/v1/admin/damaged-homes/{damaged_home_id}/comments"
        "/{comment_id}",
        response_model=AidLocationCommentDeleteReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Rol insuficiente u origen no permitido"},
            404: {"description": "Comentario inexistente"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_delete_damaged_home_comment(
        damaged_home_id: UUID,
        comment_id: UUID,
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
            f"/internal/v1/admin/damaged-homes/{damaged_home_id}"
            f"/comments/{comment_id}",
            AidLocationCommentDeleteReceipt,
        )

    @application.delete(
        "/api/v1/admin/damaged-homes/{damaged_home_id}",
        response_model=DamagedHomeDeleteReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Rol insuficiente u origen no permitido"},
            404: {"description": "Casita inexistente"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_delete_damaged_home(
        damaged_home_id: UUID,
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
            f"/internal/v1/admin/damaged-homes/{damaged_home_id}",
            DamagedHomeDeleteReceipt,
        )

    # ------------------------------------------------------------------
    # CHG-180 — La misma comunidad para «Necesitamos ayuda». Autorización
    # idéntica a la de acopios y ofertas: leer es público, comentar exige
    # Origin e Idempotency-Key (anónimo permitido), denunciar tiene su
    # variante anónima y con cuenta, y borrar exige super_admin.
    # ------------------------------------------------------------------

    async def _forward_help_request_report(
        help_request_id: UUID,
        request: Request,
        upstream: httpx.AsyncClient,
        actor_kind: str,
        denouncer_key: str,
        account_id: UUID | None,
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
        headers = {
            "content-type": request.headers.get(
                "content-type", "application/json"
            ),
            "idempotency-key": idempotency_key,
            "x-actor-kind": actor_kind,
            "x-denouncer-key": denouncer_key,
        }
        if account_id is not None:
            headers["x-account-id"] = str(account_id)
        body = await request.body()
        try:
            response = await upstream.post(
                f"/internal/v1/help-requests/{help_request_id}/reports",
                content=body,
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=202,
                content=HelpRequestReportReceipt.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible registrar la denuncia en este momento.",
                title="Servicio no disponible",
            )

    @application.post(
        "/api/v1/public/help-requests/{help_request_id}/reports",
        status_code=202,
        response_model=HelpRequestReportReceipt,
        response_model_by_alias=True,
        responses={
            404: {"description": "Solicitud inexistente"},
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HelpRequests"],
    )
    async def create_anonymous_help_request_report(
        help_request_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
    ):
        if not anonymous_contribution_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de denuncias por minuto."
            )
        return await _forward_help_request_report(
            help_request_id,
            request,
            upstream,
            "anonymous",
            _denouncer_key_anonymous(request),
            None,
        )

    @application.post(
        "/api/v1/me/help-requests/{help_request_id}/reports",
        status_code=202,
        response_model=HelpRequestReportReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Origen no permitido"},
            404: {"description": "Solicitud inexistente"},
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HelpRequests"],
    )
    async def create_account_help_request_report(
        help_request_id: UUID,
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
        if not account_contribution_limiter.allow(f"account:{account.id}"):
            return rate_limited_response(
                "Se superó el límite de denuncias por minuto."
            )
        return await _forward_help_request_report(
            help_request_id,
            request,
            upstream,
            "authenticated",
            f"account:{account.id}",
            account.id,
        )

    @application.get(
        "/api/v1/help-requests/{help_request_id}/comments",
        response_model=AidLocationCommentsResponse,
        response_model_by_alias=True,
        responses={
            404: {"description": "Solicitud inexistente"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HelpRequests"],
    )
    async def list_help_request_comments(
        help_request_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ):
        if not directory_search_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de consultas por minuto."
            )
        try:
            response = await upstream.get(
                f"/internal/v1/help-requests/{help_request_id}/comments",
                params={"limit": limit},
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return AidLocationCommentsResponse.model_validate(
                response.json()
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible consultar los comentarios en este "
                "momento.",
                title="Servicio no disponible",
            )

    @application.post(
        "/api/v1/help-requests/{help_request_id}/comments",
        status_code=201,
        response_model=AidLocationComment,
        response_model_by_alias=True,
        responses={
            403: {"description": "Origen no permitido"},
            404: {"description": "Solicitud inexistente"},
            422: {"description": "Datos inválidos"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["HelpRequests"],
    )
    async def create_help_request_comment(
        help_request_id: UUID,
        request: Request,
        upstream: Annotated[httpx.AsyncClient, Depends(get_client)],
        identity: Annotated[
            httpx.AsyncClient, Depends(get_identity_client)
        ],
    ):
        forbidden = origin_not_allowed(request)
        if forbidden is not None:
            return forbidden
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
        account = await resolve_optional_account(request, identity)
        if account is not None:
            if not account_contribution_limiter.allow(
                f"account:{account.id}"
            ):
                return rate_limited_response(
                    "Se superó el límite de comentarios por minuto."
                )
        elif not anonymous_contribution_limiter.allow(client_key(request)):
            return rate_limited_response(
                "Se superó el límite de comentarios por minuto."
            )
        headers = {
            "content-type": request.headers.get(
                "content-type", "application/json"
            ),
            "idempotency-key": idempotency_key,
            "x-actor-kind": (
                "authenticated" if account is not None else "anonymous"
            ),
        }
        if account is not None:
            headers["x-account-id"] = str(account.id)
            headers["x-actor-display"] = base64.b64encode(
                account.display_name.encode()
            ).decode()
        body = await request.body()
        try:
            response = await upstream.post(
                f"/internal/v1/help-requests/{help_request_id}/comments",
                content=body,
                headers=headers,
            )
            if 400 <= response.status_code < 500:
                return passthrough(response)
            response.raise_for_status()
            return JSONResponse(
                status_code=201,
                content=AidLocationComment.model_validate(
                    response.json()
                ).model_dump(mode="json", by_alias=True),
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return problem_response(
                "No fue posible publicar el comentario en este momento.",
                title="Servicio no disponible",
            )

    @application.delete(
        "/api/v1/admin/help-requests/{help_request_id}/comments"
        "/{comment_id}",
        response_model=AidLocationCommentDeleteReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Rol insuficiente u origen no permitido"},
            404: {"description": "Comentario inexistente"},
            429: {"description": "Límite excedido"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_delete_help_request_comment(
        help_request_id: UUID,
        comment_id: UUID,
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
            f"/internal/v1/admin/help-requests/{help_request_id}"
            f"/comments/{comment_id}",
            AidLocationCommentDeleteReceipt,
        )

    # CHG-165 §15, §21-24 — Consola super_admin: bandeja de
    # verificaciones, decisión y reactivación de acopios locales.
    @application.get(
        "/api/v1/admin/aid-locations/verifications",
        response_model=AdminAidLocationVerificationsResponse,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Rol insuficiente"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_aid_location_verifications(
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
            "/internal/v1/admin/aid-locations/verifications",
            account,
            AdminAidLocationVerificationsResponse,
        )

    @application.post(
        "/api/v1/admin/aid-locations/{location_id}/verification",
        response_model=AdminAidLocationActionReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Rol insuficiente"},
            404: {"description": "Centro inexistente"},
            422: {"description": "Datos inválidos"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_decide_aid_location_verification(
        location_id: UUID,
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
            "POST",
            f"/internal/v1/admin/aid-locations/{location_id}/verification",
            account,
            AdminAidLocationActionReceipt,
            body=await request.body(),
        )

    @application.post(
        "/api/v1/admin/aid-locations/{location_id}/reactivate",
        response_model=AdminAidLocationActionReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Rol insuficiente"},
            404: {"description": "Centro inexistente"},
            409: {"description": "Centro no deshabilitado"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_reactivate_aid_location(
        location_id: UUID,
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
            "POST",
            f"/internal/v1/admin/aid-locations/{location_id}/reactivate",
            account,
            AdminAidLocationActionReceipt,
        )

    # CHG-170 — Borrado admin del acopio completo desde su ficha de
    # VER MÁS. Muta: va por admin_mutation (Origin + super_admin +
    # limitador); 409 si tiene transportes asociados.
    @application.delete(
        "/api/v1/admin/aid-locations/{location_id}",
        response_model=AdminAidLocationDeleteReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Rol u origen insuficiente"},
            404: {"description": "Acopio inexistente"},
            409: {"description": "Acopio con transportes asociados"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_delete_aid_location(
        location_id: UUID,
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
            f"/internal/v1/admin/aid-locations/{location_id}",
            AdminAidLocationDeleteReceipt,
        )

    # CHG-167 — Borrado admin de un comentario de acopio local. Muta:
    # va por admin_mutation (Origin válido + super_admin + limitador).
    @application.delete(
        "/api/v1/admin/aid-locations/{location_id}/comments/{comment_id}",
        response_model=AidLocationCommentDeleteReceipt,
        response_model_by_alias=True,
        responses={
            401: {"description": "Sesión ausente, vencida o revocada"},
            403: {"description": "Rol u origen insuficiente"},
            404: {"description": "Comentario inexistente"},
            503: {"description": "Servicio no disponible"},
        },
        tags=["Administration"],
    )
    async def admin_delete_aid_location_comment(
        location_id: UUID,
        comment_id: UUID,
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
            f"/internal/v1/admin/aid-locations/{location_id}/comments/"
            f"{comment_id}",
            AidLocationCommentDeleteReceipt,
        )

    return application


app = create_app()
