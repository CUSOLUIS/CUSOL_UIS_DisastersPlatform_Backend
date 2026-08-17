"""CHG-126 — Métricas del sistema operativo para la consola admin.

Lecturas sin dependencias nuevas: /proc y shutil. Dentro de un
contenedor, CPU, memoria y carga son las del host (esos archivos de
/proc no están namespaced); el disco es el del sistema de archivos que
respalda al contenedor (el disco del host) y la red es la del
namespace del gateway, por donde pasa toda la API.

El muestreador guarda un anillo de muestras para que las gráficas de
la consola tengan historia desde la primera carga. Las tasas (CPU %,
bytes/s de red) se derivan de la diferencia con la muestra anterior;
la primera muestra usa los acumulados desde el arranque para la CPU y
0 para la red.
"""

from collections import deque
from datetime import UTC, datetime
from pathlib import Path
import shutil
import time


class SystemMetricsSampler:
    def __init__(
        self,
        proc_root: str = "/proc",
        disk_path: str = "/",
        history: int = 180,
    ) -> None:
        self._proc = Path(proc_root)
        self._disk_path = disk_path
        self.samples: deque[dict] = deque(maxlen=history)
        self._previous_cpu: tuple[int, int] | None = None
        self._previous_net: tuple[int, int, float] | None = None

    def _read(self, name: str) -> str:
        return (self._proc / name).read_text(encoding="ascii")

    def _cpu_counters(self) -> tuple[int, int]:
        """(ocupado, total) en jiffies acumulados desde el arranque."""
        fields = self._read("stat").splitlines()[0].split()[1:]
        values = [int(field) for field in fields]
        total = sum(values)
        # idle + iowait; el resto de columnas cuentan como ocupado.
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return total - idle, total

    def _memory(self) -> dict[str, int]:
        info: dict[str, int] = {}
        for line in self._read("meminfo").splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts:
                info[key] = int(parts[0]) * 1024
        return info

    def _network_totals(self) -> tuple[int, int]:
        rx = tx = 0
        for line in self._read("net/dev").splitlines()[2:]:
            name, _, counters = line.partition(":")
            if name.strip() == "lo":
                continue
            fields = counters.split()
            rx += int(fields[0])
            tx += int(fields[8])
        return rx, tx

    def sample(self) -> dict:
        now = time.monotonic()
        busy, total = self._cpu_counters()
        if self._previous_cpu is not None:
            previous_busy, previous_total = self._previous_cpu
            delta_total = total - previous_total
            delta_busy = busy - previous_busy
        else:
            # Primera muestra: promedio desde el arranque del host.
            delta_busy, delta_total = busy, total
        self._previous_cpu = (busy, total)
        cpu_percent = (
            100.0 * delta_busy / delta_total if delta_total > 0 else 0.0
        )

        rx, tx = self._network_totals()
        if self._previous_net is not None:
            previous_rx, previous_tx, previous_at = self._previous_net
            elapsed = now - previous_at
            rx_rate = (rx - previous_rx) / elapsed if elapsed > 0 else 0.0
            tx_rate = (tx - previous_tx) / elapsed if elapsed > 0 else 0.0
        else:
            rx_rate = tx_rate = 0.0
        self._previous_net = (rx, tx, now)

        memory = self._memory()
        memory_total = memory.get("MemTotal", 0)
        memory_available = memory.get("MemAvailable", 0)
        swap_total = memory.get("SwapTotal", 0)
        swap_free = memory.get("SwapFree", 0)

        load_1m, load_5m, load_15m = (
            float(value) for value in self._read("loadavg").split()[:3]
        )
        uptime_seconds = float(self._read("uptime").split()[0])
        disk = shutil.disk_usage(self._disk_path)

        sample = {
            "sampled_at": datetime.now(UTC),
            "cpu_percent": round(min(100.0, max(0.0, cpu_percent)), 2),
            "load_1m": load_1m,
            "load_5m": load_5m,
            "load_15m": load_15m,
            "memory_total_bytes": memory_total,
            "memory_used_bytes": max(0, memory_total - memory_available),
            "memory_available_bytes": memory_available,
            "swap_total_bytes": swap_total,
            "swap_used_bytes": max(0, swap_total - swap_free),
            "disk_total_bytes": disk.total,
            "disk_used_bytes": disk.used,
            "disk_free_bytes": disk.free,
            "network_rx_bytes_per_second": round(max(0.0, rx_rate), 2),
            "network_tx_bytes_per_second": round(max(0.0, tx_rate), 2),
            "uptime_seconds": uptime_seconds,
        }
        self.samples.append(sample)
        return sample
