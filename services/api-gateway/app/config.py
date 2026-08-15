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
    # CHG-034: límites separados por búsqueda de directorio, aporte
    # anónimo (por origen) y aporte con cuenta (por cuenta).
    directory_search_rate_limit_per_minute: int = 60
    anonymous_contribution_rate_limit_per_minute: int = 5
    account_contribution_rate_limit_per_minute: int = 10
    # CHG-035: límite separado para el ingreso anónimo de reportes de
    # edificio sin verificar.
    building_reports_rate_limit_per_minute: int = 5
    # CHG-036: límites administrativos por cuenta autenticada.
    admin_rate_limit_per_minute: int = 240
    admin_evidence_rate_limit_per_minute: int = 30
    # CHG-044: límites separados de ofertas comunitarias por cuenta y
    # tope del cuerpo JSON (sin fotos, muy por debajo del multipart).
    aid_offer_write_rate_limit_per_minute: int = 10
    aid_offer_read_rate_limit_per_minute: int = 60
    max_aid_offer_body_bytes: int = 64 * 1024

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
            directory_search_rate_limit_per_minute=int(
                os.getenv(
                    "DIRECTORY_SEARCH_RATE_LIMIT_PER_MINUTE", "60"
                )
            ),
            anonymous_contribution_rate_limit_per_minute=int(
                os.getenv(
                    "ANONYMOUS_CONTRIBUTION_RATE_LIMIT_PER_MINUTE", "5"
                )
            ),
            account_contribution_rate_limit_per_minute=int(
                os.getenv(
                    "ACCOUNT_CONTRIBUTION_RATE_LIMIT_PER_MINUTE", "10"
                )
            ),
            building_reports_rate_limit_per_minute=int(
                os.getenv(
                    "BUILDING_REPORTS_RATE_LIMIT_PER_MINUTE", "5"
                )
            ),
            admin_rate_limit_per_minute=int(
                os.getenv("ADMIN_RATE_LIMIT_PER_MINUTE", "240")
            ),
            admin_evidence_rate_limit_per_minute=int(
                os.getenv(
                    "ADMIN_EVIDENCE_RATE_LIMIT_PER_MINUTE", "30"
                )
            ),
            aid_offer_write_rate_limit_per_minute=int(
                os.getenv(
                    "AID_OFFER_WRITE_RATE_LIMIT_PER_MINUTE", "10"
                )
            ),
            aid_offer_read_rate_limit_per_minute=int(
                os.getenv(
                    "AID_OFFER_READ_RATE_LIMIT_PER_MINUTE", "60"
                )
            ),
        )
