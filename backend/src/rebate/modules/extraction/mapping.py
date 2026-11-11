"""S6.6 AI column-mapping suggestion (AI_PROCESSING.md §1, §9): headers + at most 5 sample rows, cells
truncated and card-number-masked, go to the model; the answer is a *suggestion* the user confirms (it is
saved as a mapping template, later files need no AI). The model may only pick listed headers."""

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from rebate.core.config import Settings
from rebate.core.errors import DomainError
from rebate.modules import audit, ingestion, tenancy
from rebate.modules.audit import Action, Actor
from rebate.modules.extraction import gateway, prompts
from rebate.modules.extraction.service import AiNotEnabled, ai_engine
from rebate.modules.masterdata import Who

SAMPLE_ROWS = 5
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
    headers, fields = sample["headers"], sample["fields"]
    if not headers:
        raise AiUnavailable()
    rows = [[_cell(c) for c in r] for r in sample["rows"][:SAMPLE_ROWS]]
    system, user = prompts.build_mapping_prompt(sample["import_type"], fields, headers, rows)

    def check(data: dict[str, Any]) -> None:
        mapping = data.get("mapping")
        if not isinstance(mapping, dict) or set(mapping) - set(fields):
            raise ValueError("mapping must be an object keyed by target field names")
        if any(v is not None and v not in headers for v in mapping.values()):
            raise ValueError("every value must be one of the listed column names or null")

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
            schema=prompts.mapping_schema(fields, headers),
            validate=check,
        )
    except gateway.CapReached as exc:
        raise AiUnavailable() from exc
    except gateway.GatewayError as exc:
        raise AiUnavailable() from exc
    mapping = {f: result.data["mapping"].get(f) for f in fields}
    audit.record(
        db,
        tenant_id=who.tenant_id,
        actor=Actor("user", who.user_id),
        action=Action.IMPORT_MAPPING_SUGGESTED,
        entity_type="import_batch",
        entity_id=import_id,
        meta=who.meta,
    )
    return {
        "mapping": mapping,
        "source": "ai",
        "model": result.model,
        "prompt_version": prompts.MAPPING_PROMPT_VERSION,
    }
