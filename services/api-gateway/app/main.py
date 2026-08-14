import hashlib
import json
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .models import (
    AccountRegistrationReceipt,
    AuthenticatedAccount,
    DisasterEventList,
    EmailVerificationReceipt,
    HealthStatus,
    HumanImpactOverview,
    HumanMapOverview,
    HumanStatus,
    MissingPersonReportReceipt,
    MissingPersonSearchResponse,
    OperationalMapOverview,
    PeopleRecordPage,
    SessionEnvelope,
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
        allow_methods=["GET", "POST", "DELETE"],
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
            return EmailVerificationReceipt.model_validate(
                response.json()
            )
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            return identity_unavailable()

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

    return application


app = create_app()
