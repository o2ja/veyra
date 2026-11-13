"""Audit event catalogue (SECURITY.md §7) and the fields each entity type may record.

Only allow-listed keys reach ``before``/``after`` (S1 review finding 26): secrets such as password
hashes, MFA secrets, token or code hashes can never be written, even by mistake.
"""

from enum import StrEnum


class Action(StrEnum):
    AUTH_LOGIN_SUCCEEDED = "auth.login_succeeded"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_LOCKED = "auth.locked"
    AUTH_LOGOUT = "auth.logout"
    AUTH_MFA_ENABLED = "auth.mfa_enabled"
    AUTH_MFA_FAILED = "auth.mfa_failed"
    AUTH_RECOVERY_CODE_USED = "auth.recovery_code_used"
    AUTH_RECOVERY_CODES_REGENERATED = "auth.recovery_codes_regenerated"
    AUTH_PASSWORD_RESET_REQUESTED = "auth.password_reset_requested"  # noqa: S105  # nosec B105 (event name)
    AUTH_PASSWORD_RESET = "auth.password_reset"  # noqa: S105  # nosec B105
    AUTH_PASSWORD_CHANGED = "auth.password_changed"  # noqa: S105  # nosec B105
    AUTH_EMAIL_CHANGE_REQUESTED = "auth.email_change_requested"
    AUTH_EMAIL_CHANGED = "auth.email_changed"
    AUTH_REAUTHENTICATED = "auth.reauthenticated"
    TENANT_CREATED = "tenant.created"
    TENANT_SWITCHED = "tenant.switched"
    SETTINGS_UPDATED = "settings.updated"
    MEMBERSHIP_CREATED = "membership.created"
    MEMBERSHIP_ROLE_CHANGED = "membership.role_changed"
    MEMBERSHIP_DEACTIVATED = "membership.deactivated"
    MEMBERSHIP_REACTIVATED = "membership.reactivated"
    INVITATION_CREATED = "invitation.created"
    INVITATION_REVOKED = "invitation.revoked"
    INVITATION_ACCEPTED = "invitation.accepted"
    AUTHZ_DENIED = "authz.denied"
    FILE_UPLOADED = "file.uploaded"
    FILE_SCANNED_CLEAN = "file.scanned_clean"
    FILE_INFECTED = "file.infected"
    FILE_SCAN_ERROR = "file.scan_error"
    FILE_DOWNLOAD_URL_ISSUED = "file.download_url_issued"
    JOB_RETRIED = "job.retried"
    MANUFACTURER_CREATED = "manufacturer.created"
    MANUFACTURER_UPDATED = "manufacturer.updated"
    MANUFACTURER_SETTINGS_UPDATED = "manufacturer.settings_updated"
    REASON_CODE_MAPPINGS_REPLACED = "manufacturer.reason_code_mappings_replaced"
    CUSTOMER_CREATED = "customer.created"
    CUSTOMER_UPDATED = "customer.updated"
    CUSTOMER_ALIAS_ADDED = "customer.alias_added"
    PRODUCT_CREATED = "product.created"
    PRODUCT_UPDATED = "product.updated"
    PRODUCT_ALIAS_ADDED = "product.alias_added"
    BRANCH_CREATED = "branch.created"
    UOM_CONVERSION_CREATED = "uom_conversion.created"
    UOM_CONVERSION_UPDATED = "uom_conversion.updated"
    UOM_CONVERSION_DELETED = "uom_conversion.deleted"
    UNMAPPED_ENTITY_RESOLVED = "unmapped_entity.resolved"
    MAPPING_TEMPLATE_CREATED = "mapping_template.created"
    MAPPING_TEMPLATE_UPDATED = "mapping_template.updated"
    MAPPING_TEMPLATE_DELETED = "mapping_template.deleted"
    IMPORT_CREATED = "import.created"
    IMPORT_MAPPING_SET = "import.mapping_set"
    IMPORT_CONFIRMED = "import.confirmed"
    IMPORT_COMPLETED = "import.completed"
    IMPORT_FAILED = "import.failed"
    IMPORT_ERRORS_EXPORTED = "import.errors_exported"
    AGREEMENT_CREATED = "agreement.created"
    AGREEMENT_CANCELLED = "agreement.cancelled"
    AGREEMENT_DRAFT_CREATED = "agreement_draft.created"
    AGREEMENT_DRAFT_UPDATED = "agreement_draft.updated"
    AGREEMENT_DRAFT_ITEMS_IMPORTED = "agreement_draft.items_imported"
    AGREEMENT_DRAFT_VALIDATED = "agreement_draft.validated"
    AGREEMENT_DRAFT_SUBMITTED = "agreement_draft.submitted"
    AGREEMENT_DRAFT_RETURNED = "agreement_draft.returned"
    AGREEMENT_DRAFT_REJECTED = "agreement_draft.rejected"
    AGREEMENT_DRAFT_APPROVED = "agreement_draft.approved"
    AGREEMENT_DRAFT_FOUR_EYES_VIOLATION = "agreement_draft.four_eyes_violation"
    AGREEMENT_VERSION_ACTIVATED = "agreement_version.activated"
    AGREEMENT_VERSION_SUPERSEDED = "agreement_version.superseded"
    AGREEMENT_VERSION_EXPIRED = "agreement_version.expired"
    AGREEMENT_VERSION_CANCELLED = "agreement_version.cancelled"
    AGREEMENT_VERSION_EXPIRY_ALERT = "agreement_version.expiry_alert"
    CALCULATION_RUN_CREATED = "calculation_run.created"
    CALCULATION_RUN_COMPLETED = "calculation_run.completed"
    CALCULATION_RUN_FAILED = "calculation_run.failed"
    CALCULATION_ADJUSTMENTS_PROPOSED = "calculation_run.adjustments_proposed"
    CLAIM_ADJUSTMENT_PROPOSED = "claim_adjustment.proposed"
    CLAIM_ADJUSTMENT_APPROVED = "claim_adjustment.approved"
    CLAIM_ADJUSTMENT_REJECTED = "claim_adjustment.rejected"
    CLAIM_ADJUSTMENT_FOUR_EYES_VIOLATION = "claim_adjustment.four_eyes_violation"
    CLAIM_ADJUSTMENT_INCLUDED = "claim_adjustment.included"
    CLAIM_ADJUSTMENT_RELEASED = "claim_adjustment.released"
    CLAIM_BATCH_CREATED = "claim_batch.created"
    CLAIM_BATCH_APPROVED = "claim_batch.approved"
    CLAIM_BATCH_FOUR_EYES_VIOLATION = "claim_batch.four_eyes_violation"
    CLAIM_BATCH_EXPORT_REQUESTED = "claim_batch.export_requested"
    CLAIM_BATCH_EXPORTED = "claim_batch.exported"
    CLAIM_BATCH_SUBMITTED = "claim_batch.submitted"
    CLAIM_BATCH_VOIDED = "claim_batch.voided"
    CLAIM_TEMPLATE_CREATED = "claim_export_template.created"
    CLAIM_TEMPLATE_UPDATED = "claim_export_template.updated"
    MISSED_CLAIMS_EXPORT_REQUESTED = "missed_claims.export_requested"
    MISSED_CLAIMS_EXPORTED = "missed_claims.exported"
    VARIANCE_CASE_CREATED = "variance_case.created"
    VARIANCE_CASE_STATUS_CHANGED = "variance_case.status_changed"
    VARIANCE_CASE_ASSIGNED = "variance_case.assigned"
    VARIANCE_CASE_NOTE_ADDED = "variance_case.note_added"
    VARIANCE_CASE_ATTACHMENT_ADDED = "variance_case.attachment_added"
    VARIANCE_CASE_RECLAIM_REQUESTED = "variance_case.reclaim_requested"
    VARIANCE_CASE_WRITE_OFF_PROPOSED = "variance_case.write_off_proposed"
    VARIANCE_CASE_WRITTEN_OFF = "variance_case.written_off"
    VARIANCE_CASE_FOUR_EYES_VIOLATION = "variance_case.four_eyes_violation"
    VARIANCE_CASE_RECOVERY_RECORDED = "variance_case.recovery_recorded"
    EXTRACTION_REQUESTED = "extraction.requested"
    EXTRACTION_FINISHED = "extraction.finished"
    IMPORT_MAPPING_SUGGESTED = "import.mapping_suggested"
    AI_INVOCATION = "ai.invocation"
    REMITTANCE_CREATED = "remittance.created"
    REMITTANCE_STAGED = "remittance.staged"
    REMITTANCE_ACCEPTED = "remittance.accepted"
    REMITTANCE_VOIDED = "remittance.voided"
    REMITTANCE_FINALISED = "remittance.finalised"
    RECONCILIATION_RUN = "reconciliation.run"
    MATCH_CREATED = "reconciliation_match.created"
    MATCH_CONFIRMED = "reconciliation_match.confirmed"
    MATCH_REJECTED = "reconciliation_match.rejected"


