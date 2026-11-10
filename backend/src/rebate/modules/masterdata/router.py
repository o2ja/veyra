"""Master-data API (API_SPECIFICATION.md §2.3). Thin: validate, authorise, call the service, shape."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from rebate.core.access import require, tenant_of
from rebate.core.errors import PreconditionFailed
from rebate.core.query import only_query_params
from rebate.core.request import Principal, ctx
from rebate.modules.masterdata import schemas as s
from rebate.modules.masterdata import service
from rebate.modules.masterdata.pagination import page

router = APIRouter(tags=["master data"])

READ = "data.read"
WRITE = "masterdata.write"
Limit = Annotated[int, Query(ge=1, le=200)]
PAGED = ("limit", "cursor")


def _who(request: Request, principal: Principal) -> service.Who:
    return service.Who(tenant_of(principal), principal.user_id, ctx(request).audit_meta())


def _revision(if_match: str | None) -> int:
    value = (if_match or "").strip().strip('"')
    if not value.isdigit():
        raise PreconditionFailed("Send the If-Match header with the revision you edited.")
    return int(value)


# ---- manufacturers ------------------------------------------------------------------------------


@router.get(
    "/manufacturers",
    operation_id="manufacturer_list",
    dependencies=[Depends(only_query_params("active", *PAGED))],
)
def list_manufacturers(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    active: bool | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.ManufacturerList:
    rows = service.list_manufacturers(
        ctx(request).db, tenant_of(principal), active=active, limit=limit, cursor=cursor
    )
    data, p = page(rows, limit)
    return s.ManufacturerList(data=[s.ManufacturerOut.model_validate(r) for r in data], page=p)


@router.post("/manufacturers", status_code=201, operation_id="manufacturer_create")
def create_manufacturer(
    request: Request, body: s.ManufacturerIn, principal: Annotated[Principal, Depends(require(WRITE))]
) -> s.ManufacturerOut:
    row = service.create_manufacturer(ctx(request).db, _who(request, principal), body.model_dump())
    return s.ManufacturerOut.model_validate(row)


@router.get("/manufacturers/{manufacturer_id}", operation_id="manufacturer_get")
def get_manufacturer(
    request: Request, manufacturer_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.ManufacturerOut:
    row = service.get_manufacturer(ctx(request).db, tenant_of(principal), manufacturer_id)
    return s.ManufacturerOut.model_validate(row)


@router.patch("/manufacturers/{manufacturer_id}", operation_id="manufacturer_update")
def update_manufacturer(
    request: Request,
    manufacturer_id: UUID,
    body: s.ManufacturerPatch,
    principal: Annotated[Principal, Depends(require(WRITE))],
) -> s.ManufacturerOut:
    row = service.update_manufacturer(
        ctx(request).db, _who(request, principal), manufacturer_id, body.model_dump(exclude_unset=True)
    )
    return s.ManufacturerOut.model_validate(row)


@router.get("/manufacturers/{manufacturer_id}/settings", operation_id="manufacturer_settings_get")
def get_settings(
    request: Request,
    response: Response,
    manufacturer_id: UUID,
    principal: Annotated[Principal, Depends(require(READ))],
) -> s.ManufacturerSettingsOut:
    row = service.get_manufacturer_settings(ctx(request).db, tenant_of(principal), manufacturer_id)
    response.headers["ETag"] = f'"{row.revision}"'
    return s.ManufacturerSettingsOut.model_validate(row)


@router.put(
    "/manufacturers/{manufacturer_id}/settings",
    operation_id="manufacturer_settings_put",
    responses={412: {"description": "Revision mismatch"}},
)
def put_settings(
    request: Request,
    response: Response,
    manufacturer_id: UUID,
    body: s.ManufacturerSettingsIn,
    principal: Annotated[Principal, Depends(require("manufacturer_settings.write"))],
    if_match: Annotated[str | None, Header()] = None,
) -> s.ManufacturerSettingsOut:
    row = service.put_manufacturer_settings(
        ctx(request).db, _who(request, principal), manufacturer_id, _revision(if_match), body.model_dump()
    )
    response.headers["ETag"] = f'"{row.revision}"'
    return s.ManufacturerSettingsOut.model_validate(row)


@router.get("/manufacturers/{manufacturer_id}/reason-code-mappings", operation_id="reason_code_mapping_list")
def list_reason_code_mappings(
    request: Request, manufacturer_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.ReasonCodeMappingList:
    rows = service.list_reason_code_mappings(ctx(request).db, tenant_of(principal), manufacturer_id)
    return s.ReasonCodeMappingList(data=[s.ReasonCodeMappingOut.model_validate(r) for r in rows])


@router.put(
    "/manufacturers/{manufacturer_id}/reason-code-mappings", operation_id="reason_code_mapping_replace"
)
def replace_reason_code_mappings(
    request: Request,
    manufacturer_id: UUID,
    body: s.ReasonCodeMappingsIn,
    principal: Annotated[Principal, Depends(require(WRITE))],
) -> s.ReasonCodeMappingList:
    rows = service.replace_reason_code_mappings(
        ctx(request).db, _who(request, principal), manufacturer_id, [m.model_dump() for m in body.mappings]
    )
    return s.ReasonCodeMappingList(data=[s.ReasonCodeMappingOut.model_validate(r) for r in rows])


# ---- customers ----------------------------------------------------------------------------------


@router.get(
    "/customers",
    operation_id="customer_list",
    dependencies=[Depends(only_query_params("q", "active", *PAGED))],
)
def list_customers(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    q: Annotated[str | None, Query(max_length=100)] = None,
    active: bool | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.CustomerList:
    rows = service.list_customers(
        ctx(request).db, tenant_of(principal), q=q, active=active, limit=limit, cursor=cursor
    )
    data, p = page(rows, limit)
    return s.CustomerList(data=[s.CustomerOut.model_validate(r) for r in data], page=p)


@router.post("/customers", status_code=201, operation_id="customer_create")
def create_customer(
    request: Request, body: s.CustomerIn, principal: Annotated[Principal, Depends(require(WRITE))]
) -> s.CustomerOut:
    row = service.create_customer(ctx(request).db, _who(request, principal), body.model_dump())
    return s.CustomerOut.model_validate(row)


def _customer_detail(request: Request, tenant_id: UUID, customer_id: UUID) -> s.CustomerDetail:
    db = ctx(request).db
    row = service.get_customer(db, tenant_id, customer_id)
    aliases = [s.CustomerAliasOut.model_validate(a) for a in service.customer_aliases(db, tenant_id, row.id)]
    return s.CustomerDetail.model_validate(
        {**s.CustomerOut.model_validate(row).model_dump(), "aliases": aliases}
    )


@router.get("/customers/{customer_id}", operation_id="customer_get")
def get_customer(
    request: Request, customer_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.CustomerDetail:
    return _customer_detail(request, tenant_of(principal), customer_id)


@router.patch("/customers/{customer_id}", operation_id="customer_update")
def update_customer(
    request: Request,
    customer_id: UUID,
    body: s.CustomerPatch,
    principal: Annotated[Principal, Depends(require(WRITE))],
) -> s.CustomerOut:
    row = service.update_customer(
        ctx(request).db, _who(request, principal), customer_id, body.model_dump(exclude_unset=True)
    )
    return s.CustomerOut.model_validate(row)


@router.post("/customers/{customer_id}/aliases", status_code=201, operation_id="customer_alias_add")
def add_customer_alias(
    request: Request,
    customer_id: UUID,
    body: s.CustomerAliasIn,
    principal: Annotated[Principal, Depends(require(WRITE))],
) -> s.CustomerAliasOut:
    row = service.add_customer_alias(
        ctx(request).db, _who(request, principal), customer_id, body.alias, body.source
    )
    return s.CustomerAliasOut.model_validate(row)


# ---- branches -----------------------------------------------------------------------------------


@router.get("/branches", operation_id="branch_list", dependencies=[Depends(only_query_params(*PAGED))])
def list_branches(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.BranchList:
    rows = service.list_branches(ctx(request).db, tenant_of(principal), limit=limit, cursor=cursor)
    data, p = page(rows, limit)
    return s.BranchList(data=[s.BranchOut.model_validate(r) for r in data], page=p)


@router.post("/branches", status_code=201, operation_id="branch_create")
def create_branch(
    request: Request, body: s.BranchIn, principal: Annotated[Principal, Depends(require(WRITE))]
) -> s.BranchOut:
    return s.BranchOut.model_validate(
        service.create_branch(ctx(request).db, _who(request, principal), body.model_dump())
    )


# ---- products -----------------------------------------------------------------------------------


@router.get(
    "/products",
    operation_id="product_list",
    dependencies=[Depends(only_query_params("q", "manufacturer_id", "active", *PAGED))],
)
def list_products(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    q: Annotated[str | None, Query(max_length=100)] = None,
    manufacturer_id: UUID | None = None,
    active: bool | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.ProductList:
    rows = service.list_products(
        ctx(request).db,
        tenant_of(principal),
        q=q,
        manufacturer_id=manufacturer_id,
        active=active,
        limit=limit,
        cursor=cursor,
    )
    data, p = page(rows, limit)
    return s.ProductList(data=[s.ProductOut.model_validate(r) for r in data], page=p)


@router.post("/products", status_code=201, operation_id="product_create")
def create_product(
    request: Request, body: s.ProductIn, principal: Annotated[Principal, Depends(require(WRITE))]
) -> s.ProductOut:
    row = service.create_product(ctx(request).db, _who(request, principal), body.model_dump())
    return s.ProductOut.model_validate(row)


@router.get("/products/{product_id}", operation_id="product_get")
def get_product(
    request: Request, product_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.ProductDetail:
    db, tenant_id = ctx(request).db, tenant_of(principal)
    row = service.get_product(db, tenant_id, product_id)
    aliases = [s.ProductAliasOut.model_validate(a) for a in service.product_aliases(db, tenant_id, row.id)]
    return s.ProductDetail.model_validate(
        {**s.ProductOut.model_validate(row).model_dump(), "aliases": aliases}
    )


@router.patch("/products/{product_id}", operation_id="product_update")
def update_product(
    request: Request,
    product_id: UUID,
    body: s.ProductPatch,
    principal: Annotated[Principal, Depends(require(WRITE))],
) -> s.ProductOut:
    row = service.update_product(
        ctx(request).db, _who(request, principal), product_id, body.model_dump(exclude_unset=True)
    )
    return s.ProductOut.model_validate(row)


@router.post("/products/{product_id}/aliases", status_code=201, operation_id="product_alias_add")
def add_product_alias(
    request: Request,
    product_id: UUID,
    body: s.ProductAliasIn,
    principal: Annotated[Principal, Depends(require(WRITE))],
) -> s.ProductAliasOut:
    row = service.add_product_alias(
        ctx(request).db, _who(request, principal), product_id, body.alias, body.alias_type
    )
    return s.ProductAliasOut.model_validate(row)


# ---- UoM conversions ----------------------------------------------------------------------------


@router.get(
    "/uom-conversions",
    operation_id="uom_conversion_list",
    dependencies=[Depends(only_query_params("product_id", *PAGED))],
)
def list_uom_conversions(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    product_id: UUID | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.UomConversionList:
    rows = service.list_uom_conversions(
        ctx(request).db, tenant_of(principal), product_id=product_id, limit=limit, cursor=cursor
    )
    data, p = page(rows, limit)
    return s.UomConversionList(data=[s.UomConversionOut.model_validate(r) for r in data], page=p)


@router.post("/uom-conversions", status_code=201, operation_id="uom_conversion_create")
def create_uom_conversion(
    request: Request, body: s.UomConversionIn, principal: Annotated[Principal, Depends(require(WRITE))]
) -> s.UomConversionOut:
    row = service.create_uom_conversion(ctx(request).db, _who(request, principal), body.model_dump())
    return s.UomConversionOut.model_validate(row)


@router.put("/uom-conversions/{conversion_id}", operation_id="uom_conversion_update")
def update_uom_conversion(
    request: Request,
    conversion_id: UUID,
    body: s.UomConversionUpdate,
    principal: Annotated[Principal, Depends(require(WRITE))],
) -> s.UomConversionOut:
    row = service.update_uom_conversion(ctx(request).db, _who(request, principal), conversion_id, body.factor)
    return s.UomConversionOut.model_validate(row)


@router.delete("/uom-conversions/{conversion_id}", status_code=204, operation_id="uom_conversion_delete")
def delete_uom_conversion(
    request: Request, conversion_id: UUID, principal: Annotated[Principal, Depends(require(WRITE))]
) -> Response:
    service.delete_uom_conversion(ctx(request).db, _who(request, principal), conversion_id)
    return Response(status_code=204)


# ---- unmapped entities --------------------------------------------------------------------------


@router.get(
    "/unmapped-entities",
    operation_id="unmapped_entity_list",
    dependencies=[Depends(only_query_params("status", "entity_type", *PAGED))],
)
def list_unmapped(
    request: Request,
    principal: Annotated[Principal, Depends(require(WRITE))],
    status: Literal["open", "resolved", "ignored"] | None = "open",
    entity_type: Literal["customer", "product"] | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.UnmappedList:
    rows = service.list_unmapped(
        ctx(request).db,
        tenant_of(principal),
        status=status,
        entity_type=entity_type,
        limit=limit,
        cursor=cursor,
    )
    data, p = page(rows, limit)
    return s.UnmappedList(data=[s.UnmappedOut.model_validate(r) for r in data], page=p)


@router.post("/unmapped-entities/{entry_id}/resolve", operation_id="unmapped_entity_resolve")
def resolve_unmapped(
    request: Request,
    entry_id: UUID,
    body: s.ResolveIn,
    principal: Annotated[Principal, Depends(require(WRITE))],
) -> s.UnmappedOut:
    row = service.resolve_unmapped(
        ctx(request).db, _who(request, principal), entry_id, body.action, body.target_id
    )
    return s.UnmappedOut.model_validate(row)
