"""tenancy module: workspaces, settings, memberships, invitations, permission registry. Public API."""

from rebate.modules.tenancy import jobs as _jobs  # noqa: F401  (registers job handlers)
from rebate.modules.tenancy.policies import PERMISSIONS, ROLES, check_four_eyes, permissions_for
from rebate.modules.tenancy.router import router
from rebate.modules.tenancy.service import (
    LastAdmin,
    MembershipRow,
    accept_invitation,
    active_tenant_ids,
    change_membership,
    create_workspace,
    get_settings,
    is_active,
    list_memberships,
    membership,
    memberships_of,
    open_invitation,
    tenant_name,
)

__all__ = [
    "PERMISSIONS",
    "ROLES",
    "LastAdmin",
    "MembershipRow",
    "accept_invitation",
    "active_tenant_ids",
    "change_membership",
    "check_four_eyes",
    "create_workspace",
    "get_settings",
    "is_active",
    "list_memberships",
    "membership",
    "memberships_of",
    "open_invitation",
    "permissions_for",
    "router",
    "tenant_name",
]
