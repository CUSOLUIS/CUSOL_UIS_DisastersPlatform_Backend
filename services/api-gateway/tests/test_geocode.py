"""CHG-147: proxy de geocodificación del gateway.

El navegador no llama más a nominatim.org; estos tests cubren el
moldeado de las respuestas, la caché corta, la degradación limpia y el
límite por origen, todo con transportes falsos (sin red).
"""

import httpx
import pytest

from app.config import Settings
from app.geocoding import TtlCache
from app.main import create_app

from test_main import custom_settings, get


SETTINGS = Settings(
    disaster_service_url="http://disaster-service:8001",
    upstream_timeout_seconds=1,
)

SEARCH_ROW = {
    "display_name": "Carrera 27, Bucaramanga, Santander, Colombia",
    "lat": "7.1008101",
    "lon": "-73.1130535",
}

REVERSE_PAYLOAD = {
    "display_name": "Carrera 27, La Salle, Bucaramanga, Colombia",
    "address": {
        "city": "Bucaramanga",
        "state": "Santander",
    },
}


def geocode_mock(handler, calls=None):
    def counting_handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return handler(request)

    return httpx.AsyncClient(
        transport=httpx.MockTransport(counting_handler),
        base_url="https://nominatim.example",
    )


def app_with_geocode(handler, settings=SETTINGS, calls=None):
    return create_app(
        settings, geocode_client=geocode_mock(handler, calls)
    )


@pytest.mark.anyio
async def test_search_shapes_candidates_and_queries_colombia():
    calls: list[httpx.Request] = []
    app = app_with_geocode(
        lambda _: httpx.Response(200, json=[SEARCH_ROW] * 7), calls=calls
    )

    response = await get(app, "/api/v1/geocode/search?q=Carrera 27")

    assert response.status_code == 200
    payload = response.json()
    # Nominatim puede devolver más filas; el contrato corta en 5.
    assert len(payload["candidates"]) == 5
    assert payload["candidates"][0] == {
        "label": SEARCH_ROW["display_name"],
        "latitude": 7.1008101,
        "longitude": -73.1130535,
    }
    params = dict(httpx.URL(str(calls[0].url)).params)
    assert params["countrycodes"] == "co"
    assert params["q"] == "Carrera 27"


@pytest.mark.anyio
async def test_search_skips_malformed_rows():
    rows = [
        {"display_name": "", "lat": "1", "lon": "1"},
        {"display_name": "Sin coordenadas"},
        {"display_name": "Valida", "lat": "4.6", "lon": "-74.0"},
        "no-es-objeto",
    ]
    app = app_with_geocode(lambda _: httpx.Response(200, json=rows))

    response = await get(app, "/api/v1/geocode/search?q=Bogota centro")

    assert response.status_code == 200
    assert response.json()["candidates"] == [
        {"label": "Valida", "latitude": 4.6, "longitude": -74.0}
    ]


@pytest.mark.anyio
async def test_search_uses_cache_ignoring_case_and_spaces():
    calls: list[httpx.Request] = []
    app = app_with_geocode(
        lambda _: httpx.Response(200, json=[SEARCH_ROW]), calls=calls
    )

    first = await get(app, "/api/v1/geocode/search?q=Carrera 27")
    second = await get(app, "/api/v1/geocode/search?q=  carrera   27 ")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(calls) == 1


@pytest.mark.anyio
async def test_search_degrades_to_503_when_geocoder_fails():
    def handler(_request):
        raise httpx.ConnectError("sin salida a internet")

    app = app_with_geocode(handler)

    response = await get(app, "/api/v1/geocode/search?q=Carrera 27")

    assert response.status_code == 503
    body = response.json()
    assert body["type"] == "geocoder-unavailable"
    assert body["status"] == 503


@pytest.mark.anyio
async def test_search_rejects_short_query():
    app = app_with_geocode(lambda _: httpx.Response(200, json=[]))

    response = await get(app, "/api/v1/geocode/search?q=ab")

    assert response.status_code == 422


@pytest.mark.anyio
async def test_search_rate_limited_by_origin():
    settings = custom_settings(geocode_rate_limit_per_minute=1)
    app = app_with_geocode(
        lambda _: httpx.Response(200, json=[]), settings=settings
    )

    first = await get(app, "/api/v1/geocode/search?q=Carrera 27")
    # Consulta distinta para no caer en la caché: debe topar el límite.
    second = await get(app, "/api/v1/geocode/search?q=Calle 45")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["type"] == "rate-limit-exceeded"


