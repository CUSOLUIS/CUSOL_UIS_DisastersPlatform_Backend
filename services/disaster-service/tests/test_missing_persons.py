import json
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from PIL import Image

from app.main import build_fernet, create_app
from app.config import Settings
from app.models import (
    MissingPersonPublicRecord,
    MissingPersonReportReceipt,
    PersonSuggestion,
)
from app.photos import (
    SignatureMalwareScanner,
    sniff_image_type,
    strip_metadata,
)
from app.storage import StorageUnavailableError


EICAR = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-"
    b"ANTIVIRUS-TEST-FILE!$H+H*"
)

PUBLIC_RECORD = MissingPersonPublicRecord(
    id=UUID("55555555-5555-4555-8555-555555555501"),
    public_case_code="MP-2026-DEMO01",
    display_name="Camila Rueda (caso demo)",
    aliases=["Cami"],
    approximate_age=34,
    last_seen_at=datetime(2026, 8, 10, 18, 30, tzinfo=UTC),
    last_seen_area="Sector Café Madrid",
    municipality="Bucaramanga",
    department="Santander",
    clothing_description="Chaqueta azul",
    physical_description="Cabello castaño largo",
    distinctive_marks=None,
    public_photo_url=None,
    map_point_id=None,
    updated_at=datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
    data_classification="demonstrative",
)

SUGGESTION = PersonSuggestion(
    id=UUID("55555555-5555-4555-8555-555555555501"),
    public_case_code="MP-2026-DEMO01",
    display_name="Camila Rueda (caso demo)",
    status="missing",
    approximate_age=34,
    last_seen_at=datetime(2026, 8, 10, 18, 30, tzinfo=UTC),
    last_seen_area="Sector Café Madrid",
    municipality="Bucaramanga",
    department="Santander",
    public_photo_url=None,
    source={"name": "Registro público CUSOL", "sourceType": "citizen", "url": None},
    updated_at=datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
    data_classification="demonstrative",
    similarity=0.62,
)

RECEIPT = MissingPersonReportReceipt(
    id=UUID("66666666-6666-4666-8666-666666666601"),
    public_case_code="MP-2026-AAAA1111",
    status="published",
    received_at=datetime(2026, 8, 12, 16, 0, tzinfo=UTC),
)


class FakeMissingPersonRepository:
    def __init__(self, duplicate: bool = False, fail: bool = False):
        self.duplicate = duplicate
        self.fail = fail
        self.last_search = None
        self.created_report = None
        self.created_photos = None

    async def ping(self) -> bool:
        return True

    async def search_missing_persons(self, query, limit):
        self.last_search = {"query": query, "limit": limit}
        return [PUBLIC_RECORD], 1

    # CHG-091 — Sugerencias difusas.
    async def autocomplete_persons(self, query, limit):
        self.last_autocomplete = {"query": query, "limit": limit}
        return [SUGGESTION]

    async def check_person_duplicates(self, full_name, limit):
        self.last_duplicates = {"fullName": full_name, "limit": limit}
        return [SUGGESTION]

    async def create_missing_person_report(self, report, photos):
        if self.fail:
            raise asyncpg.PostgresError("fallo simulado")
        if self.duplicate:
            return RECEIPT, False
        self.created_report = report
        self.created_photos = photos
        return (
            MissingPersonReportReceipt(
                id=report.id,
                public_case_code=report.public_case_code,
                status="published",
                received_at=datetime(2026, 8, 12, 16, 0, tzinfo=UTC),
            ),
            True,
        )


class FakeStorage:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def save(self, key: str, data: bytes) -> None:
        if self.fail:
            raise StorageUnavailableError("sin espacio")
        self.objects[key] = data

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)

    def load(self, key: str) -> bytes | None:
        if self.fail:
            from app.storage import StorageUnavailableError

            raise StorageUnavailableError("sin acceso")
        return self.objects.get(key)


def make_jpeg(with_exif: bool = True, size=(48, 48)) -> bytes:
    image = Image.new("RGB", size, "red")
    output = BytesIO()
    if with_exif:
        exif = Image.Exif()
        exif[271] = "DemoCam"  # Make
        exif[272] = "DemoModel"  # Model
        image.save(output, format="JPEG", exif=exif.tobytes())
    else:
        image.save(output, format="JPEG")
    return output.getvalue()


def make_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), "blue").save(output, format="PNG")
    return output.getvalue()


