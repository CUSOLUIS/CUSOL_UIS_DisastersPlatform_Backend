import base64
import hashlib
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

import asyncpg
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .config import Settings
from .models import (
    DisasterEventList,
    HealthStatus,
    HumanImpactOverview,
    HumanMapBounds,
    HumanMapCluster,
    HumanMapOverview,
    HumanMapPoint,
    HumanMapStatusCounts,
    HumanStatus,
    MissingPersonReportInput,
    MissingPersonReportReceipt,
    MissingPersonSearchResponse,
    OperationalMapOverview,
    OperationalMapPoint,
    OperationalMapSummary,
    PeopleRecordPage,
)
from .photos import (
    MalwareScanner,
    PhotoProcessingError,
    SignatureMalwareScanner,
    sniff_image_type,
    strip_metadata,
)
from .models import SourceReference
from .repository import (
    DisasterRepository,
    HumanMapCell,
    PostgresDisasterRepository,
    StoredPhoto,
    StoredReport,
)
from .storage import (
    LocalObjectStorage,
    ObjectStorage,
    StorageUnavailableError,
)


def problem(status_code: int, title: str, detail: str) -> JSONResponse:
    """Respuesta `application/problem+json` sin datos sensibles."""
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


def build_fernet(passphrase: str) -> Fernet:
    digest = hashlib.sha256(passphrase.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def generate_public_case_code(now: datetime) -> str:
    return f"MP-{now.year}-{secrets.token_hex(4).upper()}"


def protect_missing_person(
    point: OperationalMapPoint,
) -> OperationalMapPoint:
    """Aplica la regla pública de DEC-007 antes de serializar.

    Un punto `missing_person` representa una zona pública de búsqueda:
    mientras DEC-007 esté pendiente nunca se publica con precisión
    `exact`; si la base contiene un dato así, se degrada a zona
    aproximada redondeando la coordenada (~1 km).
    """
    if point.category != "missing_person":
        return point
    if point.coordinate_precision != "exact":
        return point
    return point.model_copy(
        update={
            "coordinate_precision": "approximate",
            "latitude": round(point.latitude, 2),
            "longitude": round(point.longitude, 2),
        }
    )


# CHG-015 — Capa geográfica de situación humana.
HUMAN_MAP_GRID_DIVISIONS = 20


def human_map_cell_size(
    zoom: int,
    west: float,
    south: float,
    east: float,
    north: float,
) -> float:
    """Tamaño determinista de celda de la grilla de clustering.

    La componente de zoom divide el mundo en celdas de ~1/4 de tesela;
    las componentes de bbox garantizan a lo sumo 20×20 celdas visibles,
    de modo que la respuesta nunca supera 500 features.
    """
    zoom_cell = 360.0 / (2**zoom * 4)
    return max(
        zoom_cell,
        (east - west) / HUMAN_MAP_GRID_DIVISIONS,
        (north - south) / HUMAN_MAP_GRID_DIVISIONS,
    )


def encode_map_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def decode_map_cursor(cursor: str) -> int | None:
    """Devuelve el offset del cursor opaco, o None si es inválido."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    if not raw.startswith("o:"):
        return None
    try:
        offset = int(raw[2:])
    except ValueError:
        return None
    return offset if offset >= 0 else None


def build_human_map_features(
    cells: list[HumanMapCell],
    zoom: int,
) -> list[HumanMapCluster | HumanMapPoint]:
    """Convierte celdas en features: clusters primero, luego puntos.

    El orden es determinista (clusters por count descendente e id;
    puntos por id) para que el cursor sea estable entre páginas.
    """
    clusters: list[HumanMapCluster] = []
    points: list[HumanMapPoint] = []
    for cell in cells:
        if cell.count == 1:
            precision = cell.point_precision
            latitude = cell.latitude
            longitude = cell.longitude
            if precision == "exact":
                # Defensa en profundidad DEC-007: la base ya lo impide,
                # pero jamás se publica una coordenada exacta.
                precision = "approximate"
                latitude = round(latitude, 2)
                longitude = round(longitude, 2)
            points.append(
                HumanMapPoint(
                    id=cell.point_id,
                    status=cell.point_status,
                    latitude=latitude,
                    longitude=longitude,
                    coordinate_precision=precision,
                    verification_status=cell.point_verification,
                    source=SourceReference(
                        name=cell.point_source_name,
                        source_type=cell.point_source_type,
                        url=cell.point_source_url,
                    ),
                    updated_at=cell.point_updated_at,
                )
            )
        else:
            clusters.append(
                HumanMapCluster(
                    id=f"z{zoom}:x{cell.cell_x}:y{cell.cell_y}",
                    latitude=cell.latitude,
                    longitude=cell.longitude,
                    count=cell.count,
                    status_counts=HumanMapStatusCounts(
                        missing=cell.missing,
                        reported_deceased=cell.reported_deceased,
                        confirmed_alive=cell.confirmed_alive,
                        confirmed_deceased=cell.confirmed_deceased,
                    ),
                    bounds=HumanMapBounds(
                        west=cell.west,
                        south=cell.south,
                        east=cell.east,
                        north=cell.north,
                    ),
                )
            )
    clusters.sort(key=lambda cluster: (-cluster.count, cluster.id))
    points.sort(key=lambda point: str(point.id))
    return [*clusters, *points]


def create_app(
    settings: Settings | None = None,
    repository: DisasterRepository | None = None,
    storage: ObjectStorage | None = None,
    scanner: MalwareScanner | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_environment()
    object_storage = storage or LocalObjectStorage(
        resolved_settings.upload_dir
    )
    malware_scanner = scanner or SignatureMalwareScanner()
    fernet = build_fernet(resolved_settings.report_encryption_key)

    def encrypt(value: str | None) -> bytes | None:
        if value is None or value == "":
            return None
        return fernet.encrypt(value.encode())

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
        application.state.repository = PostgresDisasterRepository(pool)
        try:
            yield
        finally:
            await pool.close()

    application = FastAPI(
        title="CUSOL UIS Disasters Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    def get_repository(request: Request) -> DisasterRepository:
        return request.app.state.repository

    @application.get(
        "/health/live",
        response_model=HealthStatus,
        tags=["Platform"],
    )
    async def liveness() -> HealthStatus:
        return HealthStatus(status="ok", service="disaster-service")

    @application.get(
        "/health/ready",
        response_model=HealthStatus,
        responses={503: {"description": "Base de datos no disponible"}},
        tags=["Platform"],
    )
    async def readiness(
        data: Annotated[DisasterRepository, Depends(get_repository)],
    ):
        try:
            ready = await data.ping()
        except (asyncpg.PostgresError, TimeoutError):
            ready = False

        if not ready:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "service": "disaster-service",
                },
            )

        return HealthStatus(status="ok", service="disaster-service")

    @application.get(
        "/internal/v1/disasters",
        response_model=DisasterEventList,
        response_model_by_alias=True,
        tags=["Disasters"],
    )
    async def list_disasters(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
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
    ) -> DisasterEventList:
        items, total = await data.list_events(
            disaster_type=disaster_type,
            verification_status=verification_status,
            limit=limit,
            offset=offset,
        )
        return DisasterEventList(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        )

    @application.get(
        "/internal/v1/people/overview",
        response_model=HumanImpactOverview,
        response_model_by_alias=True,
        tags=["People"],
    )
    async def people_overview(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
        recent_limit: Annotated[
            int, Query(alias="recentLimit", ge=10, le=50)
        ] = 10,
    ) -> HumanImpactOverview:
        summary, recent = await data.people_overview(recent_limit)
        return HumanImpactOverview(
            summary=summary,
            recent_people=recent,
            generated_at=datetime.now(UTC),
        )

    @application.get(
        "/internal/v1/people/records",
        response_model=PeopleRecordPage,
        response_model_by_alias=True,
        tags=["People"],
    )
    async def list_people_records(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
        offset: Annotated[int, Query(ge=0)] = 0,
        statuses: Annotated[
            list[HumanStatus] | None, Query()
        ] = None,
        q: Annotated[str | None, Query(max_length=100)] = None,
    ) -> PeopleRecordPage | JSONResponse:
        if limit not in (10, 25, 50):
            return problem(
                422,
                "Tamaño de página inválido",
                "El tamaño de página debe ser 10, 25 o 50.",
            )
        search = (q or "").strip()
        if q is not None and q.strip() and len(search) < 2:
            return problem(
                422,
                "Búsqueda inválida",
                "La búsqueda requiere entre 2 y 100 caracteres.",
            )
        unique_statuses = (
            list(dict.fromkeys(statuses)) if statuses else None
        )
        items, total = await data.list_people_records(
            unique_statuses,
            search or None,
            limit,
            offset,
        )
        return PeopleRecordPage(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            generated_at=datetime.now(UTC),
        )

    @application.get(
        "/internal/v1/people/map-overview",
        response_model=HumanMapOverview,
        response_model_by_alias=True,
        tags=["People"],
    )
    async def human_map_overview(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
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
            return problem(
                422,
                "Área inválida",
                "El bbox requiere west < east y south < north.",
            )
        offset = 0
        if cursor is not None:
            decoded = decode_map_cursor(cursor)
            if decoded is None:
                return problem(
                    422,
                    "Cursor inválido",
                    "El cursor no corresponde a esta consulta.",
                )
            offset = decoded

        cell_size = human_map_cell_size(zoom, west, south, east, north)
        cells, unmapped = await data.human_map_overview(
            west,
            south,
            east,
            north,
            cell_size,
            list(statuses) if statuses else None,
        )
        features = build_human_map_features(cells, zoom)
        total_mapped = sum(cell.count for cell in cells)
        page = features[offset:offset + limit]
        next_cursor = (
            encode_map_cursor(offset + limit)
            if offset + limit < len(features)
            else None
        )
        classification = (
            "operational"
            if cells and all(cell.all_operational for cell in cells)
            else "demonstrative"
        )
        return HumanMapOverview(
            features=page,
            total_matched=total_mapped + unmapped,
            total_mapped=total_mapped,
            unmapped_count=unmapped,
            returned_features=len(page),
            next_cursor=next_cursor,
            generated_at=datetime.now(UTC),
            data_classification=classification,
        )

    @application.get(
        "/internal/v1/operational-map/overview",
        response_model=OperationalMapOverview,
        response_model_by_alias=True,
        tags=["OperationalMap"],
    )
    async def operational_map_overview(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> OperationalMapOverview:
        points, classification = await data.operational_map_overview(
            limit
        )
        items = [protect_missing_person(point) for point in points]
        by_category = {
            "missing_person": 0,
            "collection_center": 0,
            "rubble_reviewed": 0,
            "rubble_pending": 0,
            "building_pending": 0,
        }
        for item in items:
            by_category[item.category] += 1
        return OperationalMapOverview(
            summary=OperationalMapSummary(
                missing_person=by_category["missing_person"],
                collection_center=by_category["collection_center"],
                rubble_reviewed=by_category["rubble_reviewed"],
                rubble_pending=by_category["rubble_pending"],
                building_pending=by_category["building_pending"],
            ),
            items=items,
            generated_at=datetime.now(UTC),
            data_classification=classification,
        )

    @application.get(
        "/internal/v1/missing-persons/search",
        response_model=MissingPersonSearchResponse,
        response_model_by_alias=True,
        tags=["MissingPersons"],
    )
    async def search_missing_persons(
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
        q: Annotated[str, Query(min_length=2, max_length=100)],
        limit: Annotated[int, Query(ge=1, le=20)] = 10,
    ):
        if len(q.strip()) < 2:
            return problem(
                422,
                "Consulta inválida",
                "La consulta requiere al menos dos caracteres.",
            )
        items, total = await data.search_missing_persons(q, limit)
        return MissingPersonSearchResponse(
            items=items, total=total, query=q
        )

    @application.post(
        "/internal/v1/missing-person-reports",
        status_code=201,
        response_model=MissingPersonReportReceipt,
        response_model_by_alias=True,
        tags=["MissingPersons"],
    )
    async def create_missing_person_report(
        request: Request,
        data: Annotated[
            DisasterRepository, Depends(get_repository)
        ],
    ):
        limits = resolved_settings
        idempotency_key = request.headers.get(
            "idempotency-key", ""
        ).strip()
        if not 16 <= len(idempotency_key) <= 128:
            return problem(
                422,
                "Encabezado requerido",
                "Idempotency-Key debe tener entre 16 y 128 caracteres.",
            )

        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit():
            # Margen de 2 MiB para payload y fronteras multipart.
            if int(declared) > limits.max_total_photo_bytes + 2_097_152:
                return problem(
                    413,
                    "Carga demasiado grande",
                    "El envío supera el máximo total de 50 MiB.",
                )

        try:
            form = await request.form()
        except Exception:
            return problem(
                422,
                "Formulario inválido",
                "No fue posible interpretar el envío multipart.",
            )

        payload_part = form.get("payload")
        if payload_part is None:
            return problem(
                422,
                "Datos incompletos",
                "Falta la parte JSON `payload`.",
            )
        if isinstance(payload_part, str):
            raw_payload = payload_part.encode()
        else:
            raw_payload = await payload_part.read()

        try:
            payload = MissingPersonReportInput.model_validate_json(
                raw_payload
            )
        except ValidationError as error:
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

        if payload.reporter_phone is None and payload.reporter_email is None:
            return problem(
                422,
                "Contacto requerido",
                "Se requiere al menos teléfono o correo del reportante.",
            )
        if payload.last_seen_date > datetime.now(UTC).date():
            return problem(
                422,
                "Fecha inválida",
                "La fecha de última visualización no puede ser futura.",
            )

        photo_parts = [
            value
            for key, value in form.multi_items()
            if key == "photos"
        ]
        if not 1 <= len(photo_parts) <= limits.max_photos:
            return problem(
                422,
                "Cantidad de fotografías inválida",
                "El reporte requiere entre una y cinco fotografías.",
            )

        prepared: list[tuple[int, bytes, str]] = []
        total_bytes = 0
        for index, part in enumerate(photo_parts, start=1):
            if isinstance(part, str):
                return problem(
                    415,
                    "Fotografía inválida",
                    f"La fotografía {index} no es un archivo.",
                )
            content = await part.read()
            if len(content) > limits.max_photo_bytes:
                return problem(
                    413,
                    "Archivo demasiado grande",
                    f"La fotografía {index} supera el máximo de 10 MiB.",
                )
            total_bytes += len(content)
            if total_bytes > limits.max_total_photo_bytes:
                return problem(
                    413,
                    "Carga demasiado grande",
                    "El envío supera el máximo total de 50 MiB.",
                )
            sniffed = sniff_image_type(content)
            if sniffed is None:
                return problem(
                    415,
                    "Formato no permitido",
                    f"La fotografía {index} no es JPEG, PNG, WebP ni "
                    "HEIC/HEIF según su contenido real.",
                )
            if not malware_scanner.scan(content):
                return problem(
                    415,
                    "Contenido no permitido",
                    f"La fotografía {index} no superó el análisis de "
                    "seguridad.",
                )
            prepared.append((index, content, sniffed))

        received_at = datetime.now(UTC)
        report_id = uuid4()
        saved_keys: list[str] = []
        stored_photos: list[StoredPhoto] = []

        def cleanup() -> None:
            for key in saved_keys:
                object_storage.delete(key)

        try:
            for index, content, sniffed in prepared:
                sanitized = strip_metadata(content, sniffed)
                photo_id = uuid4()
                prefix = f"missing-person-reports/{report_id}"
                original_key = f"{prefix}/original/{photo_id}.bin"
                derived_key = (
                    f"{prefix}/derived/{photo_id}.{sanitized.extension}"
                )
                object_storage.save(original_key, content)
                saved_keys.append(original_key)
                object_storage.save(derived_key, sanitized.data)
                saved_keys.append(derived_key)
                stored_photos.append(
                    StoredPhoto(
                        id=photo_id,
                        position=index,
                        storage_key=original_key,
                        derived_storage_key=derived_key,
                        content_type=sanitized.content_type,
                        size_bytes=len(content),
                        sha256=hashlib.sha256(content).hexdigest(),
                        exif_removed=True,
                        malware_scan="clean",
                    )
                )
        except PhotoProcessingError:
            cleanup()
            return problem(
                415,
                "Fotografía no procesable",
                "Alguna fotografía no pudo validarse como imagen segura.",
            )
        except StorageUnavailableError:
            cleanup()
            return problem(
                503,
                "Almacenamiento no disponible",
                "No fue posible resguardar las fotografías; el reporte "
                "no fue registrado.",
            )

        stored_report = StoredReport(
            id=report_id,
            idempotency_key=idempotency_key,
            public_case_code=generate_public_case_code(received_at),
            first_names=payload.first_names,
            last_names=payload.last_names,
            aliases=payload.aliases,
            birth_date=payload.birth_date,
            approximate_age=payload.approximate_age,
            gender_identity=payload.gender_identity,
            nationality=payload.nationality,
            document_type_encrypted=encrypt(payload.document_type),
            document_number_encrypted=encrypt(payload.document_number),
            height_cm=payload.height_cm,
            build=payload.build,
            skin_tone=payload.skin_tone,
            hair_description=payload.hair_description,
            eye_description=payload.eye_description,
            distinctive_marks=payload.distinctive_marks,
            medical_information_encrypted=encrypt(
                payload.medical_information
            ),
            last_seen_date=payload.last_seen_date,
            last_seen_time=payload.last_seen_time,
            last_seen_latitude=payload.last_seen_latitude,
            last_seen_longitude=payload.last_seen_longitude,
            department=payload.department,
            municipality=payload.municipality,
            last_seen_area=payload.last_seen_area,
            clothing_description=payload.clothing_description,
            circumstances=payload.circumstances,
            additional_description=payload.additional_description,
            reporter_name_encrypted=encrypt(payload.reporter_name),
            reporter_relationship=payload.reporter_relationship,
            reporter_phone_encrypted=encrypt(payload.reporter_phone),
            reporter_email_encrypted=encrypt(payload.reporter_email),
            official_report_number=payload.official_report_number,
        )

        try:
            receipt, created = await data.create_missing_person_report(
                stored_report, stored_photos
            )
        except asyncpg.PostgresError:
            cleanup()
            return problem(
                503,
                "Registro no disponible",
                "No fue posible registrar el reporte; ningún dato "
                "quedó publicado.",
            )

        if not created:
            # Reintento idempotente: los archivos de este intento sobran.
            cleanup()

        return receipt

    return application


app = create_app()
