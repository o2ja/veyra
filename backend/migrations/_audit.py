"""Audit helper (DATABASE_SCHEMA.md §10): the audit_log table is append-only for everyone.

``make_append_only`` already installs the UPDATE/DELETE/TRUNCATE trigger and revokes the privileges
from rebate_app; this module only names the table so later migrations (e.g. S11 sealing) reuse it.
"""

AUDIT_TABLE = "audit_log"