def valid_payload(**overrides) -> dict:
    payload = {
        "firstNames": "Nombre",
        "lastNames": "Demo",
        "lastSeenDate": "2026-08-11",
        "department": "Santander",
        "municipality": "Bucaramanga",
        "lastSeenArea": "Café Madrid",
        "clothingDescription": "Chaqueta azul",
        "circumstances": "Salió hacia el trabajo y no regresó.",
        "reporterName": "Reportante Demo",
        "reporterRelationship": "Hermana",
        "reporterPhone": "+57 300 000 0000",
        "documentNumber": "CC-1234567",
        "medicalInformation": "Requiere medicación diaria",
        "truthConfirmed": True,
        "photoAuthorizationConfirmed": True,
        "reviewAcknowledged": True,
    }
    payload.update(overrides)
    return payload


IDEMPOTENCY = {"Idempotency-Key": "clave-idempotente-0001"}


async def request_app(app, method, path, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)


def report_app(repository=None, storage=None, settings=None, scanner=None):
    return create_app(
        settings=settings,
        repository=repository or FakeMissingPersonRepository(),
        storage=storage if storage is not None else FakeStorage(),
        scanner=scanner,
    )


def photos_form(count: int, photo: bytes | None = None):
    content = photo if photo is not None else make_jpeg()
    return [
        ("photos", (f"foto-{index}.jpg", content, "image/jpeg"))
        for index in range(count)
    ]


# CHG-094: atajo para los envíos de reporte de persona.
async def post_report(app, payload=None, photos=1):
    return await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={
            "payload": json.dumps(
                payload if payload is not None else valid_payload()
            )
        },
        files=photos_form(photos),
        headers=IDEMPOTENCY,
    )


# --- Búsqueda pública ---


@pytest.mark.anyio
async def test_search_returns_only_public_projection():
    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository)

    response = await request_app(
        app, "GET", "/internal/v1/missing-persons/search?q=Camila&limit=5"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["query"] == "Camila"
    assert repository.last_search == {"query": "Camila", "limit": 5}
    item = body["items"][0]
    assert set(item.keys()) == {
        "id", "publicCaseCode", "displayName", "aliases",
        "approximateAge", "lastSeenAt", "lastSeenArea", "municipality",
        "department", "clothingDescription", "physicalDescription",
        "distinctiveMarks", "publicPhotoUrl", "mapPointId", "updatedAt",
        "dataClassification",
    }
    forbidden = {
        "documentNumber", "documentType", "medicalInformation",
        "reporterName", "reporterPhone", "reporterEmail",
    }
    assert forbidden.isdisjoint(item.keys())


@pytest.mark.anyio
async def test_search_rejects_short_queries():
    app = report_app()

    too_short = await request_app(
        app, "GET", "/internal/v1/missing-persons/search?q=a"
    )
    blank = await request_app(
        app, "GET", "/internal/v1/missing-persons/search?q=%20a"
    )

    assert too_short.status_code == 422
    assert blank.status_code == 422


# --- CHG-091: sugerencias para prevenir duplicados ---


@pytest.mark.anyio
async def test_autocomplete_returns_suggestions_with_similarity():
    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository)

    response = await request_app(
        app, "GET", "/internal/v1/persons/autocomplete?q=Kamila&limit=5"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "Kamila"
    assert repository.last_autocomplete == {"query": "Kamila", "limit": 5}
    item = body["items"][0]
    # La tarjeta pública del directorio + similitud; nada privado.
    assert item["publicCaseCode"] == "MP-2026-DEMO01"
    assert item["status"] == "missing"
    assert item["municipality"] == "Bucaramanga"
    assert item["similarity"] == 0.62
    forbidden = {
        "documentNumber", "medicalInformation", "reporterName",
        "reporterPhone", "reporterEmail",
    }
    assert forbidden.isdisjoint(item.keys())


@pytest.mark.anyio
async def test_autocomplete_rejects_short_queries():
    app = report_app()

    response = await request_app(
        app, "GET", "/internal/v1/persons/autocomplete?q=%20a"
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_check_duplicates_joins_names_and_returns_matches():
    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository)

    response = await request_app(
        app,
        "GET",
        "/internal/v1/persons/check-duplicates"
        "?firstName=Kamila&lastName=Rueda",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["firstName"] == "Kamila"
    assert body["lastName"] == "Rueda"
    assert repository.last_duplicates == {
        "fullName": "Kamila Rueda",
        "limit": 5,
    }
    assert body["items"][0]["publicCaseCode"] == "MP-2026-DEMO01"


@pytest.mark.anyio
async def test_check_duplicates_rejects_names_too_short():
    app = report_app()

    response = await request_app(
        app,
        "GET",
        "/internal/v1/persons/check-duplicates?firstName=Jo&lastName=",
    )

    assert response.status_code == 422


# --- Recepción de reportes ---


@pytest.mark.anyio
async def test_report_with_one_photo_creates_published_receipt():
    repository = FakeMissingPersonRepository()
    storage = FakeStorage()
    app = report_app(repository=repository, storage=storage)

    response = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={"payload": json.dumps(valid_payload())},
        files=photos_form(1),
        headers=IDEMPOTENCY,
    )

    assert response.status_code == 201
    body = response.json()
    # CHG-075: la constancia informa publicación inmediata.
    assert body["status"] == "published"
    assert body["publicCaseCode"].startswith("MP-")
    assert set(body.keys()) == {
        "id", "publicCaseCode", "status", "receivedAt"
    }

    # Almacenamiento opaco: original + derivado, sin nombre del cliente.
    assert len(storage.objects) == 2
    assert all("foto-0" not in key for key in storage.objects)

    # El derivado no conserva EXIF.
    derived_key = next(
        key for key in storage.objects if "/derived/" in key
    )
    derived = Image.open(BytesIO(storage.objects[derived_key]))
    assert dict(derived.getexif()) == {}

    photo = repository.created_photos[0]
    assert photo.exif_removed is True
    assert photo.malware_scan == "clean"

    # Campos sensibles cifrados y recuperables solo con la clave.
    report = repository.created_report
    fernet = build_fernet("dev-local-only-report-key")
    assert report.document_number_encrypted != b"CC-1234567"
    assert (
        fernet.decrypt(report.document_number_encrypted).decode()
        == "CC-1234567"
    )
    assert (
        fernet.decrypt(report.reporter_phone_encrypted).decode()
        == "+57 300 000 0000"
    )
    assert report.reporter_email_encrypted is None


@pytest.mark.anyio
async def test_report_accepts_three_photos_and_rejects_zero_and_four():
    # CHG-071: el máximo bajó a 3 fotografías por reporte.
    storage = FakeStorage()
    app = report_app(storage=storage)

    three = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={"payload": json.dumps(valid_payload())},
        files=photos_form(3),
        headers=IDEMPOTENCY,
    )
    zero = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={"payload": json.dumps(valid_payload())},
        headers=IDEMPOTENCY,
    )
    four = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={"payload": json.dumps(valid_payload())},
        files=photos_form(4),
        headers=IDEMPOTENCY,
    )

    assert three.status_code == 201
    assert len(storage.objects) == 6
    assert zero.status_code == 422
    assert four.status_code == 422


