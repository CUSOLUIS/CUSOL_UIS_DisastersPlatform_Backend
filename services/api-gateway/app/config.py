from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    disaster_service_url: str
    upstream_timeout_seconds: float
    search_rate_limit_per_minute: int = 60
    reports_rate_limit_per_minute: int = 10
    max_report_body_bytes: int = 52 * 1024 * 1024

    @classmethod
    def from_environment(cls) -> "Settings":
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
        )
