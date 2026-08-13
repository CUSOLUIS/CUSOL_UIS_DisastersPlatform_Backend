from contextlib import asynccontextmanager
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .models import (
    DisasterEventList,
    HealthStatus,
    HumanImpactOverview,
    MissingPersonReportReceipt,
    MissingPersonSearchResponse,
    OperationalMapOverview,
)
from .ratelimit import SlidingWindowRateLimiter


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
) -> FastAPI:
    resolved_settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if client is not None:
            application.state.upstream_client = client
            yield
            return

        async with httpx.AsyncClient(
            base_url=resolved_settings.disaster_service_url,
            timeout=resolved_settings.upstream_timeout_seconds,
        ) as upstream_client:
            application.state.upstream_client = upstream_client
            yield

    application = FastAPI(
        title="CUSOL UIS Disasters API Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )

    search_limiter = SlidingWindowRateLimiter(
        resolved_settings.search_rate_limit_per_minute
    )
    reports_limiter = SlidingWindowRateLimiter(
        resolved_settings.reports_rate_limit_per_minute
    )

    def get_client(request: Request) -> httpx.AsyncClient:
        return request.app.state.upstream_client

    def client_key(request: Request) -> str:
        return request.client.host if request.client else "unknown"

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
    ):
        try:
            response = await upstream.get("/health/ready")
            response.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException):
            return problem_response("El servicio de desastres no está preparado.")

        return HealthStatus(status="ok", service="api-gateway")

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