@pytest.mark.anyio
async def test_report_rejects_content_that_is_not_an_image():
    app = report_app()

    response = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={"payload": json.dumps(valid_payload())},
        files=[
            (
                "photos",
                ("enganosa.jpg", b"no soy una imagen real", "image/jpeg"),
            )
        ],
        headers=IDEMPOTENCY,
    )

    assert response.status_code == 415
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.anyio
async def test_report_rejects_malware_signature():
    app = report_app()
    infected = make_jpeg() + EICAR

    response = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={"payload": json.dumps(valid_payload())},
        files=[("photos", ("foto.jpg", infected, "image/jpeg"))],
        headers=IDEMPOTENCY,
    )

    assert response.status_code == 415


@pytest.mark.anyio
async def test_report_rejects_oversized_photo_and_total():
    small_limits = Settings(
        database_url="postgresql://unused",
        database_pool_min_size=1,
        database_pool_max_size=2,
        upload_dir="/tmp/unused",
        report_encryption_key="test-key",
        max_photo_bytes=64,
        max_total_photo_bytes=100_000,
    )
    app = report_app(settings=small_limits)
    oversized = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={"payload": json.dumps(valid_payload())},
        files=photos_form(1),
        headers=IDEMPOTENCY,
    )

    total_limits = Settings(
        database_url="postgresql://unused",
        database_pool_min_size=1,
        database_pool_max_size=2,
        upload_dir="/tmp/unused",
        report_encryption_key="test-key",
        max_total_photo_bytes=len(make_jpeg()) + 10,
    )
    app = report_app(settings=total_limits)
    total_exceeded = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={"payload": json.dumps(valid_payload())},
        files=photos_form(2),
        headers=IDEMPOTENCY,
    )

    assert oversized.status_code == 413
    assert total_exceeded.status_code == 413


