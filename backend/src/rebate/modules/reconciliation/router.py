"""Remittances and reconciliation (API_SPECIFICATION.md §2.7). Thin: validate, authorise, call the service, shape.
Reads need ``data.read``; everything that changes data needs ``remittance.reconcile`` (admin, approver, analyst).
``POST /remittances`` and ``…/finalize`` need an ``Idempotency-Key``; confirming/rejecting a match needs
``If-Match: "<revision>"``. A foreign tenant's id is a 404."""

from collections import defaultdict
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from rebate.core.access import require, tenant_of
from rebate.core.errors import PreconditionFailed
from rebate.core.query import only_query_params
from rebate.core.request import Principal, ctx
from rebate.modules import claims, ingestion
from rebate.modules.claims import RunJobRef
from rebate.modules.masterdata import Page, Who, page
from rebate.modules.reconciliation import ingest, matching
from rebate.modules.reconciliation import schemas as s
from rebate.modules.reconciliation.models import ReconciliationMatch

router = APIRouter(tags=["reconciliation"])

READ = "data.read"
WRITE = "remittance.reconcile"
Limit = Annotated[int, Query(ge=1, le=200)]
PAGED = ("limit", "cursor")


def _who(request: Request, principal: Principal) -> Who:
    return Who(tenant_of(principal), principal.user_id, ctx(request).audit_meta())


def _revision(if_match: str | None) -> int:
    value = (if_match or "").strip().strip('"')
    if not value.isdigit():
        raise PreconditionFailed("Send the If-Match header with the revision you saw.")
    return int(value)


def _job(job_id: UUID, remittance_id: UUID) -> RunJobRef:
    return RunJobRef(
        id=job_id, status="queued", progress=0, result_url=f"/api/v1/remittances/{remittance_id}"
    )


def _match_outs(
    request: Request, tenant_id: UUID, groups: list[list[ReconciliationMatch]]
) -> list[s.MatchOut]:
    ids = [r.claim_line_id for g in groups for r in g if r.claim_line_id is not None]
    refs = claims.claim_references(ctx(request).db, tenant_id, ids) if ids else {}
    return [
        s.MatchOut(
            id=g[0].match_id,
            remittance_line_id=g[0].remittance_line_id,
            method=g[0].method,
            status=g[0].status,
            score=g[0].score,
            revision=g[0].revision,
            evidence=g[0].evidence,
            allocations=[
                s.AllocationOut(
                    claim_line_id=r.claim_line_id,
                    claim_adjustment_id=r.claim_adjustment_id,
                    claim_reference=refs.get(r.claim_line_id) if r.claim_line_id else None,
                    allocated_amount=r.allocated_amount,
                )
                for r in g
            ],
            confirmed_by=g[0].confirmed_by,
            confirmed_at=g[0].confirmed_at,
            rejected_by=g[0].rejected_by,
            rejected_at=g[0].rejected_at,
            created_at=g[0].created_at,
        )
        for g in groups
    ]


# ---- remittance documents -----------------------------------------------------------------------------------


