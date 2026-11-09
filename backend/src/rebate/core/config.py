"""Typed settings from the environment. Missing or invalid values fail at startup.

Defaults for security parameters are documented in docs/reports/S1_KICKOFF_REPORT.md §4; changing one
is a recorded decision (CLAUDE.md §18), not a silent edit.
"""

import base64
import binascii
from functools import lru_cache
from ipaddress import ip_address
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AppEnv = Literal["local", "ci", "staging", "production"]


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
    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    app_env: AppEnv
    secret_key: SecretStr = Field(min_length=32)
    database_url: SecretStr
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Encryption of MFA secrets (S1 D-2). Rotation: DEPLOYMENT.md §2.4.
    encryption_keys: SecretStr
    public_base_url: str = "http://localhost:5173"
    trusted_proxy_ips: Annotated[tuple[str, ...], NoDecode] = ()

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
    session_idle_minutes: int = 30
    session_absolute_hours: int = 12
    session_touch_seconds: int = 60
    pending_mfa_session_minutes: int = 10
    step_up_window_minutes: int = 5
    mfa_max_attempts_per_session: int = 5

    # Tokens
    email_verify_token_hours: int = 24
    password_reset_token_minutes: int = 30
    invitation_days: int = 7

    # Email: temporary in-process sender until S2 (PROJECT_STATUS.md K-9)
    email_backend: Literal["smtp_inprocess"] = "smtp_inprocess"
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    mail_from: str = "Veyra Recover <no-reply@veyra.local>"

    @field_validator("trusted_proxy_ips", mode="before")
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

    @field_validator("encryption_keys")
    @classmethod
    def _valid_keys(cls, value: SecretStr) -> SecretStr:
        parse_encryption_keys(value.get_secret_value())
        return value

    @model_validator(mode="after")
    def _environment_rules(self) -> Self:
        hosted = self.app_env in ("staging", "production")
        if hosted and self.email_backend == "smtp_inprocess":
            raise ValueError(
                "EMAIL_BACKEND=smtp_inprocess is a temporary local/CI-only sender (K-9); "
                "the job-based sender replaces it in S2"
            )
        if hosted and not self.trusted_proxy_ips:
            raise ValueError("TRUSTED_PROXY_IPS must list the reverse proxy in staging/production")
        url = urlsplit(self.public_base_url)
        if url.scheme not in ("http", "https") or not url.netloc or url.path not in ("", "/"):
            raise ValueError("PUBLIC_BASE_URL must be an origin such as https://app.example.com")
        if hosted and url.scheme != "https":
            raise ValueError("PUBLIC_BASE_URL must use https in staging/production")
        return self

    @property
    def public_origin(self) -> str:
        url = urlsplit(self.public_base_url)
        return f"{url.scheme}://{url.netloc}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # values come from the environment