@pytest.mark.anyio
async def test_report_validation_never_echoes_submitted_values():
    app = report_app()

    response = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={
            "payload": json.dumps(
                valid_payload(documentNumber="X" * 500, firstNames="")
            )
        },
        files=photos_form(1),
        headers=IDEMPOTENCY,
    )

    assert response.status_code == 422
    text = response.text
    assert "X" * 20 not in text
    assert "documentNumber" in text  # nombra el campo, nunca el valor


@pytest.mark.anyio
async def test_report_requires_contact_and_past_date():
    app = report_app()

    no_contact = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={
            "payload": json.dumps(valid_payload(reporterPhone=None))
        },
        files=photos_form(1),
        headers=IDEMPOTENCY,
    )
    future = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={
            "payload": json.dumps(valid_payload(lastSeenDate="2999-01-01"))
        },
        files=photos_form(1),
        headers=IDEMPOTENCY,
    )

    assert no_contact.status_code == 422
    assert future.status_code == 422


@pytest.mark.anyio
async def test_report_requires_idempotency_key():
    app = report_app()

    response = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={"payload": json.dumps(valid_payload())},
        files=photos_form(1),
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_report_idempotent_retry_returns_original_receipt():
    storage = FakeStorage()
    app = report_app(
        repository=FakeMissingPersonRepository(duplicate=True),
        storage=storage,
    )

    response = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={"payload": json.dumps(valid_payload())},
        files=photos_form(1),
        headers=IDEMPOTENCY,
    )

    assert response.status_code == 201
    assert response.json()["publicCaseCode"] == "MP-2026-AAAA1111"
    # Los archivos del reintento se descartan: no queda copia huérfana.
    assert len(storage.deleted) == 2
    assert storage.objects == {}


@pytest.mark.anyio
async def test_report_storage_failure_returns_503_without_publishing():
    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository, storage=FakeStorage(fail=True))

    response = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={"payload": json.dumps(valid_payload())},
        files=photos_form(1),
        headers=IDEMPOTENCY,
    )

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert repository.created_report is None


@pytest.mark.anyio
async def test_report_database_failure_cleans_stored_files():
    storage = FakeStorage()
    app = report_app(
        repository=FakeMissingPersonRepository(fail=True),
        storage=storage,
    )

    response = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={"payload": json.dumps(valid_payload())},
        files=photos_form(1),
        headers=IDEMPOTENCY,
    )

    assert response.status_code == 503
    assert storage.objects == {}
    assert len(storage.deleted) == 2


# --- Unidades del pipeline de fotos ---


def test_sniff_image_type_detects_real_content():
    assert sniff_image_type(make_jpeg()) == "image/jpeg"
    assert sniff_image_type(make_png()) == "image/png"
    heic_header = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 8
    assert sniff_image_type(heic_header) == "image/heic"
    assert sniff_image_type(b"GIF89a" + b"\x00" * 32) is None
    assert sniff_image_type(b"no imagen") is None


def test_signature_scanner_flags_eicar_and_executables():
    scanner = SignatureMalwareScanner()
    assert scanner.scan(make_jpeg()) is True
    assert scanner.scan(make_jpeg() + EICAR) is False
    assert scanner.scan(b"\x7fELF" + b"\x00" * 32) is False


def test_strip_metadata_removes_exif_and_reencodes():
    original = make_jpeg(with_exif=True)
    exif_before = Image.open(BytesIO(original)).getexif()
    assert dict(exif_before) != {}

    sanitized = strip_metadata(original, "image/jpeg")

    assert sanitized.content_type == "image/jpeg"
    derived = Image.open(BytesIO(sanitized.data))
    assert dict(derived.getexif()) == {}


# --- CHG-015: coordenadas privadas del último avistamiento ---


@pytest.mark.anyio
async def test_report_rejects_lone_or_out_of_range_coordinates():
    app = report_app()

    lone_latitude = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={
            "payload": json.dumps(
                valid_payload(lastSeenLatitude=7.1193)
            )
        },
        files=photos_form(1),
        headers=IDEMPOTENCY,
    )
    out_of_range = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={
            "payload": json.dumps(
                valid_payload(
                    lastSeenLatitude=95.0, lastSeenLongitude=-73.1
                )
            )
        },
        files=photos_form(1),
        headers=IDEMPOTENCY,
    )

    assert lone_latitude.status_code == 422
    assert out_of_range.status_code == 422
    # Nunca se devuelven los valores enviados.
    assert "7.1193" not in lone_latitude.text
    assert "95.0" not in out_of_range.text


