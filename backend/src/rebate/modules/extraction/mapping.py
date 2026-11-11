"""S6.6 AI column-mapping suggestion (AI_PROCESSING.md §1, §9): headers + at most 5 sample rows, cells
truncated and card-number-masked, go to the model; the answer is a *suggestion* the user confirms (it is
saved as a mapping template, later files need no AI). The model may only pick listed headers.

The call is synchronous but bounded (fewer attempts, shorter timeout, no repair) and the request's database
transaction is committed *before* the provider call, so no pooled connection waits on the network."""

from typing import Any, cast
from uuid import UUID

import structlog
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from rebate.core.config import Settings
from rebate.core.db import set_tenant
from rebate.core.errors import DomainError
from rebate.modules import audit, ingestion, tenancy
from rebate.modules.audit import Action, Actor
from rebate.modules.extraction import gateway, prompts
from rebate.modules.extraction.service import AiNotEnabled, ai_engine, audit_invocations
from rebate.modules.masterdata import Who

log = structlog.get_logger("rebate.extraction")
SAMPLE_ROWS = 5
MAX_COLUMNS = 50
CELL_CHARS = 80


class AiUnavailable(DomainError):
    code = "ai_unavailable"
    status_code = 503
    message = "AI suggestions are not available right now. Map the columns manually."


def _cell(value: str) -> str:
    out = value
    while (hit := ingestion.find_card_number(out)) is not None:
        out = out.replace(hit, ingestion.CARD_REMOVED)
    return out[:CELL_CHARS]


def suggest_mapping(db: Session, who: Who, settings: Settings, import_id: UUID) -> dict[str, Any]:
    sample = ingestion.mapping_sample(db, who.tenant_id, import_id, SAMPLE_ROWS)  # 404 for foreign ids first
    enabled = tenancy.get_settings(db, who.tenant_id).ai_processing_enabled
    if not enabled:
        raise AiNotEnabled()
    fields: list[str] = sample["fields"]
    original: list[str] = sample["headers"][:MAX_COLUMNS]
    if not original:
        raise AiUnavailable()
    # what leaves the system: masked, truncated headers (first occurrence wins for duplicates) and cells
    shown = [_cell(h) for h in original]
    by_shown: dict[str, str] = {}
    for s, o in zip(shown, original, strict=True):
        by_shown.setdefault(s, o)
    rows = [[_cell(c) for c in r[:MAX_COLUMNS]] for r in sample["rows"][:SAMPLE_ROWS]]
    system, user = prompts.build_mapping_prompt(sample["import_type"], fields, shown, rows)
    engine = cast(Engine, db.get_bind())
    db.commit()  # release the request's connection: nothing below needs it until the audit entry

    def check(data: dict[str, Any]) -> None:
        mapping = data.get("mapping")
        if not isinstance(mapping, dict) or set(mapping) - set(fields):
            raise ValueError("mapping must be an object keyed by target field names")
        if any(v is not None and v not in by_shown for v in mapping.values()):
            raise ValueError("every value must be one of the listed column names or null")

    gw: gateway.Gateway | None = None
    try:
        gw = gateway.Gateway(settings, ai_engine(settings), gateway.make_provider(settings))
        result = gw.call(
            ai_enabled=enabled,
            tenant_id=who.tenant_id,
            extraction_id=None,
            purpose="mapping",
            model=settings.ai_model,
            prompt_version=prompts.MAPPING_PROMPT_VERSION,
            system=system,
            user=user,
            tool=prompts.MAPPING_TOOL_NAME,
            schema=prompts.mapping_schema(fields, list(by_shown)),
            validate=check,
            repair=False,
            max_attempts=settings.ai_mapping_max_attempts,
            timeout=settings.ai_mapping_timeout_seconds,
        )
    except gateway.GatewayError as exc:  # every provider/SDK/limit failure is a 503, never a 500
        log.warning("mapping.failed", code=exc.code, error=str(exc)[:200])
        raise AiUnavailable() from exc
    finally:
        if gw is not None:
            audit_invocations(engine, who.tenant_id, gw.take_invocations())
    set_tenant(db, who.tenant_id)  # new transaction: SET LOCAL did not survive the commit
    audit.record(
        db,
        tenant_id=who.tenant_id,
        actor=Actor("user", who.user_id),
        action=Action.IMPORT_MAPPING_SUGGESTED,
        entity_type="import_batch",
        entity_id=import_id,
        meta=who.meta,
    )
    db.commit()
    mapping = {f: by_shown.get(result.data["mapping"].get(f) or "") for f in fields}
    return {
        "mapping": mapping,
        "source": "ai",
        "model": result.model,
        "prompt_version": prompts.MAPPING_PROMPT_VERSION,
    }
