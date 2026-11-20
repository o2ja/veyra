"""masterdata module: manufacturers (+settings), customers, products, aliases, UoM conversions, branches,
reason-code mappings, unmapped-entity queue (FR-IM-01, FR-IM-09). Public API below."""

from rebate.modules.masterdata.calc_reads import (
    customer_parent_rows,
    existing_manufacturer_ids,
    manufacturer_ids,
    manufacturer_settings_rows,
    product_alias_rows,
    uom_conversion_rows,
)
from rebate.modules.masterdata.pagination import Page, decode_cursor, encode_cursor, keyset, page
from rebate.modules.masterdata.router import router
from rebate.modules.masterdata.service import (
    LoadCounts,
    MasterIndex,
    ParentCycle,
    Who,
    check_parent_cycles,
    get_manufacturer_settings,
    list_reason_code_mappings,
    load_customers,
    load_products,
    load_uom_conversions,
    master_index,
    record_unmapped,
    resolve_matching,
    unmapped_open_count,
)

__all__ = [
    "LoadCounts",
    "MasterIndex",
    "Page",
    "ParentCycle",
    "Who",
    "check_parent_cycles",
    "customer_parent_rows",
    "decode_cursor",
    "encode_cursor",
    "existing_manufacturer_ids",
    "get_manufacturer_settings",
    "keyset",
    "list_reason_code_mappings",
    "load_customers",
    "load_products",
    "load_uom_conversions",
    "manufacturer_ids",
    "manufacturer_settings_rows",
    "master_index",
    "page",
    "product_alias_rows",
    "record_unmapped",
    "resolve_matching",
    "router",
    "unmapped_open_count",
    "uom_conversion_rows",
]