@pytest.mark.anyio
async def test_report_persists_private_coordinates_without_publishing():
    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={
            "payload": json.dumps(
                valid_payload(
                    lastSeenLatitude=7.1193,
                    lastSeenLongitude=-73.1227,
                )
            )
        },
        files=photos_form(1),
        headers=IDEMPOTENCY,
    )

    assert response.status_code == 201
    stored = repository.created_report
    assert stored.last_seen_latitude == 7.1193
    assert stored.last_seen_longitude == -73.1227
    # La constancia pública no expone las coordenadas privadas.
    assert set(response.json().keys()) == {
        "id", "publicCaseCode", "status", "receivedAt"
    }


@pytest.mark.anyio
async def test_report_without_coordinates_still_accepted():
    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository)

    response = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={"payload": json.dumps(valid_payload())},
        files=photos_form(1),
        headers=IDEMPOTENCY,
    )

    assert response.status_code == 201
    assert repository.created_report.last_seen_latitude is None
    assert repository.created_report.last_seen_longitude is None


# CHG-073 — Listas cerradas y fecha de nacimiento plausible.


@pytest.mark.anyio
async def test_report_validates_person_field_options():
    app = report_app()

    async def post(payload):
        return await request_app(
            app,
            "POST",
            "/internal/v1/missing-person-reports",
            data={"payload": json.dumps(payload)},
            files=photos_form(1),
            headers=IDEMPOTENCY,
        )

    valid = await post(
        valid_payload(
            genderIdentity="Mujer",
            nationality="Colombiana",
            documentType="Cédula de ciudadanía",
            birthDate="1990-05-20",
        )
    )
    assert valid.status_code == 201

    bad_sex = await post(valid_payload(genderIdentity="No binaria"))
    assert bad_sex.status_code == 422

    bad_nationality = await post(valid_payload(nationality="Marciana"))
    assert bad_nationality.status_code == 422

    bad_document = await post(
        valid_payload(documentType="Licencia de conducción")
    )
    assert bad_document.status_code == 422

    future_birth = await post(valid_payload(birthDate="2100-01-01"))
    assert future_birth.status_code == 422

    ancient_birth = await post(valid_payload(birthDate="1850-01-01"))
    assert ancient_birth.status_code == 422


# --- CHG-075: publicación inmediata del reporte ---


@pytest.mark.anyio
async def test_report_without_review_acknowledged_is_accepted():
    # La revisión previa ya no existe; los clientes nuevos no envían
    # el campo y los antiguos (que mandan True) siguen funcionando.
    payload = valid_payload()
    payload.pop("reviewAcknowledged")
    app = report_app()

    response = await request_app(
        app,
        "POST",
        "/internal/v1/missing-person-reports",
        data={"payload": json.dumps(payload)},
        files=photos_form(1),
        headers=IDEMPOTENCY,
    )

    assert response.status_code == 201
    assert response.json()["status"] == "published"


def test_public_case_projection_composes_public_fields():
    from datetime import date

    from app.repository import PostgresDisasterRepository, StoredReport

    report = StoredReport(
        id=uuid4(),
        idempotency_key="clave-idempotente-0002",
        public_case_code="MP-2026-BBBB2222",
        first_names="  Ana María ",
        last_names=" Pérez ",
        aliases="Anita, La Mona;  ",
        birth_date=date(1990, 9, 1),
        approximate_age=None,
        gender_identity="Mujer",
        nationality="Colombiana",
        document_type_encrypted=None,
        document_number_encrypted=None,
        height_cm=165,
        build="delgada",
        skin_tone="trigueña",
        hair_description="negro largo",
        eye_description="cafés",
        distinctive_marks="tatuaje en el brazo",
        medical_information_encrypted=None,
        last_seen_date=date(2026, 8, 11),
        last_seen_time="18:30",
        last_seen_latitude=None,
        last_seen_longitude=None,
        department="Santander",
        municipality="Bucaramanga",
        last_seen_area="Café Madrid",
        clothing_description="Chaqueta azul",
        circumstances="Salió y no regresó.",
        additional_description=None,
        reporter_name_encrypted=b"x",
        reporter_relationship="Hermana",
        reporter_phone_encrypted=None,
        reporter_email_encrypted=None,
        official_report_number=None,
    )

    (
        display_name,
        aliases,
        approximate_age,
        last_seen_at,
        physical_description,
    ) = PostgresDisasterRepository._public_case_projection(report)

    assert display_name == "Ana María Pérez"
    assert aliases == ["Anita", "La Mona"]
    # Edad derivada de la fecha de nacimiento (aún no cumple años).
    assert approximate_age == 35
    assert last_seen_at.isoformat() == "2026-08-11T18:30:00-05:00"
    assert physical_description == (
        "Estatura 165 cm · Contextura: delgada · Piel: trigueña · "
        "Cabello: negro largo · Ojos: cafés"
    )