@router.post(
    "/remittances", status_code=202, operation_id="remittance_create", response_model=s.RemittanceAccepted
)
def create_remittance(
    request: Request,
    body: s.RemittanceIn,
    principal: Annotated[Principal, Depends(require(WRITE))],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    c, who = ctx(request), _who(request, principal)
    key = ingestion.parse_key(idempotency_key)
    payload = body.model_dump(mode="json")
    replayed = ingestion.replay(c.db, who.tenant_id, key, "POST /remittances", payload)
    if replayed is not None:
        return JSONResponse(replayed[1], replayed[0])
    doc, job_id = ingest.create(c.db, who, body)
    out = s.RemittanceAccepted(remittance=s.RemittanceOut.model_validate(doc), job=_job(job_id, doc.id))
    ingestion.store(c.db, who.tenant_id, key, "POST /remittances", payload, 202, out.model_dump(mode="json"))
    return JSONResponse(out.model_dump(mode="json"), 202)


@router.get(
    "/remittances",
    operation_id="remittance_list",
    dependencies=[Depends(only_query_params("status", "manufacturer_id", *PAGED))],
)
def list_remittances(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    status: s.RemittanceStatus | None = None,
    manufacturer_id: UUID | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.RemittanceList:
    rows = ingest.list_documents(
        ctx(request).db,
        tenant_of(principal),
        status=status,
        manufacturer_id=manufacturer_id,
        limit=limit,
        cursor=cursor,
    )
    data, p = page(rows, limit)
    return s.RemittanceList(data=[s.RemittanceOut.model_validate(r) for r in data], page=p)


@router.get("/remittances/{remittance_id}", operation_id="remittance_get")
def get_remittance(
    request: Request, remittance_id: UUID, principal: Annotated[Principal, Depends(require(READ))]
) -> s.RemittanceOut:
    return s.RemittanceOut.model_validate(ingest.get(ctx(request).db, tenant_of(principal), remittance_id))


@router.get(
    "/remittances/{remittance_id}/lines",
    operation_id="remittance_line_list",
    dependencies=[Depends(only_query_params(*PAGED))],
)
def list_lines(
    request: Request,
    remittance_id: UUID,
    principal: Annotated[Principal, Depends(require(READ))],
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.RemittanceLineList:
    rows = ingest.list_lines(ctx(request).db, tenant_of(principal), remittance_id, limit=limit, cursor=cursor)
    data, p = page(rows, limit)
    return s.RemittanceLineList(data=[s.RemittanceLineOut.model_validate(r) for r in data], page=p)


@router.get("/remittances/{remittance_id}/extraction", operation_id="remittance_extraction_get")
def get_extraction(
    request: Request, remittance_id: UUID, principal: Annotated[Principal, Depends(require(WRITE))]
) -> s.RemittanceExtractionOut:
    v = ingest.extraction_view(ctx(request).db, tenant_of(principal), remittance_id)
    return s.RemittanceExtractionOut(
        remittance=s.RemittanceOut.model_validate(v["remittance"]),
        lines=[s.StagedLineOut.model_validate(r) for r in v["lines"]],
        problems=[s.RemittanceIssue(**i) for i in v["problems"]],
    )


@router.post("/remittances/{remittance_id}/accept", operation_id="remittance_accept")
def accept_remittance(
    request: Request,
    remittance_id: UUID,
    principal: Annotated[Principal, Depends(require(WRITE))],
    body: s.RemittanceAcceptIn | None = None,
) -> s.RemittanceOut:
    doc = ingest.accept(
        ctx(request).db, _who(request, principal), remittance_id, body or s.RemittanceAcceptIn()
    )
    return s.RemittanceOut.model_validate(doc)


@router.post("/remittances/{remittance_id}/void", operation_id="remittance_void")
def void_remittance(
    request: Request, remittance_id: UUID, principal: Annotated[Principal, Depends(require(WRITE))]
) -> s.RemittanceOut:
    return s.RemittanceOut.model_validate(
        ingest.void(ctx(request).db, _who(request, principal), remittance_id)
    )


# ---- reconciliation -----------------------------------------------------------------------------------------


@router.post("/remittances/{remittance_id}/reconcile", status_code=202, operation_id="remittance_reconcile")
def reconcile_remittance(
    request: Request, remittance_id: UUID, principal: Annotated[Principal, Depends(require(WRITE))]
) -> JSONResponse:
    job_id = matching.request_run(ctx(request).db, _who(request, principal), remittance_id)
    return JSONResponse({"job": _job(job_id, remittance_id).model_dump(mode="json")}, 202)


@router.get(
    "/remittances/{remittance_id}/reconciliation",
    operation_id="remittance_reconciliation_get",
    dependencies=[Depends(only_query_params(*PAGED))],
)
def get_reconciliation(
    request: Request,
    remittance_id: UUID,
    principal: Annotated[Principal, Depends(require(READ))],
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.ReconciliationOut:
    c, t = ctx(request), tenant_of(principal)
    doc = ingest.get(c.db, t, remittance_id)
    rows = ingest.list_lines(c.db, t, remittance_id, limit=limit, cursor=cursor)
    data, p = page(rows, limit)
    by_line: dict[UUID, list[ReconciliationMatch]] = defaultdict(list)
    for m in c.db.query(ReconciliationMatch).filter(
        ReconciliationMatch.tenant_id == t, ReconciliationMatch.remittance_line_id.in_([r.id for r in data])
    ):
        by_line[m.remittance_line_id].append(m)
    out = []
    for line in data:
        groups: dict[UUID, list[ReconciliationMatch]] = defaultdict(list)
        for m in sorted(by_line[line.id], key=lambda r: r.id):
            groups[m.match_id].append(m)
        matches = _match_outs(request, t, list(groups.values()))
        live = [m for m in matches if m.status != "rejected"]
        state = (
            "matched" if any(m.status == "confirmed" for m in live) else "suggested" if live else "unmatched"
        )
        out.append(
            s.ReconciliationLineOut(
                line=s.RemittanceLineOut.model_validate(line), state=state, matches=matches
            )
        )
    return s.ReconciliationOut(remittance=s.RemittanceOut.model_validate(doc), data=out, page=p)


@router.post(
    "/remittances/{remittance_id}/finalize", operation_id="remittance_finalize", response_model=s.FinaliseOut
)
def finalize_remittance(
    request: Request,
    remittance_id: UUID,
    principal: Annotated[Principal, Depends(require(WRITE))],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Any:
    c, who = ctx(request), _who(request, principal)
    key = ingestion.parse_key(idempotency_key)
    endpoint = f"POST /remittances/{remittance_id}/finalize"
    replayed = ingestion.replay(c.db, who.tenant_id, key, endpoint, {})
    if replayed is not None:
        return JSONResponse(replayed[1], replayed[0])
    result = matching.finalise(c.db, who, remittance_id)
    out = s.FinaliseOut(
        remittance=s.RemittanceOut.model_validate(ingest.get(c.db, who.tenant_id, remittance_id)),
        findings=[s.FindingOut(**f.__dict__) for f in result.findings],
    )
    ingestion.store(c.db, who.tenant_id, key, endpoint, {}, 200, out.model_dump(mode="json"))
    return out


# ---- matches ------------------------------------------------------------------------------------------------


@router.get(
    "/matches",
    operation_id="match_list",
    dependencies=[Depends(only_query_params("remittance_id", "status", *PAGED))],
)
def list_matches(
    request: Request,
    principal: Annotated[Principal, Depends(require(READ))],
    remittance_id: UUID | None = None,
    status: s.MatchStatus | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> s.MatchList:
    t = tenant_of(principal)
    groups, nxt = matching.list_matches(
        ctx(request).db, t, remittance_id=remittance_id, status=status, limit=limit, cursor=cursor
    )
    return s.MatchList(
        data=_match_outs(request, t, groups), page=Page(next_cursor=nxt, has_more=nxt is not None)
    )


@router.get("/matches/{match_id}", operation_id="match_get")
def get_match(
    request: Request,
    match_id: UUID,
    response: Response,
    principal: Annotated[Principal, Depends(require(READ))],
) -> s.MatchOut:
    t = tenant_of(principal)
    (out,) = _match_outs(request, t, [matching.get_match(ctx(request).db, t, match_id)])
    response.headers["ETag"] = f'"{out.revision}"'
    return out


def _decided(
    request: Request,
    principal: Principal,
    match_id: UUID,
    if_match: str | None,
    decision: str,
    response: Response,
) -> s.MatchOut:
    rows = matching.decide(ctx(request).db, _who(request, principal), match_id, _revision(if_match), decision)
    (out,) = _match_outs(request, tenant_of(principal), [rows])
    response.headers["ETag"] = f'"{out.revision}"'
    return out


@router.post("/matches/{match_id}/confirm", operation_id="match_confirm")
def confirm_match(
    request: Request,
    match_id: UUID,
    response: Response,
    principal: Annotated[Principal, Depends(require(WRITE))],
    if_match: Annotated[str | None, Header()] = None,
) -> s.MatchOut:
    return _decided(request, principal, match_id, if_match, "confirm", response)


@router.post("/matches/{match_id}/reject", operation_id="match_reject")
def reject_match(
    request: Request,
    match_id: UUID,
    response: Response,
    principal: Annotated[Principal, Depends(require(WRITE))],
    if_match: Annotated[str | None, Header()] = None,
) -> s.MatchOut:
    return _decided(request, principal, match_id, if_match, "reject", response)


@router.post("/remittance-lines/{line_id}/categorise", operation_id="remittance_line_categorise")
def categorise_line(
    request: Request,
    line_id: UUID,
    body: s.CategoriseIn,
    principal: Annotated[Principal, Depends(require(WRITE))],
) -> s.RemittanceLineOut:
    line = matching.categorise(ctx(request).db, _who(request, principal), line_id, body.category)
    return s.RemittanceLineOut.model_validate(line)


@router.post("/matches", status_code=201, operation_id="match_create")
def create_match(
    request: Request,
    body: s.ManualMatchIn,
    response: Response,
    principal: Annotated[Principal, Depends(require(WRITE))],
) -> s.MatchOut:
    rows = matching.manual(
        ctx(request).db,
        _who(request, principal),
        body.remittance_line_id,
        body.claim_line_id,
        body.allocated_amount,
        body.claim_adjustment_id,
    )
    (out,) = _match_outs(request, tenant_of(principal), [rows])
    response.headers["ETag"] = f'"{out.revision}"'
    return out
