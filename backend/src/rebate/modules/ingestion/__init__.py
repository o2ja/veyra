"""ingestion module: import batches, mapping templates, readers/normalisers, loader, source transaction
tables and their browse API (FR-IM-01..08). Public API below.

The pure file readers, normalisers and the ``Idempotency-Key`` helpers are also used by the agreements
module (SPA item import, ``POST /agreement-drafts/{id}/approve``)."""

from rebate.modules.ingestion import jobs as _jobs  # noqa: F401  (registers job handlers)
from rebate.modules.ingestion.calc_reads import (
    line_quantities,
    purchase_volume_rows,
    returned_before,
    returned_quantities,
    sales_line_ids,
    sales_line_windows,
    sales_rows,
    sales_volume_rows,
)
from rebate.modules.ingestion.idempotency import parse_key, replay, store
from rebate.modules.ingestion.normalizers import (
    CARD_REMOVED,
    NormError,
    NumberFormat,
    canonical_code,
    canonical_uom,
    find_card_number,
    normalise_header,
    parse_date,
    parse_decimal,
    parse_int,
    text_value,
)
from rebate.modules.ingestion.readers import EXTRA_CELLS, ReadError, Source, csv_source, records, xlsx_source
from rebate.modules.ingestion.router import router
from rebate.modules.ingestion.service import get_template, mapping_sample, waiting_count
from rebate.modules.ingestion.validators import FIELDS, Field, MappingInvalid, check_mapping, suggest_mapping

__all__ = [
    "CARD_REMOVED",
    "EXTRA_CELLS",
    "FIELDS",
    "Field",
    "MappingInvalid",
    "NormError",
    "NumberFormat",
    "ReadError",
    "Source",
    "canonical_code",
    "canonical_uom",
    "check_mapping",
    "csv_source",
    "find_card_number",
    "get_template",
    "line_quantities",
    "mapping_sample",
    "normalise_header",
    "parse_date",
    "parse_decimal",
    "parse_int",
    "parse_key",
    "purchase_volume_rows",
    "records",
    "replay",
    "returned_before",
    "returned_quantities",
    "router",
    "sales_line_ids",
    "sales_line_windows",
    "sales_rows",
    "sales_volume_rows",
    "store",
    "suggest_mapping",
    "text_value",
    "waiting_count",
    "xlsx_source",
]
