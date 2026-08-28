"""Ingesta del catálogo del SGC y activación de alertas (CHG-208).

Aquí vive la orquestación con efectos: el adaptador HTTP del catálogo
(spec §5), el ciclo de polling con checkpoint e idempotencia (spec
§6-8) y la activación de alertas + notificaciones que comparten el
poller y el simulador del administrador.

El ciclo JAMÁS propaga una excepción: una caída del SGC no bloquea la
plataforma (spec §81); el fallo queda contado en el checkpoint y se
reintenta al siguiente intervalo. Tras una interrupción, la ventana de
consulta se amplía sola porque parte del último checkpoint (spec §82).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

from . import seismic
from .notifications import ReportNotifier

logger = logging.getLogger("seismic")

# Etiqueta del correo de cortesía (canal existente CHG-054).
NOTIFICATION_LABEL = "Alertas sísmicas y red de emergencia"

# Ventana por defecto al arrancar sin checkpoint: no se descarga el
# catálogo histórico (spec §7).
_BOOTSTRAP_WINDOW_HOURS = 6


class HttpSgcEventProvider:
    """Adaptador de la API del catalogador del SGC (CHG-222): la misma
    que consume el Visor Sismos oficial. `POST /api/events/search/?page=N`
    con cuerpo JSON devuelve los eventos del más reciente al más antiguo;
    se recorren páginas hasta pasar `since` (tope de seguridad) y se
    entrega al normalizador el diccionario ESP_* que ya entiende, más
    ESP_LUGAR con el lugar en palabras. Un evento «not existing» del SGC
    jamás entra."""

    _MAX_PAGES = 5

    def __init__(
        self,
        catalog_url: str,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._catalog_url = catalog_url.rstrip("/") + "/"
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    @staticmethod
    def _to_feature(event: dict[str, Any]) -> dict[str, Any] | None:
        utc_text = event.get("utc_time")
        if not isinstance(utc_text, str) or not utc_text.strip():
            return None
        try:
            origin = datetime.strptime(
                utc_text.strip(), "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=UTC)
        except ValueError:
            return None
        status = str(event.get("status") or "automatic")
        return {
            "ESP_ID_EVENTO_TXT": event.get("id"),
            # El catalogador no publica ids de solución: una revisión
            # manual tras la automática cambia el estado, y eso basta
            # para que cuente como REVISIÓN (spec §8), nunca como
            # terremoto nuevo.
            "ESP_ID_SOL_LOCALIZACION": status,
            "ESP_ID_SOL_MAGNITUD": status,
            "ESP_MAGNITUD": event.get("magnitude"),
            "ESP_FUENTE_MAGNITUD": event.get("mag_type") or event.get("agency"),
            "ESP_PROFUNDIDAD": event.get("depth"),
            "ESP_FUENTE_LOCALIZACION": event.get("agency"),
            "ESP_FECHA": int(origin.timestamp() * 1000),
            "ESP_LATITUD": event.get("latitude"),
            "ESP_LONGITUD": event.get("longitude"),
            "ESP_LUGAR": event.get("place"),
            "ESP_TIPO": event.get("event_type"),
            "MUN_CODIGO": None,
            "DEPT_CODIGO": None,
        }

    async def fetch_recent(
        self, since: datetime
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            for page in range(1, self._MAX_PAGES + 1):
                response = await client.post(
                    self._catalog_url,
                    params={"page": str(page)},
                    json={},
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
                if "detail" in payload and "results" not in payload:
                    raise RuntimeError(
                        f"SGC devolvió error: {payload['detail']!r}"
                    )
                block = payload.get("results") or {}
                events = (
                    block.get("results")
                    if isinstance(block, dict)
                    else block
                ) or []
                reached_since = False
                for event in events:
                    if event.get("event_type") == "not existing":
                        continue
                    feature = self._to_feature(event)
                    if feature is None:
                        continue
                    if feature["ESP_FECHA"] <= int(since.timestamp() * 1000):
                        reached_since = True
                        continue
                    collected.append(feature)
                if reached_since or not payload.get("next"):
                    break
        # El ciclo espera orden ascendente (el checkpoint avanza con el
        # más nuevo); la API los entrega descendentes.
        collected.sort(key=lambda feature: feature["ESP_FECHA"])
        return collected


async def _notify_alert_recipients(
    repository,
    notifier: ReportNotifier | None,
    *,
    alert_id: UUID,
    owner_account_id: UUID,
    kind: str,
    title: str,
    body: str,
    tracking_code: str,
) -> None:
    """Fila auditable siempre; correo de mejor esfuerzo (su fallo no
    revierte nada). El push real llega con las credenciales del VPS."""
    recipients = await repository.list_accepted_contact_recipients(
        owner_account_id
    )
    rows: list[dict] = []
    now = datetime.now(UTC)
    for recipient in recipients:
        recipient_id = recipient["contact_account_id"]
        rows.append(
            {
                "alert_id": alert_id,
                "recipient_account_id": recipient_id,
                "kind": kind,
                "channel": "RECORD",
                "title": title,
                "body": body,
            }
        )
        if notifier is not None:
            try:
                await notifier.notify_report_status(
                    recipient_id, NOTIFICATION_LABEL, tracking_code, body
                )
                rows[-1]["channel"] = "EMAIL"
                rows[-1]["sent_at"] = now
            except Exception:  # noqa: BLE001 — cortesía, jamás bloquea
                logger.warning(
                    "Correo de alerta sísmica no entregado",
                    exc_info=True,
                )
    if rows:
        await repository.record_seismic_notifications(rows)


async def activate_alerts_for_event(
    repository,
    notifier: ReportNotifier | None,
    event: dict,
) -> list[dict]:
    """Spec §40/§56: intersecta zonas activas con últimas ubicaciones
    conocidas, crea alertas con snapshot y notifica a los contactos
    aceptados. Idempotente: una cuenta ya alertada para el evento no se
    duplica ni se re-notifica."""
    affected = await repository.compute_affected_accounts(event["id"])
    if not affected:
        return []
    now = datetime.now(UTC)
    expires_at = seismic.alert_expiry(event["magnitude"], now)
    created = await repository.create_seismic_alerts(
        event["id"],
        [
            {
                "account_id": row["account_id"],
                "zone_id": row.get("zone_id"),
                "severity_level": row["severity_level"],
                "event_latitude": row["latitude"],
                "event_longitude": row["longitude"],
                "event_location_accuracy": row.get("accuracy_meters"),
                "event_location_timestamp": row.get("located_at"),
                # CHG-220: la altitud viaja congelada con la instantánea.
                "event_altitude": row.get("altitude_meters"),
                "event_altitude_accuracy": row.get(
                    "altitude_accuracy_meters"
                ),
                "expires_at": expires_at,
            }
            for row in affected
        ],
    )
    names = {
        row["account_id"]: row.get("display_name") or "Una persona"
        for row in affected
    }
    for alert in created:
        title, body = seismic.alert_notification_texts(
            names.get(alert["account_id"], "Una persona"),
            event["magnitude"],
        )
        if event.get("is_simulated"):
            body = f"{seismic.SIMULATED_BANNER}. {body}"
        await _notify_alert_recipients(
            repository,
            notifier,
            alert_id=alert["id"],
            owner_account_id=alert["account_id"],
            kind="ALERT_ACTIVATED",
            title=title,
            body=body,
            tracking_code=event["source_event_id"],
        )
    return created


async def ensure_provisional_zones(repository, event: dict) -> int:
    """Spec §22: sin producto instrumental, zonas PROVISIONAL_ESTIMATE
    a partir de magnitud/profundidad. Reemplaza las provisionales
    anteriores (una revisión mueve el epicentro) pero jamás toca zonas
    instrumentales."""
    if event["processing_status"] != "SEISMIC_DATA_PRELIMINARY":
        return 0
    zones = seismic.provisional_zones_geojson(
        magnitude=event["magnitude"],
        depth_km=event.get("depth_km"),
        latitude=event["latitude"],
        longitude=event["longitude"],
    )
    if not zones:
        return 0
    stored = await repository.replace_intensity_zones(
        event["id"],
        [
            {
                "source": "PROVISIONAL_ESTIMATE",
                "severity_level": severity,
                "geometry_geojson": json.dumps(geometry),
            }
            for severity, geometry in zones
        ],
        supersede=True,
    )
    return len(stored)


async def run_sgc_poll_cycle(
    repository,
    provider: seismic.SgcEventProvider,
    notifier: ReportNotifier | None,
    *,
    source: str = "SGC",
) -> dict:
    """Un ciclo completo (spec §7). Devuelve un resumen contable para
    pruebas y logs; captura todo fallo y lo cuenta en el checkpoint."""
    summary = {"created": 0, "revised": 0, "unchanged": 0, "failed": False}
    try:
        checkpoint = await repository.get_seismic_checkpoint(source)
        since = None
        if checkpoint is not None:
            since = checkpoint.get("last_event_time")
        if since is None:
            since = datetime.now(UTC) - timedelta(
                hours=_BOOTSTRAP_WINDOW_HOURS
            )
        raw_rows = await provider.fetch_recent(since)
        newest_time: datetime | None = None
        for attributes in raw_rows:
            normalized = seismic.normalize_sgc_feature(attributes)
            if normalized is None:
                continue
            if (
                newest_time is None
                or normalized.origin_time_utc > newest_time
            ):
                newest_time = normalized.origin_time_utc
            existing = await repository.get_seismic_event_by_source(
                source, normalized.source_event_id
            )
            payload_json = json.dumps(
                normalized.payload, default=str
            )
            if existing is None:
                event = await repository.insert_seismic_event(
                    source=source,
                    source_event_id=normalized.source_event_id,
                    source_location_solution_id=(
                        normalized.location_solution_id
                    ),
                    source_magnitude_solution_id=(
                        normalized.magnitude_solution_id
                    ),
                    origin_time_utc=normalized.origin_time_utc,
                    magnitude=normalized.magnitude,
                    depth_km=normalized.depth_km,
                    latitude=normalized.latitude,
                    longitude=normalized.longitude,
                    municipality_code=normalized.municipality_code,
                    department_code=normalized.department_code,
                    magnitude_source=normalized.magnitude_source,
                    location_source=normalized.location_source,
                    # CHG-222: el lugar en palabras del SGC.
                    description=normalized.description,
                    source_payload=payload_json,
                )
                summary["created"] += 1
                await ensure_provisional_zones(repository, event)
                await activate_alerts_for_event(
                    repository, notifier, event
                )
            else:
                stored = seismic.StoredEventSolution(
                    location_solution_id=existing.get(
                        "source_location_solution_id"
                    ),
                    magnitude_solution_id=existing.get(
                        "source_magnitude_solution_id"
                    ),
                    magnitude=existing["magnitude"],
                    depth_km=existing.get("depth_km"),
                    latitude=existing["latitude"],
                    longitude=existing["longitude"],
                )
                if seismic.is_revision(stored, normalized):
                    event = await repository.apply_seismic_revision(
                        existing["id"],
                        existing,
                        source_location_solution_id=(
                            normalized.location_solution_id
                        ),
                        source_magnitude_solution_id=(
                            normalized.magnitude_solution_id
                        ),
                        origin_time_utc=normalized.origin_time_utc,
                        magnitude=normalized.magnitude,
                        depth_km=normalized.depth_km,
                        latitude=normalized.latitude,
                        longitude=normalized.longitude,
                        source_payload=payload_json,
                    )
                    summary["revised"] += 1
                    # Spec §23: reprocesar zonas y usuarios afectados.
                    await ensure_provisional_zones(repository, event)
                    await activate_alerts_for_event(
                        repository, notifier, event
                    )
                else:
                    summary["unchanged"] += 1
        await repository.update_seismic_checkpoint(
            source, last_event_time=newest_time, success=True
        )
    except Exception:  # noqa: BLE001 — spec §81: nunca bloquear
        summary["failed"] = True
        logger.warning("Ciclo de polling SGC fallido", exc_info=True)
        try:
            await repository.update_seismic_checkpoint(
                source, last_event_time=None, success=False
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Checkpoint SGC no actualizable", exc_info=True
            )
    return summary


async def supervised_poll_loop(
    repository,
    provider: seismic.SgcEventProvider,
    notifier: ReportNotifier | None,
    interval_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    """Bucle de fondo supervisado: corre hasta que el servicio pare.
    Cada ciclo está aislado; el intervalo es configurable (spec §6)."""
    while not stop_event.is_set():
        await run_sgc_poll_cycle(repository, provider, notifier)
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=interval_seconds
            )
        except TimeoutError:
            continue