# --- CHG-082: señal de cambios para el refresco en vivo ---


@pytest.mark.anyio
async def test_platform_change_signal_shape():
    repository = FakeMissingPersonRepository()
    repository.platform_change_signal = (  # type: ignore[attr-defined]
        lambda: _async_signal()
    )
    app = report_app(repository=repository)

    response = await request_app(
        app, "GET", "/internal/v1/platform/change-signal"
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"signal", "generatedAt"}
    assert body["signal"] == "abc123"


async def _async_signal() -> str:
    return "abc123"


# --- CHG-094: campos ampliados del reporte de persona ---


@pytest.mark.anyio
async def test_extended_fields_are_stored_with_health_data_encrypted():
    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository)

    response = await post_report(
        app,
        payload=valid_payload(
            tattooDescription="Tatuaje de ancla en antebrazo izquierdo",
            scarsDescription="Cicatriz de 4 cm en la rodilla derecha",
            prostheticsDescription="Prótesis auditiva en oído derecho",
            piercingsAndMoles="Lunar visible en la mejilla izquierda",
            mentalHealthCondition="Diagnóstico de Alzheimer inicial",
            vitalMedication="Insulina cada 8 horas",
            severeAllergies="Alergia grave a la penicilina",
            belongingsDescription="Mochila azul con computador portátil",
            transportMode="private_vehicle",
            vehicleDetails="Placa ABC123, Renault Logan gris",
            companionsDescription="Salió con un vecino del barrio",
            officialAuthorityName="Fiscalía General de la Nación",
            isReporterPhonePublic=True,
            isReporterEmailPublic=False,
        ),
    )

    assert response.status_code == 201
    stored = repository.created_report
    # Identificación física: en claro, sirve para reconocer.
    assert stored.tattoo_description == (
        "Tatuaje de ancla en antebrazo izquierdo"
    )
    assert stored.piercings_and_moles.startswith("Lunar visible")
    assert stored.belongings_description.startswith("Mochila azul")
    assert stored.transport_mode == "private_vehicle"
    assert stored.companions_description.startswith("Salió con")
    assert stored.official_authority_name == (
        "Fiscalía General de la Nación"
    )
    # Salud y placa: cifradas, nunca en claro.
    for encrypted, leak in (
        (stored.mental_health_condition_encrypted, b"Alzheimer"),
        (stored.vital_medication_encrypted, b"Insulina"),
        (stored.severe_allergies_encrypted, b"penicilina"),
        (stored.vehicle_details_encrypted, b"ABC123"),
    ):
        assert encrypted is not None
        assert leak not in encrypted
    # Consentimiento explícito del reportante.
    assert stored.reporter_phone_public is True
    assert stored.reporter_email_public is False


@pytest.mark.anyio
async def test_photo_categories_must_match_photo_count():
    app = report_app()

    response = await post_report(
        app,
        payload=valid_payload(
            photoCategories=["recent_face", "full_body"],
        ),
        photos=1,
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_photo_categories_are_stored_per_photo():
    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository)

    response = await post_report(
        app,
        payload=valid_payload(
            photoCategories=["recent_face", "distinctive_mark"],
        ),
        photos=2,
    )

    assert response.status_code == 201
    categories = [photo.category for photo in repository.created_photos]
    assert categories == ["recent_face", "distinctive_mark"]


