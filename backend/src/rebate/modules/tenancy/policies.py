"""Permission registry: the matrix in SECURITY.md §3, as data. Roles are read per request.

Four-eyes helper (SECURITY.md §3): the maker of a draft/batch/adjustment/write-off cannot approve it
when the workspace setting ``four_eyes_required`` is on. First used in S4; tested here.
"""

from uuid import UUID

from rebate.core.errors import FourEyesViolation

ROLES = ("admin", "approver", "analyst", "viewer")

_ALL = frozenset(ROLES)
_ADMIN = frozenset({"admin"})
_APPROVERS = frozenset({"admin", "approver"})
_WRITERS = frozenset({"admin", "approver", "analyst"})

# permission -> roles that hold it
PERMISSIONS: dict[str, frozenset[str]] = {
    "user.manage": _ADMIN,
    "invitation.manage": _ADMIN,
    "settings.write": _ADMIN,
    "jobs.manage": _ADMIN,
    "settings.read": _ALL,
    "data.read": _ALL,
    "report.read": _ALL,
    "audit.read": frozenset({"admin", "approver", "viewer"}),  # SECURITY.md §3/§7, API §2.9
    "file.upload": _WRITERS,
    "import.run": _WRITERS,
    "masterdata.write": _WRITERS,
    "mapping.write": _WRITERS,
    "agreement.draft": _WRITERS,
    "agreement.approve": _APPROVERS,
    "calculation.run": _WRITERS,
    "claim_batch.create": _WRITERS,
    "claim_batch.export": _WRITERS,
    "claim_batch.approve": _APPROVERS,
    "claim_batch.submit": _APPROVERS,
    "claim_batch.void": _APPROVERS,
    "adjustment.approve": _APPROVERS,
    "remittance.reconcile": _WRITERS,
    "case.work": _WRITERS,
    "case.write_off": _APPROVERS,
    "ai.use": _WRITERS,
}


def permissions_for(role: str) -> frozenset[str]:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    return frozenset(p for p, roles in PERMISSIONS.items() if role in roles)


def check_four_eyes(*, required: bool, maker_user_ids: set[UUID], approver_user_id: UUID) -> None:
    """Raise if four-eyes applies and the approver created or last edited the item."""
    if required and approver_user_id in maker_user_ids:
        raise FourEyesViolation()
