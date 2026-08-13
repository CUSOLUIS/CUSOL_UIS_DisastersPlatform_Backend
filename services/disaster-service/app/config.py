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
        )
