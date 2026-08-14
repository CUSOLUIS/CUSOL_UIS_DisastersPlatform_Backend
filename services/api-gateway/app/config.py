from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    disaster_service_url: str
    upstream_timeout_seconds: float
    search_rate_limit_per_minute: int = 60
    reports_rate_limit_per_minute: int = 10
    max_report_body_bytes: int = 52 * 1024 * 1024
    # CHG-022 / ADR-004
    identity_service_url: str = "http://localhost:8002"
    registration_rate_limit_per_minute: int = 5
    verification_rate_limit_per_minute: int = 10
    login_rate_limit_per_minute: int = 10
    session_cookie_secure: bool = False
    allowed_origins: tuple[str, ...] = (
        "http://localhost:3100",
        "http://127.0.0.1:3100",
    )

    @classmethod
    def from_environment(cls) -> "Settings":
        origins = tuple(
            origin.strip().rstrip("/")
            for origin in os.getenv(
                "ALLOWED_ORIGINS",
                "http://localhost:3100,http://127.0.0.1:3100",
            ).split(",")
            if origin.strip()
        )
        return cls(
            disaster_service_url=os.getenv(
                "DISASTER_SERVICE_URL", "http://localhost:8001"
            ).rstrip("/"),
            upstream_timeout_seconds=float(
                os.getenv("UPSTREAM_TIMEOUT_SECONDS", "5")
            ),
            search_rate_limit_per_minute=int(
                os.getenv("SEARCH_RATE_LIMIT_PER_MINUTE", "60")
            ),
            reports_rate_limit_per_minute=int(
                os.getenv("REPORTS_RATE_LIMIT_PER_MINUTE", "10")
            ),
            identity_service_url=os.getenv(
                "IDENTITY_SERVICE_URL", "http://localhost:8002"
            ).rstrip("/"),
            registration_rate_limit_per_minute=int(
                os.getenv("REGISTRATION_RATE_LIMIT_PER_MINUTE", "5")
            ),
            verification_rate_limit_per_minute=int(
                os.getenv("VERIFICATION_RATE_LIMIT_PER_MINUTE", "10")
            ),
            login_rate_limit_per_minute=int(
                os.getenv("LOGIN_RATE_LIMIT_PER_MINUTE", "10")
            ),
            # `Secure` fuera de local (ADR-004).
            session_cookie_secure=os.getenv(
                "SESSION_COOKIE_SECURE", "false"
            ).strip().casefold()
            in {"1", "true", "yes"},
            allowed_origins=origins,
        )
