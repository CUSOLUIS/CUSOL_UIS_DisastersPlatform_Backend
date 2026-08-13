from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

import asyncpg

from .models import (
    DataClassification,
    DisasterEvent,
    HumanImpactSummary,
    MissingPersonPublicRecord,
    MissingPersonReportReceipt,
    OperationalMapPoint,
    PersonRecord,
    SourceReference,
    VerificationStatus,
)


@dataclass(frozen=True)
class HumanMapCell:
    """Agregado por celda de grilla para la capa humana (CHG-015).

    Cuando `count == 1` los campos `point_*` describen el único punto
    público anónimo de la celda; nunca incluyen identidad.
    """

    cell_x: int
    cell_y: int
    count: int
    missing: int
    reported_deceased: int
    confirmed_alive: int
    confirmed_deceased: int
    latitude: float
    longitude: float
    west: float
    south: float
    east: float
    north: float
    all_operational: bool
    point_id: UUID | None = None
    point_status: str | None = None
    point_precision: str | None = None
    point_verification: str | None = None
    point_source_name: str | None = None
    point_source_type: str | None = None
    point_source_url: str | None = None
    point_updated_at: datetime | None = None


@dataclass(frozen=True)
class StoredReport:
    """Reporte privado listo para persistir; lo sensible llega cifrado."""

    id: UUID
    idempotency_key: str
    public_case_code: str
    first_names: str
    last_names: str
    aliases: str | None
    birth_date: date | None
    approximate_age: int | None
    gender_identity: str | None
    nationality: str | None
    document_type_encrypted: bytes | None
    document_number_encrypted: bytes | None
    height_cm: int | None
    build: str | None
    skin_tone: str | None
    hair_description: str | None
    eye_description: str | None
    distinctive_marks: str | None
    medical_information_encrypted: bytes | None
    last_seen_date: date
    last_seen_time: str | None
    # CHG-015: coordenadas privadas; nunca se publican automáticamente.
    last_seen_latitude: float | None
    last_seen_longitude: float | None
    department: str
    municipality: str
    last_seen_area: str
    clothing_description: str
    circumstances: str
    additional_description: str | None
    reporter_name_encrypted: bytes
    reporter_relationship: str
    reporter_phone_encrypted: bytes | None
    reporter_email_encrypted: bytes | None
    official_report_number: str | None


@dataclass(frozen=True)
class StoredPhoto:
    id: UUID
    position: int
    storage_key: str
    derived_storage_key: str
    content_type: str
    size_bytes: int
    sha256: str
    exif_removed: bool
    malware_scan: str


class DisasterRepository(Protocol):
    async def ping(self) -> bool: ...

    async def list_events(
        self,
        disaster_type: str | None,
        verification_status: VerificationStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DisasterEvent], int]: ...

    async def people_overview(
        self,
        recent_limit: int,
    ) -> tuple[HumanImpactSummary, list[PersonRecord]]: ...

    async def operational_map_overview(
        self,
        limit: int,
    ) -> tuple[list[OperationalMapPoint], DataClassification]: ...

    async def human_map_overview(
        self,
        west: float,
        south: float,
        east: float,
        north: float,
        cell_size: float,
        statuses: list[str] | None,
    ) -> tuple[list[HumanMapCell], int]: ...

    async def search_missing_persons(
        self,
        query: str,
        limit: int,
    ) -> tuple[list[MissingPersonPublicRecord], int]: ...

    async def create_missing_person_report(
        self,
        report: StoredReport,
        photos: list[StoredPhoto],
    ) -> tuple[MissingPersonReportReceipt, bool]: ...


class PostgresDisasterRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def ping(self) -> bool:
        return await self._pool.fetchval("SELECT 1") == 1

    async def list_events(
        self,
        disaster_type: str | None,
        verification_status: VerificationStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[DisasterEvent], int]:
        clauses: list[str] = []
        filter_values: list[object] = []

        if disaster_type is not None:
            filter_values.append(disaster_type)
            clauses.append(
                f"e.disaster_type = ${len(filter_values)}"
            )

        if verification_status is not None:
            filter_values.append(verification_status)
            clauses.append(
                f"e.verification_status = ${len(filter_values)}"
            )

        where_clause = (
            "WHERE " + " AND ".join(clauses) if clauses else ""
        )
        total = await self._pool.fetchval(
            f"""
            SELECT COUNT(*)
            FROM disaster_service.disaster_events e
            {where_clause}
            """,
            *filter_values,
        )

        query_values = [*filter_values, limit, offset]
        limit_parameter = len(filter_values) + 1
        offset_parameter = len(filter_values) + 2
        rows = await self._pool.fetch(
            f"""
            SELECT
                e.id,
                e.title,
                e.description,
                e.disaster_type,
                e.severity,
                e.verification_status,
                CASE
                    WHEN e.location IS NULL THEN NULL
                    ELSE ST_Y(e.location::geometry)
                END AS latitude,
                CASE
                    WHEN e.location IS NULL THEN NULL
                    ELSE ST_X(e.location::geometry)
                END AS longitude,
                e.occurred_at,
                e.updated_at,
                s.name AS source_name,
                s.source_type,
                s.url AS source_url
            FROM disaster_service.disaster_events e
            INNER JOIN disaster_service.sources s ON s.id = e.source_id
            {where_clause}
            ORDER BY e.updated_at DESC
            LIMIT ${limit_parameter} OFFSET ${offset_parameter}
            """,
            *query_values,
        )

        events = [
            DisasterEvent(
                id=row["id"],
                title=row["title"],
                description=row["description"],
                disaster_type=row["disaster_type"],
                severity=row["severity"],
                verification_status=row["verification_status"],
                latitude=row["latitude"],
                longitude=row["longitude"],
                occurred_at=row["occurred_at"],
                updated_at=row["updated_at"],
                source=SourceReference(
                    name=row["source_name"],
                    source_type=row["source_type"],
                    url=row["source_url"],
                ),
            )
            for row in rows
        ]
        return events, int(total)

    async def people_overview(
        self,
        recent_limit: int,
    ) -> tuple[HumanImpactSummary, list[PersonRecord]]:
        counts = await self._pool.fetch(
            """
            SELECT status, COUNT(*) AS quantity
            FROM disaster_service.people
            GROUP BY status
            """
        )
        by_status = {
            row["status"]: int(row["quantity"]) for row in counts
        }
        summary = HumanImpactSummary(
            missing=by_status.get("missing", 0),
            reported_deceased=by_status.get("reported_deceased", 0),
            confirmed_alive=by_status.get("confirmed_alive", 0),
            confirmed_deceased=by_status.get("confirmed_deceased", 0),
        )

        rows = await self._pool.fetch(
            """
            SELECT
                p.id,
                p.display_name,
                p.status,
                p.location,
                p.related_event,
                p.created_at,
                s.name AS source_name,
                s.source_type,
                s.url AS source_url
            FROM disaster_service.people p
            INNER JOIN disaster_service.sources s ON s.id = p.source_id
            ORDER BY p.created_at DESC
            LIMIT $1
            """,
            recent_limit,
        )
        recent = [
            PersonRecord(
                id=row["id"],
                display_name=row["display_name"],
                status=row["status"],
                location=row["location"],
                related_event=row["related_event"],
                created_at=row["created_at"],
                source=SourceReference(
                    name=row["source_name"],
                    source_type=row["source_type"],
                    url=row["source_url"],
                ),
            )
            for row in rows
        ]
        return summary, recent

    async def operational_map_overview(
        self,
        limit: int,
    ) -> tuple[list[OperationalMapPoint], DataClassification]:
        rows = await self._pool.fetch(
            """
            SELECT
                p.id,
                p.category,
                p.title,
                p.description,
                p.location_label,
                ST_Y(p.location::geometry) AS latitude,
                ST_X(p.location::geometry) AS longitude,
                p.coordinate_precision,
                p.verification_status,
                p.related_disaster_id,
                p.data_classification,
                p.updated_at,
                s.name AS source_name,
                s.source_type,
                s.url AS source_url
            FROM disaster_service.operational_map_points p
            INNER JOIN disaster_service.sources s ON s.id = p.source_id
            WHERE ST_Y(p.location::geometry) BETWEEN -90 AND 90
              AND ST_X(p.location::geometry) BETWEEN -180 AND 180
            ORDER BY p.updated_at DESC
            LIMIT $1
            """,
            limit,
        )
        points = [
            OperationalMapPoint(
                id=row["id"],
                category=row["category"],
                title=row["title"],
                location_label=row["location_label"],
                latitude=row["latitude"],
                longitude=row["longitude"],
                coordinate_precision=row["coordinate_precision"],
                verification_status=row["verification_status"],
                related_disaster_id=row["related_disaster_id"],
                description=row["description"],
                source=SourceReference(
                    name=row["source_name"],
                    source_type=row["source_type"],
                    url=row["source_url"],
                ),
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
        operational = bool(rows) and all(
            row["data_classification"] == "operational" for row in rows
        )
        classification: DataClassification = (
            "operational" if operational else "demonstrative"
        )
        return points, classification

    async def human_map_overview(
        self,
        west: float,
        south: float,
        east: float,
        north: float,
        cell_size: float,
        statuses: list[str] | None,
    ) -> tuple[list[HumanMapCell], int]:
        # Una sola consulta agregada por celda de grilla (sin N+1); el
        # filtro espacial usa && sobre geography para aprovechar el GIST.
        rows = await self._pool.fetch(
            """
            WITH filtered AS (
                SELECT
                    pr.id AS public_id,
                    ST_Y(pr.location::geometry) AS latitude,
                    ST_X(pr.location::geometry) AS longitude,
                    pr.coordinate_precision,
                    pr.verification_status,
                    pr.data_classification,
                    pr.updated_at,
                    p.status,
                    s.name AS source_name,
                    s.source_type,
                    s.url AS source_url
                FROM disaster_service.people_map_projection pr
                INNER JOIN disaster_service.people p
                    ON p.id = pr.person_id
                INNER JOIN disaster_service.sources s
                    ON s.id = p.source_id
                WHERE pr.visibility = 'published'
                  AND (
                    $5::text[] IS NULL
                    OR p.status::text = ANY($5::text[])
                  )
                  AND pr.location && ST_MakeEnvelope(
                        $1, $2, $3, $4, 4326
                      )::geography
            )
            SELECT
                floor((longitude + 180.0) / $6)::int AS cell_x,
                floor((latitude + 90.0) / $6)::int AS cell_y,
                COUNT(*)::int AS count,
                COUNT(*) FILTER (WHERE status = 'missing')::int
                    AS missing,
                COUNT(*) FILTER (WHERE status = 'reported_deceased')::int
                    AS reported_deceased,
                COUNT(*) FILTER (WHERE status = 'confirmed_alive')::int
                    AS confirmed_alive,
                COUNT(*) FILTER (WHERE status = 'confirmed_deceased')::int
                    AS confirmed_deceased,
                AVG(latitude) AS latitude,
                AVG(longitude) AS longitude,
                MIN(longitude) AS west,
                MIN(latitude) AS south,
                MAX(longitude) AS east,
                MAX(latitude) AS north,
                bool_and(data_classification = 'operational')
                    AS all_operational,
                (array_agg(public_id ORDER BY public_id))[1]
                    AS point_id,
                (array_agg(status ORDER BY public_id))[1]
                    AS point_status,
                (array_agg(coordinate_precision ORDER BY public_id))[1]
                    AS point_precision,
                (array_agg(verification_status ORDER BY public_id))[1]
                    AS point_verification,
                (array_agg(source_name ORDER BY public_id))[1]
                    AS point_source_name,
                (array_agg(source_type ORDER BY public_id))[1]
                    AS point_source_type,
                (array_agg(source_url ORDER BY public_id))[1]
                    AS point_source_url,
                (array_agg(updated_at ORDER BY public_id))[1]
                    AS point_updated_at
            FROM filtered
            GROUP BY cell_x, cell_y
            ORDER BY count DESC, cell_x, cell_y
            """,
            west,
            south,
            east,
            north,
            statuses,
            cell_size,
        )
        cells = [
            HumanMapCell(
                cell_x=row["cell_x"],
                cell_y=row["cell_y"],
                count=row["count"],
                missing=row["missing"],
                reported_deceased=row["reported_deceased"],
                confirmed_alive=row["confirmed_alive"],
                confirmed_deceased=row["confirmed_deceased"],
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                west=float(row["west"]),
                south=float(row["south"]),
                east=float(row["east"]),
                north=float(row["north"]),
                all_operational=row["all_operational"],
                point_id=row["point_id"],
                point_status=row["point_status"],
                point_precision=row["point_precision"],
                point_verification=row["point_verification"],
                point_source_name=row["point_source_name"],
                point_source_type=row["point_source_type"],
                point_source_url=row["point_source_url"],
                point_updated_at=row["point_updated_at"],
            )
            for row in rows
        ]
        unmapped = await self._pool.fetchval(
            """
            SELECT COUNT(*)
            FROM disaster_service.people p
            WHERE (
                $1::text[] IS NULL
                OR p.status::text = ANY($1::text[])
            )
            AND NOT EXISTS (
                SELECT 1
                FROM disaster_service.people_map_projection pr
                WHERE pr.person_id = p.id
                  AND pr.visibility = 'published'
            )
            """,
            statuses,
        )
        return cells, int(unmapped)

    async def search_missing_persons(
        self,
        query: str,
        limit: int,
    ) -> tuple[list[MissingPersonPublicRecord], int]:
        # Solo casos publicados y solo la proyección pública; unaccent y
        # lower en ambos lados cubren tildes y mayúsculas.
        condition = """
            publication_status = 'published'
            AND (
                lower(unaccent(display_name)) LIKE
                    '%' || lower(unaccent($1)) || '%'
                OR lower(unaccent(array_to_string(aliases, ' '))) LIKE
                    '%' || lower(unaccent($1)) || '%'
                OR lower(public_case_code) LIKE '%' || lower($1) || '%'
                OR approximate_age::TEXT = $1
                OR lower(unaccent(municipality)) LIKE
                    '%' || lower(unaccent($1)) || '%'
                OR lower(unaccent(department)) LIKE
                    '%' || lower(unaccent($1)) || '%'
                OR lower(unaccent(last_seen_area)) LIKE
                    '%' || lower(unaccent($1)) || '%'
                OR lower(unaccent(coalesce(clothing_description, '')))
                    LIKE '%' || lower(unaccent($1)) || '%'
                OR lower(unaccent(coalesce(physical_description, '')))
                    LIKE '%' || lower(unaccent($1)) || '%'
                OR lower(unaccent(coalesce(distinctive_marks, '')))
                    LIKE '%' || lower(unaccent($1)) || '%'
            )
        """
        pattern_source = query.strip()
        total = await self._pool.fetchval(
            f"""
            SELECT COUNT(*)
            FROM disaster_service.missing_person_cases
            WHERE {condition}
            """,
            pattern_source,
        )
        rows = await self._pool.fetch(
            f"""
            SELECT
                id, public_case_code, display_name, aliases,
                approximate_age, last_seen_at, last_seen_area,
                municipality, department, clothing_description,
                physical_description, distinctive_marks,
                public_photo_url, map_point_id, updated_at,
                data_classification
            FROM disaster_service.missing_person_cases
            WHERE {condition}
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            pattern_source,
            limit,
        )
        records = [
            MissingPersonPublicRecord(
                id=row["id"],
                public_case_code=row["public_case_code"],
                display_name=row["display_name"],
                aliases=list(row["aliases"]),
                approximate_age=row["approximate_age"],
                last_seen_at=row["last_seen_at"],
                last_seen_area=row["last_seen_area"],
                municipality=row["municipality"],
                department=row["department"],
                clothing_description=row["clothing_description"],
                physical_description=row["physical_description"],
                distinctive_marks=row["distinctive_marks"],
                public_photo_url=row["public_photo_url"],
                map_point_id=row["map_point_id"],
                updated_at=row["updated_at"],
                data_classification=row["data_classification"],
            )
            for row in rows
        ]
        return records, int(total)

    async def create_missing_person_report(
        self,
        report: StoredReport,
        photos: list[StoredPhoto],
    ) -> tuple[MissingPersonReportReceipt, bool]:
        async with self._pool.acquire() as connection:
            try:
                async with connection.transaction():
                    row = await connection.fetchrow(
                        """
                        INSERT INTO disaster_service.missing_person_reports (
                            id, idempotency_key, public_case_code,
                            first_names, last_names, aliases, birth_date,
                            approximate_age, gender_identity, nationality,
                            document_type_encrypted,
                            document_number_encrypted,
                            height_cm, build, skin_tone, hair_description,
                            eye_description, distinctive_marks,
                            medical_information_encrypted,
                            last_seen_date, last_seen_time, department,
                            municipality, last_seen_area,
                            clothing_description, circumstances,
                            additional_description,
                            reporter_name_encrypted, reporter_relationship,
                            reporter_phone_encrypted,
                            reporter_email_encrypted,
                            official_report_number,
                            last_seen_latitude, last_seen_longitude
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                            $11, $12, $13, $14, $15, $16, $17, $18, $19,
                            $20, $21, $22, $23, $24, $25, $26, $27, $28,
                            $29, $30, $31, $32, $33, $34
                        )
                        RETURNING id, public_case_code, status, received_at
                        """,
                        report.id,
                        report.idempotency_key,
                        report.public_case_code,
                        report.first_names,
                        report.last_names,
                        report.aliases,
                        report.birth_date,
                        report.approximate_age,
                        report.gender_identity,
                        report.nationality,
                        report.document_type_encrypted,
                        report.document_number_encrypted,
                        report.height_cm,
                        report.build,
                        report.skin_tone,
                        report.hair_description,
                        report.eye_description,
                        report.distinctive_marks,
                        report.medical_information_encrypted,
                        report.last_seen_date,
                        report.last_seen_time,
                        report.department,
                        report.municipality,
                        report.last_seen_area,
                        report.clothing_description,
                        report.circumstances,
                        report.additional_description,
                        report.reporter_name_encrypted,
                        report.reporter_relationship,
                        report.reporter_phone_encrypted,
                        report.reporter_email_encrypted,
                        report.official_report_number,
                        report.last_seen_latitude,
                        report.last_seen_longitude,
                    )
                    for photo in photos:
                        await connection.execute(
                            """
                            INSERT INTO
                                disaster_service.missing_person_report_photos (
                                id, report_id, position, storage_key,
                                derived_storage_key, content_type,
                                size_bytes, sha256, exif_removed,
                                malware_scan
                            ) VALUES (
                                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
                            )
                            """,
                            photo.id,
                            report.id,
                            photo.position,
                            photo.storage_key,
                            photo.derived_storage_key,
                            photo.content_type,
                            photo.size_bytes,
                            photo.sha256,
                            photo.exif_removed,
                            photo.malware_scan,
                        )
                    await connection.execute(
                        """
                        INSERT INTO disaster_service.missing_person_audit (
                            event_type, report_id, detail
                        ) VALUES ($1, $2, $3)
                        """,
                        "report_received",
                        report.id,
                        f"fotos={len(photos)}",
                    )
            except asyncpg.UniqueViolationError:
                # Reintento idempotente: devolver la constancia original.
                existing = await connection.fetchrow(
                    """
                    SELECT id, public_case_code, status, received_at
                    FROM disaster_service.missing_person_reports
                    WHERE idempotency_key = $1
                    """,
                    report.idempotency_key,
                )
                if existing is None:
                    raise
                return (
                    MissingPersonReportReceipt(
                        id=existing["id"],
                        public_case_code=existing["public_case_code"],
                        status=existing["status"],
                        received_at=existing["received_at"],
                    ),
                    False,
                )
        return (
            MissingPersonReportReceipt(
                id=row["id"],
                public_case_code=row["public_case_code"],
                status=row["status"],
                received_at=row["received_at"],
            ),
            True,
        )
