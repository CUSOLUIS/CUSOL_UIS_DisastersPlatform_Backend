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
    # CHG-091: sugerencias mientras se escribe; con debounce de 400 ms
    # un tecleo rapido puede producir ~2 consultas/s en rafagas cortas.
    suggestions_rate_limit_per_minute: int = 120
    # CHG-082: sonda de la señal de cambios (una consulta cada ~10 s
    # por pestaña abierta).
    change_signal_rate_limit_per_minute: int = 30
    anonymous_contribution_rate_limit_per_minute: int = 5
    account_contribution_rate_limit_per_minute: int = 10
    # CHG-035: límite separado para el ingreso anónimo de reportes de
    # edificio sin verificar.
    building_reports_rate_limit_per_minute: int = 5
    # CHG-125: solicitudes «Necesitamos ayuda» — alta pública por
    # origen, lectura por origen (el dashboard sondea cada 30 s) y
    # atención por cuenta autenticada.
    help_request_rate_limit_per_minute: int = 5
    help_request_read_rate_limit_per_minute: int = 60
    help_attend_rate_limit_per_minute: int = 30
    # CHG-163: ofertas «Ofrecer comida» — el alta comparte los
    # limitadores de contribución; la lectura tiene el suyo (el
    # dashboard sondea cada 30 s, como las solicitudes).
    food_offer_read_rate_limit_per_minute: int = 60
    # CHG-205: la gemela de alojamiento lleva su propio cupo de
    # lectura; el mapa consulta las dos listas por separado.
    shelter_offer_read_rate_limit_per_minute: int = 60
    # CHG-171: lectura pública de La Mulera (ciudades + viajes activos,
    # el mapa sondea) y posiciones del GPS del conductor (~cada 20 s).
    transport_read_rate_limit_per_minute: int = 60
    transport_position_rate_limit_per_minute: int = 12
    # CHG-036: límites administrativos por cuenta autenticada.
    admin_rate_limit_per_minute: int = 240
    admin_evidence_rate_limit_per_minute: int = 30
    # CHG-044: límites separados de ofertas comunitarias por cuenta y
    # tope del cuerpo JSON (sin fotos, muy por debajo del multipart).
    aid_offer_write_rate_limit_per_minute: int = 10
    aid_offer_read_rate_limit_per_minute: int = 60
    max_aid_offer_body_bytes: int = 64 * 1024
    # CHG-066: reportes de presencia por dispositivo (throttle cliente
    # ~30 s; el límite cubre abusos).
    presence_rate_limit_per_minute: int = 10
    # CHG-111: la consulta de revisión la usa el pipeline (una vez por
    # despliegue) y cualquiera que quiera comprobar qué corre; con 30
    # por minuto y origen sobra sin abrir un vector de amplificación.
    version_rate_limit_per_minute: int = 30
    # CHG-111: commit del que salió la imagen. Lo inyecta el build
    # (`--build-arg GIT_REVISION`); sin él la imagen no sabe de dónde
    # viene y lo dice, en vez de fingir una revisión.
    git_revision: str = "unknown"
    # CHG-126: métricas del sistema para la consola admin — una
    # muestra cada 5 s, con 180 en memoria (15 min de historia).
    system_metrics_sample_seconds: float = 5.0
    system_metrics_history_samples: int = 180
    # CHG-147: proxy de geocodificación. Nominatim exige User-Agent
    # identificable y uso moderado; la caché corta absorbe los
    # reintentos y el arrastre del muñequito sin repetir consultas.
    geocode_base_url: str = "https://nominatim.openstreetmap.org"
    geocode_user_agent: str = (
        "CUSOL-UIS-DisastersPlatform/0.1 "
        "(+https://cusoldisasterplatform.com)"
    )
    geocode_timeout_seconds: float = 6.0
    geocode_rate_limit_per_minute: int = 30
    geocode_cache_seconds: float = 600.0
    geocode_cache_max_entries: int = 512

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
            suggestions_rate_limit_per_minute=int(
                os.getenv("SUGGESTIONS_RATE_LIMIT_PER_MINUTE", "120")
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
            help_request_rate_limit_per_minute=int(
                os.getenv("HELP_REQUEST_RATE_LIMIT_PER_MINUTE", "5")
            ),
            help_request_read_rate_limit_per_minute=int(
                os.getenv(
                    "HELP_REQUEST_READ_RATE_LIMIT_PER_MINUTE", "60"
                )
            ),
            help_attend_rate_limit_per_minute=int(
                os.getenv("HELP_ATTEND_RATE_LIMIT_PER_MINUTE", "30")
            ),
            food_offer_read_rate_limit_per_minute=int(
                os.getenv(
                    "FOOD_OFFER_READ_RATE_LIMIT_PER_MINUTE", "60"
                )
            ),
            shelter_offer_read_rate_limit_per_minute=int(
                os.getenv(
                    "SHELTER_OFFER_READ_RATE_LIMIT_PER_MINUTE", "60"
                )
            ),
            transport_read_rate_limit_per_minute=int(
                os.getenv("TRANSPORT_READ_RATE_LIMIT_PER_MINUTE", "60")
            ),
            transport_position_rate_limit_per_minute=int(
                os.getenv(
                    "TRANSPORT_POSITION_RATE_LIMIT_PER_MINUTE", "12"
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
            presence_rate_limit_per_minute=int(
                os.getenv("PRESENCE_RATE_LIMIT_PER_MINUTE", "10")
            ),
            version_rate_limit_per_minute=int(
                os.getenv("VERSION_RATE_LIMIT_PER_MINUTE", "30")
            ),
            git_revision=os.getenv("GIT_REVISION", "unknown").strip()
            or "unknown",
            system_metrics_sample_seconds=float(
                os.getenv("SYSTEM_METRICS_SAMPLE_SECONDS", "5")
            ),
            system_metrics_history_samples=int(
                os.getenv("SYSTEM_METRICS_HISTORY_SAMPLES", "180")
            ),
            geocode_base_url=os.getenv(
                "GEOCODE_BASE_URL", "https://nominatim.openstreetmap.org"
            ).rstrip("/"),
            geocode_user_agent=os.getenv(
                "GEOCODE_USER_AGENT",
                "CUSOL-UIS-DisastersPlatform/0.1 "
                "(+https://cusoldisasterplatform.com)",
            ).strip(),
            geocode_timeout_seconds=float(
                os.getenv("GEOCODE_TIMEOUT_SECONDS", "6")
            ),
            geocode_rate_limit_per_minute=int(
                os.getenv("GEOCODE_RATE_LIMIT_PER_MINUTE", "30")
            ),
            geocode_cache_seconds=float(
                os.getenv("GEOCODE_CACHE_SECONDS", "600")
            ),
            geocode_cache_max_entries=int(
                os.getenv("GEOCODE_CACHE_MAX_ENTRIES", "512")
            ),
        )
