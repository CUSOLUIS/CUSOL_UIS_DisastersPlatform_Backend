"""CHG-126 — Métricas del sistema en el gateway.

El muestreador deriva CPU % y tasas de red de la diferencia entre
muestras, y el endpoint /api/v1/admin/system-metrics exige sesión y
rol super_admin como el resto de la consola.
"""

import httpx
import pytest

from app import system_metrics
from app.config import Settings
from app.main import create_app
from app.system_metrics import SystemMetricsSampler


DISASTER_URL = "http://disaster-service:8001"
IDENTITY_URL = "http://identity-service:8002"

ADMIN_ACCOUNT = {
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
    "displayName": "Admin CUSOL",
    "email": "admin@cusol.local",
    "assignedRole": "super_admin",
    "status": "active",
    "sessionExpiresAt": "2026-08-17T10:00:00Z",
}
USER_ACCOUNT = {
    **ADMIN_ACCOUNT,
    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
    "assignedRole": "user",
}


def write_fake_proc(
    root,
    *,
    busy: int,
    total: int,
    rx: int,
    tx: int,
) -> None:
    idle = total - busy
    (root / "stat").write_text(
        f"cpu {busy} 0 0 {idle} 0 0 0 0 0 0\n", encoding="ascii"
    )
    (root / "meminfo").write_text(
        "MemTotal:       1000000 kB\n"
        "MemFree:         200000 kB\n"
        "MemAvailable:    400000 kB\n"
        "SwapTotal:       100000 kB\n"
        "SwapFree:         80000 kB\n",
        encoding="ascii",
    )
    (root / "loadavg").write_text(
        "1.50 1.20 0.90 2/345 6789\n", encoding="ascii"
    )
    (root / "uptime").write_text("54321.10 98765.43\n", encoding="ascii")
    (root / "net").mkdir(exist_ok=True)
    (root / "net" / "dev").write_text(
        "Inter-|   Receive                                                |"
        "  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|"
        "bytes    packets errs drop fifo colls carrier compressed\n"
        f"    lo: 5000 10 0 0 0 0 0 0 5000 10 0 0 0 0 0 0\n"
        f"  eth0: {rx} 100 0 0 0 0 0 0 {tx} 90 0 0 0 0 0 0\n",
        encoding="ascii",
    )


def test_sampler_derives_rates_between_samples(tmp_path, monkeypatch):
    clock = iter([100.0, 110.0])
    monkeypatch.setattr(
        system_metrics.time, "monotonic", lambda: next(clock)
    )
    sampler = SystemMetricsSampler(proc_root=str(tmp_path), history=5)

    write_fake_proc(tmp_path, busy=500, total=1000, rx=1_000, tx=2_000)
    first = sampler.sample()
    # Primera muestra: CPU promedio desde el arranque, red sin tasa aún.
    assert first["cpu_percent"] == 50.0
    assert first["network_rx_bytes_per_second"] == 0.0
    assert first["memory_total_bytes"] == 1_000_000 * 1024
    assert first["memory_used_bytes"] == 600_000 * 1024
    assert first["memory_available_bytes"] == 400_000 * 1024
    assert first["swap_used_bytes"] == 20_000 * 1024
    assert first["load_1m"] == 1.5
    assert first["uptime_seconds"] == 54321.10
    assert first["disk_total_bytes"] > 0

    # 10 s después: 100 jiffies ocupados de 400 → 25 %; 50 KiB en 10 s.
    write_fake_proc(
        tmp_path, busy=600, total=1400, rx=513_000, tx=104_400
    )
    second = sampler.sample()
    assert second["cpu_percent"] == 25.0
    assert second["network_rx_bytes_per_second"] == 51_200.0
    assert second["network_tx_bytes_per_second"] == 10_240.0
    assert len(sampler.samples) == 2


def test_sampler_ring_keeps_only_recent_history(tmp_path, monkeypatch):
    monkeypatch.setattr(system_metrics.time, "monotonic", lambda: 1.0)
    sampler = SystemMetricsSampler(proc_root=str(tmp_path), history=3)
    write_fake_proc(tmp_path, busy=10, total=100, rx=0, tx=0)
    for _ in range(5):
        sampler.sample()
    assert len(sampler.samples) == 3


def gateway_settings() -> Settings:
    return Settings(
        disaster_service_url=DISASTER_URL,
        identity_service_url=IDENTITY_URL,
        upstream_timeout_seconds=1,
        # Intervalo largo: la tarea de fondo toma su muestra inicial y
        # no interfiere más durante la prueba.
        system_metrics_sample_seconds=60,
    )


def identity_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/internal/v1/auth/me":
        token = request.headers.get("x-session-token")
        if token == "token-admin":
            return httpx.Response(200, json=ADMIN_ACCOUNT)
        if token == "token-user":
            return httpx.Response(200, json=USER_ACCOUNT)
        return httpx.Response(
            401,
            json={
                "type": "session-required",
                "title": "Sesión requerida",
                "status": 401,
                "detail": "La sesión está ausente, vencida o revocada.",
            },
        )
    raise AssertionError(f"ruta identity inesperada: {request.url.path}")


def make_app():
    def disaster_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("las métricas no consultan al upstream")

    upstream = httpx.AsyncClient(
        transport=httpx.MockTransport(disaster_handler),
        base_url=DISASTER_URL,
    )
    identity = httpx.AsyncClient(
        transport=httpx.MockTransport(identity_handler),
        base_url=IDENTITY_URL,
    )
    return create_app(gateway_settings(), upstream, identity)


async def request_gateway(app, path, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get(path, **kwargs)


@pytest.mark.anyio
async def test_system_metrics_requires_session_and_role():
    app = make_app()

    anonymous = await request_gateway(app, "/api/v1/admin/system-metrics")
    as_user = await request_gateway(
        app,
        "/api/v1/admin/system-metrics",
        headers={"Cookie": "cusol_session=token-user"},
    )

    assert anonymous.status_code == 401
    assert as_user.status_code == 403


@pytest.mark.anyio
async def test_system_metrics_returns_snapshot_and_series():
    app = make_app()

    response = await request_gateway(
        app,
        "/api/v1/admin/system-metrics",
        headers={"Cookie": "cusol_session=token-admin"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["intervalSeconds"] == 60
    assert body["series"], "la serie nunca llega vacía"
    latest = body["latest"]
    for key in (
        "sampledAt",
        "cpuPercent",
        "load1m",
        "memoryTotalBytes",
        "memoryUsedBytes",
        "memoryAvailableBytes",
        "swapTotalBytes",
        "diskTotalBytes",
        "diskFreeBytes",
        "networkRxBytesPerSecond",
        "networkTxBytesPerSecond",
        "uptimeSeconds",
    ):
        assert key in latest
    assert latest["memoryTotalBytes"] > 0
    assert 0 <= latest["cpuPercent"] <= 100
