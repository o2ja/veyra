"""TOTP (RFC 6238 via pyotp) and recovery codes (SECURITY.md §1).

* Secret encrypted with AES-256-GCM, ``user_id`` as associated data (S1 review finding 24).
* A code is accepted for the current step ±1, and only if its step is newer than the last accepted
  step (replay of the same or an older code is rejected; review finding 21).
* Recovery codes: 10 x 80-bit base32, SHA-256 stored, consumed with a conditional UPDATE so two
  concurrent submissions cannot both succeed.
* The secret, the otpauth URI and the QR code are never logged (S1 D-8).
"""

import base64
import secrets
from uuid import UUID

import pyotp
import segno
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from rebate.core import clock
from rebate.core.ids import new_id
from rebate.core.security import SecretBox, hash_token
from rebate.modules.identity.models import MfaRecoveryCode, User

ISSUER = "Veyra Recover"
STEP_SECONDS = 30
RECOVERY_CODES = 10


def new_secret() -> str:
    return pyotp.random_base32(length=32)


def encrypt_secret(box: SecretBox, user_id: UUID, secret: str) -> bytes:
    return box.encrypt(secret.encode(), aad=user_id.bytes)


def decrypt_secret(box: SecretBox, user: User) -> str:
    if user.mfa_secret_enc is None:
        raise ValueError("user has no MFA secret")
    return box.decrypt(bytes(user.mfa_secret_enc), aad=user.id.bytes).decode()


def provisioning(secret: str, email: str) -> tuple[str, str]:
    """(otpauth URI, QR code as an SVG data URI). Returned to the user once; never logged."""
    uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)
    svg = segno.make(uri, error="m").svg_data_uri(scale=4, border=2)
    return uri, svg


def verify_totp(secret: str, code: str, last_used_step: int | None) -> int | None:
    """Return the matched time step, or None. Rejects steps ≤ ``last_used_step``."""
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    now_step = int(clock.now().timestamp()) // STEP_SECONDS
    for step in (now_step - 1, now_step, now_step + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if secrets.compare_digest(totp.at(step * STEP_SECONDS), code):
            return step
    return None


def _normalize_code(code: str) -> str:
    return code.replace("-", "").upper()


def new_recovery_codes(db: Session, user_id: UUID) -> list[str]:
    """Replace all recovery codes; returns the raw codes (shown once)."""
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = base64.b32encode(secrets.token_bytes(10)).decode().rstrip("=")  # 16 chars, 80 bits
        codes.append("-".join(raw[i : i + 4] for i in range(0, 16, 4)))
        db.add(MfaRecoveryCode(id=new_id(), user_id=user_id, code_hash=hash_token(_normalize_code(raw))))
    db.flush()
    return codes


def use_recovery_code(db: Session, user_id: UUID, code: str) -> bool:
    result = db.execute(
        update(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.user_id == user_id,
            MfaRecoveryCode.code_hash == hash_token(_normalize_code(code)),
            MfaRecoveryCode.used_at.is_(None),
        )
        .values(used_at=clock.now())
        .returning(MfaRecoveryCode.id)
    )
    return result.first() is not None


def check_code(db: Session, box: SecretBox, user: User, code: str) -> str | None:
    """TOTP or recovery code. Returns "totp" / "recovery" on success (and records use), else None."""
    if user.mfa_enabled and user.mfa_secret_enc is not None:
        step = verify_totp(decrypt_secret(box, user), code, user.mfa_last_used_step)
        if step is not None:
            user.mfa_last_used_step = step
            return "totp"
        if len(_normalize_code(code)) == 16 and use_recovery_code(db, user.id, code):
            return "recovery"
    return None
