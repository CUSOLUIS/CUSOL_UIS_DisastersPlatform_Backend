"""Pruebas CHG-015 — capa geográfica escalable de situación humana."""

import math
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from app.main import create_app
from app.repository import HumanMapCell


ANCHORS = [
    (7.1310, -73.1205),
    (7.0700, -73.1730),
    (7.1132, -73.2181),
    (7.0660, -73.3790),
    (6.9500, -73.0300),
    (7.0890, -73.1050),
    (6.8550, -73.1030),
    (7.1310, -73.1150),
    (6.9870, -73.0490),
    (7.0800, -73.0700),
]
STATUSES = [
    "missing",
    "confirmed_alive",
    "reported_deceased",
    "confirmed_deceased",
]
NATIONAL = "west=-79.0&south=-4.3&east=-66.8&north=12.6&zoom=5"


def make_rows(count: int = 2000) -> list[dict]:
    rows = []
    for n in range(count):
        anchor_latitude, anchor_longitude = ANCHORS[n % len(ANCHORS)]
        rows.append(
            {
                "id": UUID(int=n + 1),
                "lat": anchor_latitude
                + ((n * 7919) % 1997) / 1997.0 * 0.04
                - 0.02,
                "lon": anchor_longitude
                + ((n * 104729) % 1499) / 1499.0 * 0.04
                - 0.02,
                "status": STATUSES[n % len(STATUSES)],
                "precision": "approximate" if n % 3 else "municipality",
                "verification": "under_review",
                "source_name": "Reporte ciudadano — plataforma CUSOL",
                "source_type": "citizen",
                "source_url": None,
                "classification": "demonstrative",
                "updated_at": datetime(2026, 8, 12, tzinfo=UTC),
                "visibility": "published",
            }
        )
    return rows


class FakeHumanMapRepository:
    """Réplica en Python de la agregación por celda del repositorio SQL."""

    def __init__(self, rows=None, unmapped: int = 0):
        self.rows = rows if rows is not None else []
        self.unmapped = unmapped

    async def ping(self) -> bool:
        return True

    async def human_map_overview(
        self, west, south, east, north, cell_size, statuses
    ):
        filtered = [
            row
            for row in self.rows
            if row["visibility"] == "published"
            and (statuses is None or row["status"] in statuses)
            and west <= row["lon"] <= east
            and south <= row["lat"] <= north
        ]
        grouped: dict[tuple[int, int], list[dict]] = {}
        for row in filtered:
            key = (
                math.floor((row["lon"] + 180.0) / cell_size),
                math.floor((row["lat"] + 90.0) / cell_size),
            )
            grouped.setdefault(key, []).append(row)
        cells = []
        for (cell_x, cell_y), members in grouped.items():
            members = sorted(members, key=lambda row: str(row["id"]))
            first = members[0]
            cells.append(
                HumanMapCell(
                    cell_x=cell_x,
                    cell_y=cell_y,
                    count=len(members),
                    missing=sum(
                        1 for m in members if m["status"] == "missing"
                    ),
                    reported_deceased=sum(
                        1
                        for m in members
                        if m["status"] == "reported_deceased"
                    ),
                    confirmed_alive=sum(
                        1
                        for m in members
                        if m["status"] == "confirmed_alive"
                    ),
                    confirmed_deceased=sum(
                        1
                        for m in members
                        if m["status"] == "confirmed_deceased"
                    ),
                    latitude=sum(m["lat"] for m in members)
                    / len(members),
                    longitude=sum(m["lon"] for m in members)
                    / len(members),
                    west=min(m["lon"] for m in members),
                    south=min(m["lat"] for m in members),
                    east=max(m["lon"] for m in members),
                    north=max(m["lat"] for m in members),
                    all_operational=all(
                        m["classification"] == "operational"
                        for m in members
                    ),
                    point_id=first["id"],
                    point_status=first["status"],
                    point_precision=first["precision"],
                    point_verification=first["verification"],
                    point_source_name=first["source_name"],
                    point_source_type=first["source_type"],
                    point_source_url=first["source_url"],
                    point_updated_at=first["updated_at"],
                )
            )
        cells.sort(key=lambda cell: (-cell.count, cell.cell_x, cell.cell_y))
        return cells, self.unmapped


async def get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get(path)


def feature_people_count(feature: dict) -> int:
    return feature["count"] if feature["kind"] == "cluster" else 1


