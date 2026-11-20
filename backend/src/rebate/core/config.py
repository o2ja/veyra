"""Typed settings from the environment. Missing or invalid values fail at startup.

Defaults for security parameters are documented in docs/reports/S1_KICKOFF_REPORT.md §4; changing one
is a recorded decision (CLAUDE.md §18), not a silent edit.
"""

import base64
import binascii
from decimal import Decimal
from functools import lru_cache
from ipaddress import ip_address, ip_network
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AppEnv = Literal["local", "ci", "staging", "production"]
PLACEHOLDER = "replace-me"  # the value shipped in .env.example / .env.prod.example
PLACEHOLDER_CHECKED = (
    "secret_key",
    "database_url",
    "database_url_ai",
    "encryption_keys",
    "smtp_password",
    "s3_access_key_id",
    "s3_secret_access_key",
)


def parse_encryption_keys(raw: str) -> dict[str, bytes]:
    """``kid:base64(32 bytes)[,kid:...]``; the first entry is the active key. Order is preserved."""
    keys: dict[str, bytes] = {}
    for item in raw.split(","):
        kid, sep, b64 = item.strip().partition(":")
        if not sep or not kid.isalnum() or len(kid) > 16:
            raise ValueError("ENCRYPTION_KEYS entries must look like kid:base64key (kid alphanumeric)")
        if kid in keys:
            raise ValueError(f"duplicate encryption key id {kid!r}")
        try:
            key = base64.b64decode(b64, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"encryption key {kid!r} is not valid base64") from exc
        if len(key) != 32:
            raise ValueError(f"encryption key {kid!r} must be 32 bytes (AES-256)")
        keys[kid] = key
    return keys


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True, env_ignore_empty=True)

    app_env: AppEnv
    secret_key: SecretStr = Field(min_length=32)
    database_url: SecretStr
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Encryption of MFA secrets (S1 D-2). Rotation: DEPLOYMENT.md §2.4.
    encryption_keys: SecretStr
    public_base_url: str = "http://localhost:5173"
    trusted_proxy_ips: Annotated[tuple[str, ...], NoDecode] = ()
    # Cloudflare edge ranges (CIDR, comma-separated). CF-Connecting-IP is honoured only for requests whose
    # peer-as-seen-by-Caddy is in one of these. Source and update steps: infra/runbooks/cloudflare-ip-update.md
    cloudflare_ip_ranges: Annotated[tuple[str, ...], NoDecode] = ()

    # Passwords (SECURITY.md §1)
    password_min_length: int = 12
    password_max_length: int = 256
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 4

    # Lockout (progressive, decaying)
    lockout_threshold: int = 5
    lockout_base_minutes: int = 15
    lockout_max_minutes: int = 240
    lockout_decay_hours: int = 24

    # Sessions
    session_idle_minutes: int = 20160
    session_absolute_hours: int = 336
    session_touch_seconds: int = 60
    pending_mfa_session_minutes: int = 10
    step_up_window_minutes: int = 5
    mfa_max_attempts_per_session: int = 5

    # Tokens
    email_verify_token_hours: int = 24
    password_reset_token_minutes: int = 30
    invitation_days: int = 7

    # Email (sent by jobs; S2)
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_starttls: bool = False
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    mail_from: str = "Veyra Recover <no-reply@veyra.local>"

    # Object storage (S3 API via boto3; SeaweedFS locally and in CI, ADR-017)
    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None  # host the browser uses for presigned URLs
    s3_region: str = "us-east-1"
    s3_bucket: str = "rebate-files"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    presigned_url_seconds: int = Field(default=300, ge=10, le=300)  # SECURITY.md §4: at most 5 minutes

    # Password spraying: login attempts per client IP per minute across all accounts (SECURITY.md §1)
    login_ip_limit_per_minute: int = Field(default=20, ge=1)

    # Uploads and malware scanning (SECURITY.md §4)
    upload_max_mb_spreadsheet: int = Field(default=50, ge=1, le=50)
    upload_max_mb_pdf: int = Field(default=25, ge=1, le=50)
    # every request body except file uploads (draft payloads <= 5 MB, API_SPECIFICATION.md §1)
    body_max_mb: int = Field(default=6, ge=1, le=50)
    clamd_host: str = "clamav"
    clamd_port: int = 3310

    # AI gateway (S6, AI_PROCESSING.md). OD-2 is unresolved: with no key configured no provider call is possible.
    database_url_ai: SecretStr | None = (
        None  # rebate_ai_writer connection; the gateway refuses to run without it
    )
    ai_provider: Literal["anthropic", "gemini", "openrouter"] = "anthropic"
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    # OpenRouter routes to third-party hosts: "deny" only uses hosts that do not store or train on prompts (OD-2)
    openrouter_data_collection: Literal["allow", "deny"] = "deny"
    ai_model: str = "claude-sonnet-5-5"
    ai_model_pass_b: str | None = None  # a different model for pass B, if configured
    ai_max_output_tokens: int = Field(default=16000, ge=256, le=64000)
    ai_request_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    ai_max_attempts: int = Field(default=4, ge=1, le=6)  # per call: timeout/5xx/429 (AI_PROCESSING.md 10)
    ai_backoff_base_seconds: float = Field(default=2.0, ge=0)
    ai_backoff_max_seconds: float = Field(default=30.0, ge=0)
    # ASSUMPTION: list prices in USD per million tokens; review with the provider terms (OD-2).
    # For Gemini, the owner must set AI_PRICE_INPUT_PER_MTOK and AI_PRICE_OUTPUT_PER_MTOK.
    ai_price_input_per_mtok: Decimal = Decimal("3.00")
    ai_price_output_per_mtok: Decimal = Decimal("15.00")
    ai_daily_cost_cap_usd: Decimal = Decimal("25.00")  # per tenant and UTC day
    ai_daily_token_cap: int = Field(default=2_000_000, ge=1)
    ai_lock_timeout_seconds: float = Field(
        default=120.0, gt=0, le=600
    )  # wait for the workspace's AI slot (jobs)
    ai_requests_per_minute: int = Field(
        default=10, ge=1
    )  # per tenant: POST /document-extractions, suggest-mapping
    ai_max_active_extractions: int = Field(
        default=3, ge=1
    )  # per tenant: queued + processing extract_agreement
    ai_mapping_max_attempts: int = Field(default=2, ge=1, le=4)  # synchronous path: bounded total time
    ai_mapping_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    ocr_timeout_seconds: int = Field(default=180, ge=10, le=900)
    ocr_memory_mb: int = Field(default=4096, ge=512)

    @field_validator("trusted_proxy_ips", "cloudflare_ip_ranges", mode="before")
    @classmethod
    def _split_ips(cls, value: object) -> object:
        if isinstance(value, str):
            value = tuple(v.strip() for v in value.split(",") if v.strip())
        return value

    @field_validator("trusted_proxy_ips")
    @classmethod
    def _valid_ips(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for ip in value:
            ip_address(ip)  # raises ValueError for anything that is not an IP
        return value

    @field_validator("cloudflare_ip_ranges")
    @classmethod
    def _valid_ranges(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for cidr in value:
            ip_network(cidr, strict=False)  # raises ValueError for anything that is not a CIDR
        return value

    @field_validator("encryption_keys")
    @classmethod
    def _valid_keys(cls, value: SecretStr) -> SecretStr:
        parse_encryption_keys(value.get_secret_value())
        return value

    @model_validator(mode="after")
    def _environment_rules(self) -> Self:
        hosted = self.app_env in ("staging", "production")
        if hosted and not self.smtp_starttls:
            raise ValueError("SMTP_STARTTLS must be on in staging/production (no plaintext SMTP)")
        if hosted and not self.trusted_proxy_ips:
            raise ValueError("TRUSTED_PROXY_IPS must list the reverse proxy in staging/production")
        for name in PLACEHOLDER_CHECKED:
            value = getattr(self, name)
            if hosted and value is not None and PLACEHOLDER in value.get_secret_value():
                raise ValueError(f"{name.upper()} still holds the '{PLACEHOLDER}' placeholder")
        url = urlsplit(self.public_base_url)
        if url.scheme not in ("http", "https") or not url.netloc or url.path not in ("", "/"):
            raise ValueError("PUBLIC_BASE_URL must be an origin such as https://app.example.com")
        if hosted and url.scheme != "https":
            raise ValueError("PUBLIC_BASE_URL must use https in staging/production")
        if hosted and not (self.s3_access_key_id and self.s3_secret_access_key):
            raise ValueError("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY are required in staging/production")
        public_s3 = self.s3_public_endpoint_url or self.s3_endpoint_url
        if hosted and public_s3 is not None and urlsplit(public_s3).scheme != "https":
            raise ValueError("presigned download URLs must use https in staging/production")
        return self

    @property
    def public_origin(self) -> str:
        url = urlsplit(self.public_base_url)
        return f"{url.scheme}://{url.netloc}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # values come from the environment
