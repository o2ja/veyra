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
    canonical_code,
    canonical_uom,
    find_card_number,
    normalise_header,
    parse_decimal,
)
from rebate.modules.ingestion.readers import ReadError, csv_source, records, xlsx_source
from rebate.modules.ingestion.router import router
from rebate.modules.ingestion.service import mapping_sample

__all__ = [
    "CARD_REMOVED",
    "NormError",
    "ReadError",
    "canonical_code",
    "canonical_uom",
    "csv_source",
    "find_card_number",
    "line_quantities",
    "mapping_sample",
    "normalise_header",
    "parse_decimal",
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
    "xlsx_source",
]