def collect_all_pages(body: dict) -> list[dict]:
    return body["features"]


@pytest.mark.anyio
async def test_national_zoom_clusters_all_2000_under_500_features():
    app = create_app(repository=FakeHumanMapRepository(make_rows(2000)))

    response = await get(
        app, f"/internal/v1/people/map-overview?{NATIONAL}&limit=500"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["returnedFeatures"] == len(body["features"]) <= 500
    assert body["nextCursor"] is None
    assert body["totalMapped"] == 2000
    assert body["totalMatched"] == 2000
    assert body["unmappedCount"] == 0
    represented = sum(
        feature_people_count(feature) for feature in body["features"]
    )
    assert represented == body["totalMapped"]
    status_total = sum(
        sum(feature["statusCounts"].values())
        for feature in body["features"]
        if feature["kind"] == "cluster"
    ) + sum(1 for f in body["features"] if f["kind"] == "point")
    assert status_total == 2000
    for feature in body["features"]:
        if feature["kind"] == "cluster":
            assert feature["count"] >= 2
            assert (
                sum(feature["statusCounts"].values()) == feature["count"]
            )
    assert body["dataClassification"] == "demonstrative"
    assert "generatedAt" in body


@pytest.mark.anyio
async def test_zoom_increase_splits_clusters_without_losing_records():
    app = create_app(repository=FakeHumanMapRepository(make_rows(2000)))

    coarse = await get(
        app, f"/internal/v1/people/map-overview?{NATIONAL}&limit=500"
    )
    fine = await get(
        app,
        "/internal/v1/people/map-overview"
        "?west=-73.5&south=6.7&east=-72.9&north=7.3&zoom=12&limit=500",
    )

    assert coarse.status_code == 200 and fine.status_code == 200
    coarse_body = coarse.json()
    fine_body = fine.json()
    assert len(fine_body["features"]) > len(coarse_body["features"])
    assert (
        sum(feature_people_count(f) for f in coarse_body["features"])
        == coarse_body["totalMapped"]
        == 2000
    )
    assert (
        sum(feature_people_count(f) for f in fine_body["features"])
        == fine_body["totalMapped"]
        == 2000
    )


@pytest.mark.anyio
async def test_urban_zoom_returns_anonymous_points():
    app = create_app(repository=FakeHumanMapRepository(make_rows(2000)))

    response = await get(
        app,
        "/internal/v1/people/map-overview"
        "?west=-73.15&south=7.10&east=-73.09&north=7.16&zoom=19&limit=500",
    )

    assert response.status_code == 200
    body = response.json()
    points = [f for f in body["features"] if f["kind"] == "point"]
    assert points, "el zoom urbano debe producir puntos individuales"
    for point in points:
        assert set(point.keys()) == {
            "kind", "id", "status", "latitude", "longitude",
            "coordinatePrecision", "verificationStatus", "source",
            "updatedAt",
        }
        assert point["coordinatePrecision"] in {
            "approximate", "municipality"
        }
        forbidden = {
            "displayName", "name", "document", "documentNumber",
            "phone", "contact", "medicalInformation", "photo",
            "personId",
        }
        assert forbidden.isdisjoint(point.keys())


@pytest.mark.anyio
async def test_bbox_excludes_outside_points():
    app = create_app(repository=FakeHumanMapRepository(make_rows(200)))

    response = await get(
        app,
        "/internal/v1/people/map-overview"
        "?west=0.0&south=0.0&east=1.0&north=1.0&zoom=10",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["features"] == []
    assert body["totalMapped"] == 0
    assert body["returnedFeatures"] == 0


@pytest.mark.anyio
async def test_status_filter_only_counts_matching_people():
    rows = make_rows(2000)
    expected_missing = sum(
        1 for row in rows if row["status"] == "missing"
    )
    app = create_app(repository=FakeHumanMapRepository(rows))

    response = await get(
        app,
        f"/internal/v1/people/map-overview?{NATIONAL}"
        "&statuses=missing&limit=500",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["totalMapped"] == expected_missing
    for feature in body["features"]:
        if feature["kind"] == "cluster":
            counts = feature["statusCounts"]
            assert counts["missing"] == feature["count"]
            assert counts["reportedDeceased"] == 0
            assert counts["confirmedAlive"] == 0
            assert counts["confirmedDeceased"] == 0
        else:
            assert feature["status"] == "missing"


@pytest.mark.anyio
async def test_unmapped_people_are_reported_not_invented():
    app = create_app(
        repository=FakeHumanMapRepository(make_rows(100), unmapped=25)
    )

    response = await get(
        app, f"/internal/v1/people/map-overview?{NATIONAL}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["unmappedCount"] == 25
    assert body["totalMapped"] == 100
    assert body["totalMatched"] == 125
    assert (
        sum(feature_people_count(f) for f in body["features"]) == 100
    )


@pytest.mark.anyio
async def test_cursor_paginates_points_without_loss_or_duplication():
    app = create_app(repository=FakeHumanMapRepository(make_rows(2000)))
    base = (
        "/internal/v1/people/map-overview"
        "?west=-73.15&south=7.10&east=-73.09&north=7.16&zoom=19&limit=50"
    )

    first = await get(app, base)
    assert first.status_code == 200
    first_body = first.json()
    total_mapped = first_body["totalMapped"]
    seen_ids: list[str] = []
    represented = 0
    body = first_body
    pages = 0
    while True:
        pages += 1
        assert pages < 50, "paginación sin término"
        represented += sum(
            feature_people_count(f) for f in body["features"]
        )
        seen_ids.extend(str(f["id"]) for f in body["features"])
        assert body["totalMapped"] == total_mapped
        assert len(body["features"]) <= 50
        if body["nextCursor"] is None:
            break
        following = await get(
            app, f"{base}&cursor={body['nextCursor']}"
        )
        assert following.status_code == 200
        body = following.json()
    assert pages > 1, "el caso debe requerir más de una página"
    assert represented == total_mapped
    assert len(seen_ids) == len(set(seen_ids))


@pytest.mark.anyio
async def test_cluster_ids_are_stable_for_same_bbox_and_zoom():
    app = create_app(repository=FakeHumanMapRepository(make_rows(2000)))
    path = f"/internal/v1/people/map-overview?{NATIONAL}&limit=500"

    first = await get(app, path)
    second = await get(app, path)

    assert [f["id"] for f in first.json()["features"]] == [
        f["id"] for f in second.json()["features"]
    ]


@pytest.mark.anyio
async def test_exact_precision_is_degraded_in_depth_defense():
    row = make_rows(1)[0]
    row["precision"] = "exact"
    row["lat"] = 7.123456
    row["lon"] = -73.123456
    app = create_app(repository=FakeHumanMapRepository([row]))

    response = await get(
        app,
        "/internal/v1/people/map-overview"
        "?west=-74.0&south=6.0&east=-72.0&north=8.0&zoom=19",
    )

    assert response.status_code == 200
    point = response.json()["features"][0]
    assert point["kind"] == "point"
    assert point["coordinatePrecision"] == "approximate"
    assert point["latitude"] == 7.12
    assert point["longitude"] == -73.12


@pytest.mark.anyio
async def test_invalid_parameters_return_422():
    app = create_app(repository=FakeHumanMapRepository(make_rows(10)))
    base = "/internal/v1/people/map-overview"

    inverted_bbox = await get(
        app, f"{base}?west=-66.8&south=-4.3&east=-79.0&north=12.6&zoom=5"
    )
    flat_bbox = await get(
        app, f"{base}?west=-79.0&south=5.0&east=-66.8&north=5.0&zoom=5"
    )
    zoom_low = await get(
        app, f"{base}?west=-79.0&south=-4.3&east=-66.8&north=12.6&zoom=2"
    )
    zoom_high = await get(
        app,
        f"{base}?west=-79.0&south=-4.3&east=-66.8&north=12.6&zoom=20",
    )
    out_of_range = await get(
        app, f"{base}?west=-181.0&south=-4.3&east=-66.8&north=12.6&zoom=5"
    )
    bad_status = await get(
        app, f"{base}?{NATIONAL}&statuses=desconocido"
    )
    bad_limit = await get(app, f"{base}?{NATIONAL}&limit=501")
    bad_cursor = await get(app, f"{base}?{NATIONAL}&cursor=@@@@")
    negative_cursor = await get(
        app, f"{base}?{NATIONAL}&cursor=bzotMQ=="
    )
    missing_bbox = await get(app, f"{base}?zoom=5")

    for response in (
        inverted_bbox,
        flat_bbox,
        zoom_low,
        zoom_high,
        out_of_range,
        bad_status,
        bad_limit,
        bad_cursor,
        negative_cursor,
        missing_bbox,
    ):
        assert response.status_code == 422
