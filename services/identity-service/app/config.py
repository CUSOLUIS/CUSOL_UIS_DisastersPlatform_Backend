from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str
    database_pool_min_size: int = 1
    database_pool_max_size: int = 5
    verification_ttl_hours: int = 24
    session_ttl_hours: int = 24
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    mail_from: str = "no-reply@cusol.local"
    public_base_url: str = "http://localhost:3100"
    # Parámetros Argon2id configurables (ADR-004).
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536
    argon2_parallelism: int = 2

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
            smtp_host=os.getenv("SMTP_HOST", "mailpit"),
            smtp_port=int(os.getenv("SMTP_PORT", "1025")),
            mail_from=os.getenv("MAIL_FROM", "no-reply@cusol.local"),
            public_base_url=os.getenv(
                "PUBLIC_BASE_URL", "http://localhost:3100"
            ).rstrip("/"),
            argon2_time_cost=int(os.getenv("ARGON2_TIME_COST", "3")),
            argon2_memory_cost=int(
                os.getenv("ARGON2_MEMORY_COST", "65536")
            ),
            argon2_parallelism=int(os.getenv("ARGON2_PARALLELISM", "2")),
        )
