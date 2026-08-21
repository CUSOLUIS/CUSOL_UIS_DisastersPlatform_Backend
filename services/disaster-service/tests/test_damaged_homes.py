"""CHG-182 — «Mi casita destruida»: feed público, comunidad y avisos.

La publicación exige cuenta (eso se prueba en
`test_transports_and_homes.py`, junto al alta). Aquí se cubre lo que
vive después: el feed que alimenta el mapa, las fotos públicas, los
comentarios con estrellas —que avisan a la dueña—, la denuncia con sus
umbrales, la bandeja de «Mi espacio» y el borrado administrativo.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.main import create_app
from app.models import normalized_tiktok_url

from test_admin_console import RecordingNotifier
from test_missing_persons import FakeStorage, request_app

HOME_ID = UUID("cccccccc-cccc-4ccc-8ccc-ccccccccc182")
COMMENT_ID = UUID("cccccccc-cccc-4ccc-8ccc-ccccccccc183")
OWNER_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa8")
VISITOR_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa9")
PHOTO_ID = UUID("dddddddd-dddd-4ddd-8ddd-ddddddddd182")
CREATED_AT = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)

ADMIN_HEADERS = {
    "X-Actor-Role": "super_admin",
    "X-Actor-Account-Id": str(OWNER_ID),
    "X-Actor-Display": "YWRtaW4=",
}
ANON_COMMENT_HEADERS = {
    "Idempotency-Key": "clave-comentario-casita-0182",
    "X-Actor-Kind": "anonymous",
}
OWNER_HEADERS = {
    "X-Actor-Kind": "authenticated",
    "X-Account-Id": str(OWNER_ID),
}
REPORT_HEADERS = {
    "Idempotency-Key": "clave-denuncia-casita-0182",
    "X-Actor-Kind": "anonymous",
    "X-Denouncer-Key": "fp:abcdef0123456789",
}


def home_row(**overrides) -> dict:
    row = {
        "id": HOME_ID,
        "public_code": "CASA-2026-ABCD1234",
        "description": "El río se llevó la mitad de la casa.",
        "department": "Chocó",
        "municipality": "Quibdó",
        "address": "Barrio Niño Jesús, calle 3",
        "latitude": 5.69,
        "longitude": -76.66,
        "household_size": 5,
        "donation_channel": "Nequi",
        "donation_reference": "3001234567",
        # CHG-201: vídeo de TikTok, opcional.
        "video_url": "https://www.tiktok.com/@familia/video/7412345678901234567",
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
        "comment_rating_average": 4.5,
        "comment_rating_count": 2,
        "photo_ids": [PHOTO_ID],
    }
    row.update(overrides)
    return row


class FakeDamagedHomesRepository:
    def __init__(self, *, missing=False, reporters=1, disabled=False):
        self.missing = missing
        self.reporters = reporters
        self.disabled = disabled
        self.calls: list[tuple[str, dict]] = []
        self.seen: list[dict] = []

    async def ping(self):
        return True

    # --- feed y fotos ---

    async def list_active_damaged_homes(self, limit, offset):
        self.calls.append(("feed", {"limit": limit, "offset": offset}))
        return ([] if self.missing else [home_row()]), 0 if self.missing else 1

    async def get_damaged_home_photo(self, *, damaged_home_id, photo_id):
        self.calls.append(
            ("photo", {"home": damaged_home_id, "photo": photo_id})
        )
        if self.missing:
            return None
        return {"object_key": "casitas/derivada.jpg",
                "content_type": "image/jpeg"}

    # --- comunidad ---

    async def list_damaged_home_comments(self, **kwargs):
        self.calls.append(("list", kwargs))
        if self.missing:
            return None
        return {
            "items": [
                {
                    "id": COMMENT_ID,
                    "account_id": None,
                    "author_display_name": None,
                    "actor_kind": "anonymous",
                    "content": "Fuimos y la familia sigue durmiendo afuera.",
                    "rating": 5,
                    "created_at": CREATED_AT,
                }
            ],
            "total": 1,
            "rating_average": 4.5,
            "rating_count": 2,
        }

    async def create_damaged_home_comment(self, **kwargs):
        self.calls.append(("comment", kwargs))
        if self.missing:
            return None
        return {
            "id": COMMENT_ID,
            "account_id": kwargs["account_id"],
            "author_display_name": kwargs["author_display_name"],
            "actor_kind": kwargs["actor_kind"],
            "content": kwargs["content"],
            "rating": kwargs["rating"],
            "created_at": CREATED_AT,
            "created": True,
            "owner_account_id": OWNER_ID,
            "public_code": "CASA-2026-ABCD1234",
        }

    async def create_damaged_home_complaint(self, **kwargs):
        self.calls.append(("complaint", kwargs))
        if self.missing:
            return None
        return {
            "reports_count": self.reporters,
            "under_observation": self.reporters >= 10 and not self.disabled,
            "disabled": self.disabled,
        }

    async def admin_delete_damaged_home_comment(self, **kwargs):
        self.calls.append(("delete_comment", kwargs))
        return 0 if self.missing else 1

    async def admin_delete_damaged_home(self, **kwargs):
        self.calls.append(("delete_home", kwargs))
        if self.missing:
            return 0, []
        return 1, ["casitas/original.bin", "casitas/derivada.jpg"]

    # --- mi espacio ---

    async def list_my_damaged_homes(self, account_id):
        self.calls.append(("mine", {"account_id": account_id}))
        if self.missing:
            return []
        return [
            home_row(
                visible=True,
                disabled_at=None,
                unread_comments=3,
                comments_count=7,
            )
        ]

    # CHG-202 — borrado por la dueña, con puerta de propiedad.
    async def delete_own_damaged_home(self, damaged_home_id, account_id):
        self.calls.append(
            ("delete_own", {"home": damaged_home_id, "account": account_id})
        )
        if self.missing or damaged_home_id != HOME_ID or account_id != OWNER_ID:
            return None
        self.missing = True
        return ["casitas/original.jpg", "casitas/derivada.jpg"]

    async def mark_damaged_home_comments_seen(self, **kwargs):
        self.seen.append(kwargs)
        return not self.missing


def homes_app(repository=None, notifier=None, storage=None):
    return create_app(
        repository=repository or FakeDamagedHomesRepository(),
        storage=storage if storage is not None else FakeStorage(),
        notifier=notifier,
    )


@pytest.mark.anyio
async def test_feed_publishes_photos_household_and_rating():
    response = await request_app(
        homes_app(), "GET", "/internal/v1/damaged-homes?limit=25&offset=0"
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["householdSize"] == 5
    assert item["donationChannel"] == "Nequi"
    assert item["donationReference"] == "3001234567"
    assert item["commentRatingAverage"] == 4.5
    # CHG-201: el vídeo viaja al feed, así que la ficha puede ofrecerlo
    # a cualquiera que la abra, con cuenta o sin ella.
    assert item["videoUrl"] == (
        "https://www.tiktok.com/@familia/video/7412345678901234567"
    )
    # Las fotos viajan como rutas públicas, nunca como claves de
    # almacenamiento.
    assert item["photoUrls"] == [
        f"/api/v1/public/damaged-homes/{HOME_ID}/photos/{PHOTO_ID}"
    ]


@pytest.mark.anyio
async def test_feed_rejects_an_unknown_page_size():
    response = await request_app(
        homes_app(), "GET", "/internal/v1/damaged-homes?limit=7&offset=0"
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_public_photo_is_served_from_the_derived_copy():
    storage = FakeStorage()
    storage.objects["casitas/derivada.jpg"] = b"imagen"
    response = await request_app(
        homes_app(storage=storage),
        "GET",
        f"/internal/v1/public/damaged-homes/{HOME_ID}/photos/{PHOTO_ID}",
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content == b"imagen"


@pytest.mark.anyio
async def test_comment_requires_a_star_rating():
    response = await request_app(
        homes_app(),
        "POST",
        f"/internal/v1/damaged-homes/{HOME_ID}/comments",
        headers=ANON_COMMENT_HEADERS,
        json={"content": "Ánimo, ya vamos para allá."},
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_comment_notifies_the_owner_by_mail():
    repository = FakeDamagedHomesRepository()
    notifier = RecordingNotifier()
    response = await request_app(
        homes_app(repository, notifier),
        "POST",
        f"/internal/v1/damaged-homes/{HOME_ID}/comments",
        headers=ANON_COMMENT_HEADERS,
        json={"content": "Fuimos y la familia necesita colchones.", "rating": 5},
    )

    assert response.status_code == 201
    assert len(notifier.notified) == 1
    aviso = notifier.notified[0]
    assert aviso["account_id"] == OWNER_ID
    assert aviso["tracking_code"] == "CASA-2026-ABCD1234"
    # El correo no repite el comentario: invita a entrar a Mi espacio.
    assert "Mi espacio" in aviso["status_label"]
    assert "colchones" not in aviso["status_label"]


@pytest.mark.anyio
async def test_owner_comment_does_not_notify_herself():
    repository = FakeDamagedHomesRepository()
    notifier = RecordingNotifier()
    response = await request_app(
        homes_app(repository, notifier),
        "POST",
        f"/internal/v1/damaged-homes/{HOME_ID}/comments",
        headers={**ANON_COMMENT_HEADERS, **OWNER_HEADERS},
        json={"content": "Gracias a todos por la ayuda recibida.", "rating": 5},
    )

    assert response.status_code == 201
    assert notifier.notified == []


@pytest.mark.anyio
async def test_a_failing_mail_never_breaks_the_comment():
    notifier = RecordingNotifier(error=RuntimeError("mail caído"))
    response = await request_app(
        homes_app(notifier=notifier),
        "POST",
        f"/internal/v1/damaged-homes/{HOME_ID}/comments",
        headers=ANON_COMMENT_HEADERS,
        json={"content": "Vamos mañana con herramienta.", "rating": 4},
    )
    # El aviso es cortesía: su fallo no revierte el comentario.
    assert response.status_code == 201


@pytest.mark.anyio
async def test_complaint_reports_the_threshold_state():
    repository = FakeDamagedHomesRepository(reporters=20, disabled=True)
    response = await request_app(
        homes_app(repository),
        "POST",
        f"/internal/v1/damaged-homes/{HOME_ID}/reports",
        headers=REPORT_HEADERS,
        json={
            "category": "informacion_falsa",
            "reason": "La casa de la foto no está en esa dirección.",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["disabled"] is True
    assert body["damagedHomeId"] == str(HOME_ID)


@pytest.mark.anyio
async def test_my_space_lists_homes_with_unread_comments():
    response = await request_app(
        homes_app(),
        "GET",
        "/internal/v1/me/damaged-homes",
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["unreadTotal"] == 3
    assert body["items"][0]["unreadComments"] == 3
    assert body["items"][0]["published"] is True


@pytest.mark.anyio
async def test_my_space_refuses_an_anonymous_visitor():
    response = await request_app(
        homes_app(),
        "GET",
        "/internal/v1/me/damaged-homes",
        headers={"X-Actor-Kind": "anonymous"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_marking_comments_as_seen_belongs_to_the_owner():
    repository = FakeDamagedHomesRepository()
    ok = await request_app(
        homes_app(repository),
        "POST",
        f"/internal/v1/me/damaged-homes/{HOME_ID}/comments-seen",
        headers=OWNER_HEADERS,
    )
    ajena = await request_app(
        homes_app(FakeDamagedHomesRepository(missing=True)),
        "POST",
        f"/internal/v1/me/damaged-homes/{HOME_ID}/comments-seen",
        headers=OWNER_HEADERS,
    )

    assert ok.status_code == 204
    assert repository.seen[0]["account_id"] == OWNER_ID
    assert ajena.status_code == 404


@pytest.mark.anyio
async def test_admin_deletes_comment_and_home_with_its_binaries():
    repository = FakeDamagedHomesRepository()
    storage = FakeStorage()
    app = homes_app(repository, storage=storage)

    comentario = await request_app(
        app,
        "DELETE",
        f"/internal/v1/admin/damaged-homes/{HOME_ID}/comments/{COMMENT_ID}",
        headers=ADMIN_HEADERS,
    )
    casita = await request_app(
        app,
        "DELETE",
        f"/internal/v1/admin/damaged-homes/{HOME_ID}",
        headers=ADMIN_HEADERS,
    )

    assert comentario.status_code == 200
    assert casita.status_code == 200
    # Las fotos no quedan huérfanas en el almacenamiento.
    assert storage.deleted == [
        "casitas/original.bin",
        "casitas/derivada.jpg",
    ]


@pytest.mark.anyio
async def test_admin_endpoints_refuse_a_plain_account():
    response = await request_app(
        homes_app(),
        "DELETE",
        f"/internal/v1/admin/damaged-homes/{HOME_ID}",
        headers={
            "X-Actor-Role": "user",
            "X-Actor-Account-Id": str(VISITOR_ID),
        },
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_community_endpoints_404_on_a_missing_home():
    app = homes_app(FakeDamagedHomesRepository(missing=True))

    listing = await request_app(
        app, "GET", f"/internal/v1/damaged-homes/{HOME_ID}/comments"
    )
    complaint = await request_app(
        app,
        "POST",
        f"/internal/v1/damaged-homes/{HOME_ID}/reports",
        headers=REPORT_HEADERS,
        json={"category": "otro", "reason": "No corresponde con nada."},
    )

    assert listing.status_code == 404
    assert complaint.status_code == 404


# CHG-201 — DEC-201-01: el enlace del vídeo solo puede ser de TikTok.
# Sin esta puerta, el campo sería un canal para publicar cualquier
# enlace junto a un medio para recibir dinero (CHG-182).
@pytest.mark.parametrize(
    "enlace",
    [
        "https://www.tiktok.com/@familia/video/7412345678901234567",
        "https://vm.tiktok.com/ZM6abcdef/",
        "https://vt.tiktok.com/ZS8abcdef/",
        "https://m.tiktok.com/v/7412345678901234567.html",
    ],
)
def test_tiktok_links_are_accepted(enlace):
    assert normalized_tiktok_url(enlace) == enlace


@pytest.mark.parametrize(
    "enlace",
    [
        "http://www.tiktok.com/@familia/video/74123",  # sin https
        "https://tiktok.com.malicioso.example/video/1",  # anfitrión ajeno
        "https://www.youtube.com/watch?v=abc",
        "https://malicioso.example/phishing",
        "https://www.tiktok.com@malicioso.example/video/1",  # credenciales
        "javascript:alert(1)",
    ],
)
def test_everything_that_is_not_tiktok_is_rejected(enlace):
    with pytest.raises(ValueError):
        normalized_tiktok_url(enlace)


def test_an_empty_video_link_means_no_video():
    assert normalized_tiktok_url(None) is None
    assert normalized_tiktok_url("") is None
    assert normalized_tiktok_url("   ") is None


# CHG-202 — La dueña elimina su casita; nadie más puede.
@pytest.mark.anyio
async def test_owner_deletes_her_damaged_home():
    repository = FakeDamagedHomesRepository()
    storage = FakeStorage()
    app = homes_app(repository=repository, storage=storage)

    response = await request_app(
        app,
        "DELETE",
        f"/internal/v1/me/damaged-homes/{HOME_ID}",
        headers={
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": str(OWNER_ID),
        },
    )

    assert response.status_code == 204
    # Los binarios de sus fotos salen del almacén con ella.
    assert storage.deleted == [
        "casitas/original.jpg",
        "casitas/derivada.jpg",
    ]


@pytest.mark.anyio
async def test_deleting_a_damaged_home_is_only_for_its_owner():
    repository = FakeDamagedHomesRepository()
    app = homes_app(repository=repository)

    ajena = await request_app(
        app,
        "DELETE",
        f"/internal/v1/me/damaged-homes/{HOME_ID}",
        headers={
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": str(VISITOR_ID),
        },
    )
    inexistente = await request_app(
        app,
        "DELETE",
        f"/internal/v1/me/damaged-homes/{uuid4()}",
        headers={
            "X-Actor-Kind": "authenticated",
            "X-Account-Id": str(OWNER_ID),
        },
    )
    sin_sesion = await request_app(
        app, "DELETE", f"/internal/v1/me/damaged-homes/{HOME_ID}"
    )

    # Ajena e inexistente responden IGUAL: no se delata cuál existe.
    assert ajena.status_code == 404
    assert inexistente.status_code == 404
    assert ajena.json()["detail"] == inexistente.json()["detail"]
    assert sin_sesion.status_code == 401
