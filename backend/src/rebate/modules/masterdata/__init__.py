"""masterdata module: manufacturers (+settings), customers, products, aliases, UoM conversions, branches,
reason-code mappings, unmapped-entity queue (FR-IM-01, FR-IM-09). Public API below."""

from rebate.modules.masterdata.pagination import Page, decode_cursor, encode_cursor, keyset, page
from rebate.modules.masterdata.router import router
from rebate.modules.masterdata.service import (
    LoadCounts,
    ParentCycle,
    MasterIndex,
    Who,
    load_customers,
    load_products,
    load_uom_conversions,
    master_index,
    record_unmapped,
    resolve_matching,
)

__all__ = [
    "LoadCounts",
    "ParentCycle",
    "MasterIndex",
    "Page",
    "Who",
    "decode_cursor",
    "encode_cursor",
    "keyset",
    "load_customers",
    "load_products",
    "load_uom_conversions",
    "master_index",
    "page",
    "record_unmapped",
    "resolve_matching",
    "router",
]