@pytest.mark.anyio
async def test_reverse_resolves_label_municipality_department():
    calls: list[httpx.Request] = []
    app = app_with_geocode(
        lambda _: httpx.Response(200, json=REVERSE_PAYLOAD), calls=calls
    )

    response = await get(
        app, "/api/v1/geocode/reverse?lat=7.10081&lon=-73.11305"
    )

    assert response.status_code == 200
    assert response.json() == {
        "label": REVERSE_PAYLOAD["display_name"],
        # CHG-156: la dirección corta termina donde empieza el municipio.
        "addressLine": "Carrera 27, La Salle",
        "municipality": "Bucaramanga",
        "department": "Santander",
    }
    params = dict(httpx.URL(str(calls[0].url)).params)
    assert params["zoom"] == "17"


@pytest.mark.anyio
async def test_reverse_falls_back_to_town_and_region():
    payload = {
        "display_name": "Vereda El Roble, Colombia",
        "address": {
            "town": "El Playón",
            "region": "Santander",
            "country": "Colombia",
        },
    }
    app = app_with_geocode(lambda _: httpx.Response(200, json=payload))

    response = await get(app, "/api/v1/geocode/reverse?lat=7.2&lon=-73.2")

    assert response.status_code == 200
    assert response.json()["municipality"] == "El Playón"
    assert response.json()["department"] == "Santander"
    assert response.json()["addressLine"] == "Vereda El Roble"


@pytest.mark.anyio
async def test_reverse_prefers_county_and_cuts_urban_perimeter():
    # CHG-156: caso Bucaramanga real — `city` es el polígono "Perímetro
    # Urbano" y el municipio verdadero viene en `county`; la dirección
    # corta conserva vía, barrio y comuna.
    payload = {
        "display_name": (
            "Avenida Calle 36, Centro, Comuna 15 - Centro, "
            "Perímetro Urbano Bucaramanga, Bucaramanga, Metropolitana, "
            "Santander, RAP Gran Santander, 680006, Colombia"
        ),
        "address": {
            "road": "Avenida Calle 36",
            "neighbourhood": "Centro",
            "suburb": "Comuna 15 - Centro",
            "city": "Perímetro Urbano Bucaramanga",
            "county": "Bucaramanga",
            "state_district": "Metropolitana",
            "state": "Santander",
            "region": "RAP Gran Santander",
            "postcode": "680006",
            "country": "Colombia",
        },
    }
    app = app_with_geocode(lambda _: httpx.Response(200, json=payload))

    response = await get(
        app, "/api/v1/geocode/reverse?lat=7.11935&lon=-73.12274"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["addressLine"] == "Avenida Calle 36, Centro, Comuna 15 - Centro"
    assert body["municipality"] == "Bucaramanga"
    assert body["department"] == "Santander"


@pytest.mark.anyio
async def test_reverse_unknown_point_is_404_and_cached():
    calls: list[httpx.Request] = []
    app = app_with_geocode(
        lambda _: httpx.Response(
            200, json={"error": "Unable to geocode"}
        ),
        calls=calls,
    )

    first = await get(app, "/api/v1/geocode/reverse?lat=0&lon=-95")
    second = await get(app, "/api/v1/geocode/reverse?lat=0&lon=-95")

    assert first.status_code == 404
    assert first.json()["type"] == "geocode-not-found"
    assert second.status_code == 404
    # Repetir el punto en medio del mar no reconsulta a Nominatim.
    assert len(calls) == 1


@pytest.mark.anyio
async def test_reverse_cache_merges_micro_movements():
    calls: list[httpx.Request] = []
    app = app_with_geocode(
        lambda _: httpx.Response(200, json=REVERSE_PAYLOAD), calls=calls
    )

    await get(app, "/api/v1/geocode/reverse?lat=7.10081&lon=-73.11311")
    # A ~11 m (4 decimales) el arrastre fino comparte entrada de caché.
    await get(app, "/api/v1/geocode/reverse?lat=7.10083&lon=-73.11313")

    assert len(calls) == 1


@pytest.mark.anyio
async def test_reverse_rejects_out_of_range_coordinates():
    app = app_with_geocode(
        lambda _: httpx.Response(200, json=REVERSE_PAYLOAD)
    )

    response = await get(app, "/api/v1/geocode/reverse?lat=95&lon=-73.1")

    assert response.status_code == 422


@pytest.mark.anyio
async def test_reverse_degrades_to_503_when_geocoder_fails():
    def handler(_request):
        raise httpx.ReadTimeout("nominatim lento")

    app = app_with_geocode(handler)

    response = await get(
        app, "/api/v1/geocode/reverse?lat=7.1&lon=-73.1"
    )

    assert response.status_code == 503
    assert response.json()["type"] == "geocoder-unavailable"


def test_ttl_cache_evicts_oldest_entry_beyond_capacity():
    cache = TtlCache(ttl_seconds=60, max_entries=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)

    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3