@pytest.mark.anyio
async def test_unknown_transport_mode_is_rejected():
    app = report_app()

    response = await post_report(
        app, payload=valid_payload(transportMode="teleport")
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_vehicle_details_require_a_vehicle_transport_mode():
    app = report_app()

    response = await post_report(
        app,
        payload=valid_payload(
            transportMode="on_foot",
            vehicleDetails="Placa ABC123",
        ),
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_report_without_new_fields_still_works():
    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository)

    response = await post_report(app)

    assert response.status_code == 201
    stored = repository.created_report
    assert stored.tattoo_description is None
    assert stored.reporter_phone_public is False


# --- CHG-100: el teléfono de contacto se valida contra E.164 ---


@pytest.mark.anyio
async def test_absurdly_long_phone_is_rejected():
    app = report_app()

    # El caso del reporte: 25 dígitos pasaban el límite anterior de 40
    # y quedaban guardados como contacto inservible.
    response = await post_report(
        app,
        payload=valid_payload(
            reporterPhone="3345676543234565432356543",
        ),
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_real_phone_formats_are_accepted():
    for phone in [
        "3001234567",
        "+57 300 123 4567",
        "(607) 634-5678",
        "+123456789012345",
    ]:
        repository = FakeMissingPersonRepository()
        app = report_app(repository=repository)

        response = await post_report(
            app, payload=valid_payload(reporterPhone=phone)
        )

        assert response.status_code == 201, phone


@pytest.mark.anyio
async def test_phone_without_digits_or_leading_zero_is_rejected():
    app = report_app()

    for phone in ["12345", "0300123456", "sin-numero"]:
        response = await post_report(
            app, payload=valid_payload(reporterPhone=phone)
        )
        assert response.status_code == 422, phone


# --- CHG-105: fotografía pública del caso ---


def test_public_photo_prefers_the_declared_face_photo():
    from app.repository import StoredPhoto, select_public_photo_key

    def foto(position, category=None):
        return StoredPhoto(
            id=uuid4(),
            position=position,
            storage_key=f"cuarentena/{position}.bin",
            derived_storage_key=f"derivado/{position}.jpg",
            content_type="image/jpeg",
            size_bytes=1024,
            sha256="x",
            exif_removed=True,
            malware_scan="clean",
            category=category,
        )

    # Con categorías declaradas gana el rostro reciente, no la primera.
    elegida = select_public_photo_key(
        [foto(1, "full_body"), foto(2, "recent_face"), foto(3)]
    )
    assert elegida == "derivado/2.jpg"

    # Sin categorías, la primera que envió.
    assert select_public_photo_key([foto(2), foto(1)]) == "derivado/1.jpg"

    # Sin fotos no hay nada que publicar.
    assert select_public_photo_key([]) is None


def test_public_photo_never_exposes_the_quarantined_original():
    from app.repository import StoredPhoto, select_public_photo_key

    photo = StoredPhoto(
        id=uuid4(),
        position=1,
        storage_key="cuarentena/original.bin",
        derived_storage_key="derivado/limpio.jpg",
        content_type="image/jpeg",
        size_bytes=1024,
        sha256="x",
        exif_removed=True,
        malware_scan="clean",
    )

    # Siempre el derivado sin EXIF; el original queda en el expediente.
    assert select_public_photo_key([photo]) == "derivado/limpio.jpg"


def test_public_photo_url_is_built_from_the_public_case_id():
    from app.repository import public_photo_url_for

    case_id = UUID("55555555-5555-4555-8555-555555555501")
    url = public_photo_url_for(case_id, "derivado/limpio.jpg")

    assert url == f"/api/v1/public/missing-persons/{case_id}/photo"
    # La clave del objeto lleva el id del expediente privado: jamás
    # puede aparecer en la URL pública.
    assert "derivado" not in url
    # Sin foto publicada no hay URL.
    assert public_photo_url_for(case_id, None) is None


# --- CHG-106: la última visualización cae dentro de la cobertura ---


@pytest.mark.anyio
async def test_last_seen_before_platform_coverage_is_rejected():
    app = report_app()

    # El calendario ya no ofrece años previos; esto cubre el envío
    # armado a mano.
    for fecha in ["2025-12-31", "2015-06-01", "1999-01-01"]:
        response = await post_report(
            app, payload=valid_payload(lastSeenDate=fecha)
        )
        assert response.status_code == 422, fecha


@pytest.mark.anyio
async def test_last_seen_within_coverage_is_accepted():
    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository)

    response = await post_report(
        app, payload=valid_payload(lastSeenDate="2026-01-01")
    )

    assert response.status_code == 201


# --- CHG-113: la vestimenta es opcional ---


@pytest.mark.anyio
async def test_report_without_clothing_description_is_accepted():
    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository)

    payload = valid_payload()
    payload.pop("clothingDescription")

    response = await post_report(app, payload=payload)

    assert response.status_code == 201
    assert repository.created_report.clothing_description is None


@pytest.mark.anyio
async def test_blank_clothing_description_is_stored_as_absent():
    # Quien no sabe la ropa y deja el campo en blanco dice lo mismo que
    # quien lo omite: se guarda como ausencia, no como cadena vacía, o
    # la ficha pública mostraría "Vestimenta:" seguido de nada en vez de
    # su texto de respaldo.
    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository)

    response = await post_report(
        app, payload=valid_payload(clothingDescription="   ")
    )

    assert response.status_code == 201
    assert repository.created_report.clothing_description is None


@pytest.mark.anyio
async def test_clothing_description_is_still_stored_when_known():
    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository)

    response = await post_report(
        app,
        payload=valid_payload(
            clothingDescription="Chaqueta amarilla y jean azul"
        ),
    )

    assert response.status_code == 201
    assert repository.created_report.clothing_description == (
        "Chaqueta amarilla y jean azul"
    )


