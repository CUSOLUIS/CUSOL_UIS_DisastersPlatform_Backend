from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str
    database_pool_min_size: int
    database_pool_max_size: int
    upload_dir: str = "/data/uploads"
    report_encryption_key: str = "dev-local-only-report-key"
    max_photos: int = 5
    max_photo_bytes: int = 10 * 1024 * 1024
    max_total_photo_bytes: int = 50 * 1024 * 1024
    # CHG-036: vigencia del acceso temporal a evidencia (tope 300 s).
    evidence_grant_ttl_seconds: int = 300
    # CHG-044: clave EXCLUSIVA de ofertas comunitarias, montada como
    # secreto de Docker; aquí solo vive la ruta, jamás la clave.
    aid_offer_encryption_key_file: str = (
        "/run/secrets/cusol/aid_offer_key"
    )

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
        )