AUDITED_FIELDS: dict[str, frozenset[str]] = {
    "variance_case": frozenset(
        {
            "case_type",
            "reason",  # not "reason_code": any key containing "code" is refused as a secret by the audit guard
            "write_off_reason",
            "status",
            "claim_line_id",
            "remittance_line_id",
            "variance_amount",
            "recovered_amount",
            "written_off_amount",
            "owner_user_id",
            "deadline_date",
            "write_off_proposed_by",
            "revision",
        }
    ),
    "ai_invocation": frozenset(
        {"extraction_id", "purpose", "model", "status", "cost_usd", "usage_in", "usage_out", "failure"}
    ),
    "document_extraction": frozenset(
        {"file_id", "kind", "status", "failure", "model", "prompt_version", "draft_id", "text_method"}
    ),
    "user": frozenset({"email", "full_name", "mfa_enabled"}),
    "tenant": frozenset({"name", "slug", "status"}),
    "settings": frozenset(
        {
            "business_timezone",
            "mfa_required",
            "four_eyes_required",
            "ai_processing_enabled",
            "retention_years",
        }
    ),
    "membership": frozenset({"user_id", "role", "status"}),
    "invitation": frozenset({"email", "role", "expires_at"}),
    "session": frozenset(),
    "permission": frozenset({"permission", "method", "route"}),
    "file": frozenset({"kind", "original_name", "size_bytes", "sha256", "scan_status"}),
    "job": frozenset({"kind", "status", "attempts"}),
    "manufacturer": frozenset({"name", "code", "external_id", "active"}),
    "manufacturer_settings": frozenset(
        {
            "claim_window_days",
            "claim_window_basis",
            "dispute_window_days",
            "dispute_window_basis",
            "allow_negative_claims",
            "conflict_policy",
            "variance_tolerance",
            "payment_lag_days_expected",
            "notes",
        }
    ),
    "customer": frozenset(
        {"name", "code", "external_id", "customer_type", "parent_customer_id", "active", "alias"}
    ),
    "product": frozenset(
        {
            "sku",
            "description",
            "manufacturer_id",
            "manufacturer_part_number",
            "base_uom",
            "category",
            "list_cost",
            "active",
            "alias",
            "alias_type",
        }
    ),
    "branch": frozenset({"name", "code"}),
    "uom_conversion": frozenset({"product_id", "from_uom", "to_uom", "factor"}),
    "unmapped_entity": frozenset({"status", "resolved_entity_id", "external_value"}),
    "mapping_template": frozenset(
        {"name", "import_type", "header_signature", "mapping", "options", "version"}
    ),
    "import_batch": frozenset(
        {
            "import_type",
            "file_id",
            "source_system",
            "status",
            "mapping_template_id",
            "mapping_revision",
            "accept_partial",
            "counts",
        }
    ),
    "agreement": frozenset(
        {
            "manufacturer_id",
            "agreement_type",
            "external_number",
            "name",
            "cancelled_at",
            "effective_date",
            "reason",
        }
    ),
    "agreement_version": frozenset(
        {
            "agreement_id",
            "version_number",
            "status",
            "valid_from",
            "valid_to",
            "draft_id",
            "derived_from_version_id",
            "supersedes_version_id",
            "superseded_by_version_id",
            "days_left",
        }
    ),
    "agreement_draft": frozenset(
        {
            "source",
            "status",
            "revision",
            "agreement_id",
            "amends_version_id",
            "source_file_id",
            "extraction_id",
            "change_summary",
            "item_count",
            "error_count",
            "warning_count",
            "acknowledged_warnings",
            "rejection_reason",
            "review_comment",
            "resulting_version_id",
        }
    ),
    "calculation_run": frozenset(
        {
            "scope",
            "status",
            "as_of",
            "run_date",
            "data_cutoff",
            "engine_version",
            "totals",
            "error",
            "proposed",
        }
    ),
    "claim_batch": frozenset(
        {
            "manufacturer_id",
            "batch_number",
            "status",
            "line_count",
            "total_amount",
            "period_from",
            "period_to",
            "export_template_id",
            "exported_file_id",
            "submitted_at",
            "external_reference",
            "void_reason",
            "included_adjustment_ids",
            "adjustments_total",
        }
    ),
    "claim_adjustment": frozenset(
        {
            "manufacturer_id",
            "adjustment_type",
            "claim_line_id",
            "rebate_accrual_entry_id",
            "calculation_result_id",
            "run_id",
            "amount",
            "reason",
            "status",
            "included_in_batch_id",
        }
    ),
    "claim_export_template": frozenset(
        {"manufacturer_id", "name", "format", "columns", "options", "active", "revision"}
    ),
    "missed_claims_export": frozenset({"format", "filter"}),
    "remittance": frozenset(
        {
            "manufacturer_id",
            "file_id",
            "doc_type",
            "channel",
            "document_number",
            "document_date",
            "payment_date",
            "total_amount",
            "status",
            "extraction_id",
            "failure",
            "line_count",
            "corrections",
            "findings",
            "run",
        }
    ),
    "reconciliation_match": frozenset(
        {"remittance_id", "remittance_line_id", "method", "status", "allocations", "allocated_amount"}
    ),
}
