from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str
    database_pool_min_size: int = 1
    database_pool_max_size: int = 5
    verification_ttl_hours: int = 24
    session_ttl_hours: int = 24
    # CHG-043: la entrega SMTP vive en el mail-service.
    mail_service_url: str = "http://mail-service:8003"
    mail_timeout_seconds: float = 10.0
    # Parámetros Argon2id configurables (ADR-004).
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536
    argon2_parallelism: int = 2
    # CHG-036: misma clave que disaster-service para que el motivo de
    # auditoría sea legible por la consola.
    report_encryption_key: str = "dev-local-only-report-key"

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
            verification_ttl_hours=int(
                os.getenv("VERIFICATION_TTL_HOURS", "24")
            ),
            session_ttl_hours=int(os.getenv("SESSION_TTL_HOURS", "24")),
            mail_service_url=os.getenv(
                "MAIL_SERVICE_URL", "http://mail-service:8003"
            ).rstrip("/"),
            mail_timeout_seconds=float(
                os.getenv("MAIL_TIMEOUT_SECONDS", "10")
            ),
            argon2_time_cost=int(os.getenv("ARGON2_TIME_COST", "3")),
            argon2_memory_cost=int(
                os.getenv("ARGON2_MEMORY_COST", "65536")
            ),
            argon2_parallelism=int(os.getenv("ARGON2_PARALLELISM", "2")),
            report_encryption_key=os.getenv(
                "REPORT_ENCRYPTION_KEY", "dev-local-only-report-key"
            ),
        )