@pytest.mark.anyio
async def test_clothing_description_still_has_a_length_limit():
    app = report_app()

    response = await post_report(
        app, payload=valid_payload(clothingDescription="x" * 1001)
    )

    assert response.status_code == 422


# --- CHG-114: el rechazo se lee y señala el campo ---


@pytest.mark.anyio
async def test_rejection_names_fields_in_spanish_and_lists_keys():
    # El reporte de UX mostraba "Revisa los campos: reporterPhone.":
    # la clave del modelo, en inglés, delante de quien está
    # denunciando una desaparición.
    app = report_app()

    response = await post_report(
        app, payload=valid_payload(reporterPhone="3001234567-")
    )

    assert response.status_code == 422
    cuerpo = response.json()
    assert cuerpo["detail"] == (
        "Revisa los campos: Teléfono del reportante."
    )
    # La clave viaja aparte: es lo que permite al formulario resaltar
    # el campo y desplazarse hasta él.
    assert cuerpo["fields"] == ["reporterPhone"]


@pytest.mark.anyio
async def test_rejection_lists_every_invalid_field_with_its_label():
    app = report_app()

    payload = valid_payload(reporterPhone="3001234567-")
    payload["circumstances"] = ""

    response = await post_report(app, payload=payload)

    assert response.status_code == 422
    cuerpo = response.json()
    assert cuerpo["fields"] == ["circumstances", "reporterPhone"]
    assert cuerpo["detail"] == (
        "Revisa los campos: Circunstancias, Teléfono del reportante."
    )


@pytest.mark.anyio
async def test_unknown_field_key_is_shown_as_is():
    # Una clave sin etiqueta conocida se muestra tal cual: peor que una
    # etiqueta, mejor que un hueco.
    from app.main import field_label, invalid_fields_detail

    assert field_label("campoQueNoExiste") == "campoQueNoExiste"
    assert invalid_fields_detail(["campoQueNoExiste"]) == (
        "Revisa los campos: campoQueNoExiste."
    )


# --- CHG-115: la edad se deriva de la fecha de nacimiento ---


def test_age_counts_the_day_not_only_the_year():
    from datetime import date

    from app.models import age_from_birth_date

    # El caso de la captura: 2004-11-23 no son 12 años.
    assert age_from_birth_date(date(2004, 11, 23), date(2026, 8, 16)) == 21
    # Víspera del cumpleaños: todavía no los cumple.
    assert age_from_birth_date(date(2004, 11, 23), date(2026, 11, 22)) == 21
    # El día del cumpleaños ya cuenta.
    assert age_from_birth_date(date(2004, 11, 23), date(2026, 11, 23)) == 22
    # Nacido un 29 de febrero, en un año sin ese día.
    assert age_from_birth_date(date(2004, 2, 29), date(2026, 2, 28)) == 21
    assert age_from_birth_date(date(2004, 2, 29), date(2026, 3, 1)) == 22


@pytest.mark.anyio
async def test_report_recomputes_age_from_birth_date():
    from datetime import date, timedelta

    from app.models import age_from_birth_date

    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository)

    nacimiento = date.today() - timedelta(days=365 * 30 + 8)
    esperada = age_from_birth_date(nacimiento, date.today())

    # La edad recibida es la del defecto: escrita a mano y contradiciendo
    # a la fecha. El servicio la descarta.
    response = await post_report(
        app,
        payload=valid_payload(
            birthDate=nacimiento.isoformat(), approximateAge=12
        ),
    )

    assert response.status_code == 201
    assert repository.created_report.approximate_age == esperada
    assert esperada != 12


@pytest.mark.anyio
async def test_manual_age_is_kept_without_birth_date():
    # Sin fecha de nacimiento la edad aproximada es el único dato que
    # hay, y es el caso normal en una desaparición.
    repository = FakeMissingPersonRepository()
    app = report_app(repository=repository)

    payload = valid_payload(approximateAge=34)
    payload.pop("birthDate", None)

    response = await post_report(app, payload=payload)

    assert response.status_code == 201
    assert repository.created_report.approximate_age == 34


@pytest.mark.anyio
async def test_birth_date_beyond_the_age_ceiling_is_rejected():
    # 120 es el tope del modelo y del CHECK de la base: guardar la edad
    # recortada sería volver a tener un dato que miente.
    app = report_app()

    response = await post_report(
        app, payload=valid_payload(birthDate="1900-01-01")
    )

    assert response.status_code == 422
