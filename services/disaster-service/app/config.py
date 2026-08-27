from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str
    database_pool_min_size: int
    database_pool_max_size: int
    upload_dir: str = "/data/uploads"
    report_encryption_key: str = "dev-local-only-report-key"
    # CHG-071: máximo 3 fotografías por reporte (el frontend comprime
    # antes de enviar si la suma supera el presupuesto total).
    max_photos: int = 3
    max_photo_bytes: int = 10 * 1024 * 1024
    max_total_photo_bytes: int = 50 * 1024 * 1024
    # CHG-036: vigencia del acceso temporal a evidencia (tope 300 s).
    evidence_grant_ttl_seconds: int = 300
    # CHG-044: clave EXCLUSIVA de ofertas comunitarias, montada como
    # secreto de Docker; aquí solo vive la ruta, jamás la clave.
    aid_offer_encryption_key_file: str = (
        "/run/secrets/cusol/aid_offer_key"
    )
    # CHG-054: identity-service entrega las novedades de reportes con
    # cuenta (dueño de los correos de los titulares).
    identity_service_url: str = "http://identity-service:8002"
    notification_timeout_seconds: float = 5.0
    # CHG-111: commit del que salió la imagen, inyectado por el build.
    git_revision: str = "unknown"
    # CHG-208: polling del catálogo de sismos del SGC. Apagado por
    # defecto (la Pi es testing); el intervalo es parametrizable y
    # nunca vive cableado en el código (spec §6).
    sgc_poll_enabled: bool = False
    sgc_poll_interval_seconds: int = 15
    sgc_catalog_url: str = (
        "https://services1.arcgis.com/121aH0BAWntBBTHM/arcgis/rest/"
        "services/catalogo_de_sismos_2/FeatureServer/0"
    )
    # Ventana de visibilidad de un evento en el mapa público.
    seismic_visibility_hours: int = 24

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql://disasters:disasters@localhost:5432/disasters",
            ),
            database_pool_min_size=int(
                os.getenv("DATABASE_POOL_MIN_SIZE", "1")
            ),
            database_pool_max_size=int(
                os.getenv("DATABASE_POOL_MAX_SIZE", "5")
            ),
            upload_dir=os.getenv("UPLOAD_DIR", "/data/uploads"),
            report_encryption_key=os.getenv(
                "REPORT_ENCRYPTION_KEY", "dev-local-only-report-key"
            ),
            evidence_grant_ttl_seconds=min(
                300,
                int(os.getenv("EVIDENCE_GRANT_TTL_SECONDS", "300")),
            ),
            aid_offer_encryption_key_file=os.getenv(
                "AID_OFFER_ENCRYPTION_KEY_FILE",
                "/run/secrets/cusol/aid_offer_key",
            ),
            identity_service_url=os.getenv(
                "IDENTITY_SERVICE_URL", "http://identity-service:8002"
            ).rstrip("/"),
            notification_timeout_seconds=float(
                os.getenv("NOTIFICATION_TIMEOUT_SECONDS", "5")
            ),
            git_revision=os.getenv("GIT_REVISION", "unknown").strip()
            or "unknown",
            sgc_poll_enabled=(
                os.getenv("SGC_POLL_ENABLED", "false").strip().lower()
                == "true"
            ),
            sgc_poll_interval_seconds=max(
                5, int(os.getenv("SGC_POLL_INTERVAL_SECONDS", "15"))
            ),
            sgc_catalog_url=os.getenv(
                "SGC_CATALOG_URL",
                "https://services1.arcgis.com/121aH0BAWntBBTHM/arcgis/"
                "rest/services/catalogo_de_sismos_2/FeatureServer/0",
            ).rstrip("/"),
            seismic_visibility_hours=int(
                os.getenv("SEISMIC_EVENT_VISIBILITY_HOURS", "24")
            ),
        )
