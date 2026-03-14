from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Base de datos
    database_url: str = Field(..., description="PostgreSQL async URL")

    # Redis / Celery
    redis_url: str = Field(default="redis://localhost:6379/0")
    celery_broker: str = Field(default="redis://localhost:6379/0")
    celery_backend: str = Field(default="redis://localhost:6379/1")

    # Seguridad (para Fernet)
    secret_key: str = Field(..., min_length=32)

    # CAPTCHA
    captcha_provider: str = Field(default="capsolver")  # "2captcha" o "capsolver"
    twocaptcha_api_key: str = Field(default="")
    capsolver_api_key: str = Field(default="")
    captcha_assisted_mode: str = Field(default="off")  # off | fallback | only
    captcha_assisted_timeout_sec: int = Field(default=180)

    # Playwright
    playwright_headless: bool = Field(default=True)
    browser_prefer_nodriver: bool = Field(default=True)
    browser_persistent_context: bool = Field(default=True)
    browser_executable_path: str = Field(default="")
    browser_channel: str = Field(default="")
    browser_profile_path: str = Field(default="./chrome_profile")
    browser_proxy_server: str = Field(default="")
    browser_proxy_username: str = Field(default="")
    browser_proxy_password: str = Field(default="")
    browser_proxy_bypass: str = Field(default="")
    browser_timeout_ms: int = Field(default=30_000)
    download_timeout_ms: int = Field(default=45_000)
    recaptcha_sitekey_fallback: str = Field(default="")

    # Anti-detección
    delay_min_ms: int = Field(default=800)
    delay_max_ms: int = Field(default=2_500)
    delay_between_pages_ms: int = Field(default=2_000)

    # Inteligencia avanzada de CAPTCHA
    fingerprint_rotation: bool = Field(default=True)
    session_warmup: bool = Field(default=True)
    behavior_simulation: bool = Field(default=True)
    provider_race_mode: bool = Field(default=False)
    trap_detection: bool = Field(default=True)
    token_prevalidation: bool = Field(default=True)
    pattern_analysis_enabled: bool = Field(default=True)
    pattern_analysis_interval_hours: int = Field(default=6)

    # Proxy pool (comma-separated: host:port:user:pass:label:geo)
    proxy_pool_urls: str = Field(default="")
    proxy_rotation: bool = Field(default=False)

    # Adaptive strategy TTLs (configurable)
    adaptive_stats_ttl_days: int = Field(default=7)
    adaptive_block_ttl_hours: int = Field(default=2)

    # Reintentos
    max_retries: int = Field(default=5)
    retry_wait_min_sec: int = Field(default=4)
    retry_wait_max_sec: int = Field(default=300)
    circuit_breaker_threshold: int = Field(default=5)
    circuit_breaker_timeout_min: int = Field(default=30)

    # Concurrencia
    max_concurrent_tenants: int = Field(default=3)
    browser_pool_limit: int = Field(default=3)
    database_pool_size: int = Field(default=10)
    database_max_overflow: int = Field(default=20)

    # Storage
    xml_storage_path: str = Field(default="./xmls")
    screenshot_path: str = Field(default="./screenshots")
    screenshot_on_error: bool = Field(default=True)

    # Scheduler
    schedule_hour: int = Field(default=6)
    schedule_minute: int = Field(default=30)

    # Notificaciones (opcional)
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="")
    smtp_pass: str = Field(default="")
    alert_email: str = Field(default="")

    # Sentry (opcional)
    sentry_dsn: str = Field(default="")

    def configured_captcha_providers(self) -> list[str]:
        providers: list[str] = []
        if self.capsolver_api_key:
            providers.append("capsolver")
        if self.twocaptcha_api_key:
            providers.append("2captcha")
        return providers

    def captcha_assisted_enabled(self) -> bool:
        return self.captcha_assisted_mode.lower().strip() in {
            "fallback",
            "only",
        }


def get_settings() -> Settings:
    return Settings()
