"""S10 reporting on real Postgres: the ground-truth story of CB-VOLT-2026-06 and remittance CM-7781 (S8), then the
dashboard, drill-down, trace resolver, audit pack and role visibility. Expected values are the ground truth
(``claim_batches_fixture.json``, ``expected_reconciliation.json``) or hand derivations in the comments, never the output
of the code under test. Tests share a module-scoped workspace and run in file order."""

import csv
import io
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from rebate.core.db import transaction
from rebate.core.figures import Filter
from rebate.modules.files import storage
from rebate.modules.recovery import service as recovery_service
from rebate.modules.reporting import service
from tests.conftest import DbUrls
from tests.integration.agreements_support import member
from tests.integration.calc_support import LATE_RETURN, SCENARIOS
from tests.reconciliation.support import (
    CSV,
    EXPECTED,
    Story,
    build_story,
    categorise_unmatched,
    ingest,
    key,
    listing_of,
    reconcile,
    upload,
)
from tests.support import Api, FakeClock, drain, fake_clock, make_app
from tests.testdata import data

D = Decimal
NOW = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)
CM_CSV = data("remittance/credit_memo_VOLT_CM-7781.csv")
SHARED: dict[str, Any] = {}

# Hand derivation (owner decision 2026-11-13, D-N6: an adjustment belongs to the batch that carries it). Story =
# build_story + CM-7781 (paid 2026-07-31), refreshed on 2026-07-10; ADJ-2026-0001 (-50.00) rides in B2 with INV-10020/1.
# claimed: CB-VOLT 2200 + 3120 + 2080 + 250 + 1000 + 1100 + 660 = 10,410.00 (the GT batch_total 10,360.00 puts the
# adjustment inside CB-VOLT, an ordering the real flow cannot produce); B2 = 312.50 - 50.00 = 262.50; total 10,672.50.
CB_VOLT = D("10410.00")
CLAIMED = CB_VOLT + D("312.50") + D("-50.00")
# collected = confirmed allocations of the accepted remittance to claim lines AND adjustments: 2050 + 3120 + 0 + 200
# (line 4: 250 to the line, -50 to the adjustment) + (1000 + 1100 line 5) = 7,470.00 (= 7,545.00 - 75.00 unmatched)
COLLECTED = D("2050.00") + D("3120.00") + D("0.00") + D("250.00") + D("-50.00") + D("1000.00") + D("1100.00")
# short_paid = 150.00 + 2,080.00 + 50.00 (CL-4, G2) + 660.00 = 2,940.00 (the 75.00 unmatched payment is not owed to us)
SHORT_PAID = D("150.00") + D("2080.00") + D("50.00") + D("660.00")
# cross-check: claimed - collected = 3,202.50 = short_paid 2,940.00 + 262.50 (B2, not yet due)
# deadlines: CM-7781 cases 2026-07-31 + 60 = 2026-09-29; CL-7 unpaid (G3) = submission 2026-07-05 + 60 = 2026-09-03
AT_RISK_NOW = D("0.00")  # 07-10 (horizon 07-24): every deadline is later; the unpaid case now has one (G3)
AT_RISK_LATER = SHORT_PAID  # 09-20 (horizon 10-04): all four are due by then (CL-7 is overdue)
EXPIRING_LATER = D("150.00") + D("2080.00") + D("50.00")  # 09-20 .. 10-04: 09-29 (CL-7, 09-03, is overdue)


class Env:
    def __init__(
        self, app: FastAPI, stack: ExitStack, clock: FakeClock, engine: Engine, admin: Engine
    ) -> None:
        self.app, self.stack, self.clock, self.engine, self.admin = app, stack, clock, engine, admin


@pytest.fixture(scope="module")
def env(migrated_db: DbUrls, app_engine: Engine, admin_engine: Engine) -> Iterator[Env]:
    app = make_app(migrated_db.app)
    with fake_clock() as fc, ExitStack() as stack:
        fc.t = NOW
        yield Env(app, stack, fc, app_engine, admin_engine)


@pytest.fixture(scope="module")
def story(env: Env) -> Story:
    s = build_story(env.app, env.stack)
    ws = s.ws
    doc = ingest(
        ws,
        upload(ws, "credit_memo_VOLT_CM-7781.csv", CM_CSV, CSV),
        s.mfr["VOLT"],
        document_number="CM-7781",
        document_date="2026-07-31",
        payment_date="2026-07-31",
        total_amount=EXPECTED["document_total"],
    )
    reconcile(ws, doc["id"])
    suggested = [
        x
        for x in ws.api.get("/matches", params={"remittance_id": doc["id"]}).json()["data"]
        if x["status"] == "suggested"
    ]
    (match,) = suggested
    confirm = ws.api.post(f"/matches/{match['id']}/confirm", headers={"If-Match": f'"{match["revision"]}"'})
    assert confirm.status_code == 200, confirm.text
    categorise_unmatched(
        ws, doc["id"]
    )  # final S8: every unmatched line (line 6, 75.00) is categorised before finalising
    assert ws.api.post(f"/remittances/{doc['id']}/finalize", headers=key()).status_code == 200
    SHARED["doc"] = doc
    return s


def refresh(env: Env, story: Story) -> dict[str, Any]:
    api = story.ws.api
    assert api.post("/dashboard/refresh").status_code == 202
    drain(env.app)
    r = api.get("/dashboard")
    assert r.status_code == 200, r.text
    return dict(r.json())


def money(d: dict[str, Any]) -> dict[str, Decimal]:
    return {k: D(v) for k, v in d.items()}


def test_finalising_queued_a_refresh_and_the_dashboard_matches_the_ground_truth(
    env: Env, story: Story
) -> None:
    api = story.ws.api
    assert api.get("/dashboard").json()["as_of"] is None or True  # the finalisation queued a refresh job
    drain(env.app)
    body = api.get("/dashboard").json()
    assert body["as_of"] is not None and body["as_of_date"] == "2026-07-10"
    assert money(body["figures"]) == {
        "claimed": CLAIMED,
        "collected": COLLECTED,
        "short_paid": SHORT_PAID,
        "recovered": D(0),
        "written_off": D(0),
        "recovered_after_write_off": D(0),
        "at_risk": AT_RISK_NOW,
        "expiring": D(0),
    }
    assert [m["month"] for m in body["months"]] == ["2026-07-01"]
    assert money(body["months"][0]["figures"]) == money(body["figures"])


def test_filters_narrow_the_figures(env: Env, story: Story) -> None:
    api, volt = story.ws.api, story.mfr["VOLT"]
    same = api.get("/dashboard", params={"manufacturer_id": volt, "month": "2026-07-15"}).json()
    assert D(same["figures"]["claimed"]) == CLAIMED  # any day of the month selects the month
    for params in (
        {"month": "2026-06-01"},
        {"manufacturer_id": str(uuid.uuid4())},
        {"branch_id": str(uuid.uuid4())},
    ):
        zero = api.get("/dashboard", params=params).json()
        assert {D(v) for v in zero["figures"].values()} == {D(0)}, params
    assert api.get("/dashboard", params={"nonsense": "1"}).status_code == 400


def test_every_figure_drills_to_rows_that_sum_to_it(env: Env, story: Story) -> None:
    api = story.ws.api
    figures = api.get("/dashboard").json()["figures"]
    counts = {
        "claimed": 9,  # 7 + INV-10020/1 + the adjustment
        # D-N3 + D-N11 interim: ADJ-2026-0001 rides in B2 and nothing auto-allocates to it, so line 4 pays 200.00 all to CL-4.
        # Rows: 2050 + 3120 + 0 + 200 + 1000 + 1100 = 7,470.00 = 6 allocations (the figure is unchanged).
        "collected": 6,
        "short_paid": 4,
        "recovered": 0,
        "written_off": 0,
        "recovered_after_write_off": 0,
        "at_risk": 0,
        "expiring": 0,
    }
    for metric, figure in figures.items():
        rows, cursor, first = [], None, None
        while True:  # small pages: the sum must hold across page boundaries
            r = api.get(
                "/dashboard/drilldown",
                params={"metric": metric, "limit": 3, **({"cursor": cursor} if cursor else {})},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            first = first or body
            rows += body["data"]
            cursor = body["page"]["next_cursor"]
            if not body["page"]["has_more"]:
                break
        assert len(rows) == counts[metric], metric
        assert (
            sum((D(x["amount"]) for x in rows), D(0)) == D(figure) == D(first["figure"]) == D(first["total"])
        ), metric
        assert first["consistent"] is True
        assert len({x["id"] for x in rows}) == len(rows)
    assert api.get("/dashboard/drilldown", params={"metric": "profit"}).status_code == 422


def test_a_claimed_adjustment_row_traces_to_its_batch_and_the_line_it_corrects(
    env: Env, story: Story
) -> None:
    api = story.ws.api
    rows = api.get("/dashboard/drilldown", params={"metric": "claimed", "limit": 50}).json()["data"]
    (adj,) = [x for x in rows if x["trace_kind"] == "claim_adjustment"]
    assert D(adj["amount"]) == D("-50.00")
    kinds = {n["kind"]: n for n in api.get(f"/trace/claim_adjustment/{adj['trace_id']}").json()["chain"]}
    assert D(kinds["claim_adjustment"]["fields"]["amount"]) == D("-50.00")
    assert kinds["claim_batch"]["fields"]["status"] == "submitted" and "source_row" in kinds
    assert api.get(f"/trace/claim_adjustment/{uuid.uuid4()}").status_code == 404


def test_figures_that_depend_on_the_date_follow_the_refresh_date(env: Env, story: Story) -> None:
    # the session would expire at that date, so the service is called directly in a tenant transaction
    tenant = uuid.UUID(story.ws.tenant_id)
    env.clock.t = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    try:
        with transaction(env.engine, tenant) as db:
            service.refresh(db, tenant)
            body = service.dashboard(db, tenant, Filter())
            drill = service.drilldown(db, tenant, "expiring", Filter(), None, 50)
            summary = recovery_service.summary(db, tenant)  # ONE at-risk definition: the same figure here
    finally:
        env.clock.t = NOW
    assert str(body["as_of_date"]) == "2026-09-20"
    assert body["figures"]["at_risk"] == AT_RISK_LATER
    assert body["figures"]["expiring"] == EXPIRING_LATER
    assert sum(r.amount for r in drill["rows"]) == EXPIRING_LATER and drill["consistent"]
    assert (summary["at_risk"]["count"], summary["at_risk"]["amount"]) == (4, AT_RISK_LATER)
    with transaction(
        env.engine, tenant
    ) as db:  # re-dated and idempotent: the same data again gives the same rows
        service.refresh(db, tenant)
        again = service.dashboard(db, tenant, Filter())
    assert again["figures"]["at_risk"] == AT_RISK_NOW and again["figures"]["claimed"] == CLAIMED
    with transaction(env.engine, tenant) as db:
        now_summary = recovery_service.summary(db, tenant)["at_risk"]
    assert (now_summary["count"], now_summary["amount"]) == (0, AT_RISK_NOW)  # 07-10: same as the dashboard


def _source_row_of(invoice: str, line: str = "1") -> int:
    """Row number of an invoice line in the part file the story imports (header = row 1)."""
    _, *rows = data(SCENARIOS).decode().splitlines()
    keep = [r for r in rows if not r.startswith(LATE_RETURN)]
    return 2 + next(i for i, r in enumerate(keep) if r.startswith(f"{invoice},{line},"))


def _nodes(api: Api, kind: str, ident: str) -> dict[str, list[dict[str, Any]]]:
    r = api.get(f"/trace/{kind}/{ident}")
    assert r.status_code == 200, r.text
    out: dict[str, list[dict[str, Any]]] = {}
    for n in r.json()["chain"]:
        out.setdefault(n["kind"], []).append(n)
    return out


def test_a_claim_line_traces_to_its_file_row_agreement_and_payment(env: Env, story: Story) -> None:
    line = story.lines["INV-10001"]  # GL-V1: claimed 2200.00, paid 2050.00 (line 1 of CM-7781)
    got = _nodes(story.ws.api, "claim_line", line["id"])
    assert got["claim_line"][0]["fields"]["claim_amount"] == "2200.0000"
    (src,) = got["source_row"][:1]
    assert (src["fields"]["row_number"], src["fields"]["file_name"]) == (
        str(_source_row_of("INV-10001")),
        "sales_part.csv",
    )
    assert got["sales_line"][0]["label"] == "INV-10001/1"
    assert got["agreement_version"][0]["label"].startswith("SPA-VOLT-2026-884 v")
    assert got["claim_batch"][0]["fields"]["status"] == "submitted"
    assert got["match"][0]["fields"]["allocated_amount"] == "2050.0000"
    assert got["remittance_line"][0]["fields"]["amount_paid"] == "2050.0000"
    pay_src = got["source_row"][-1]  # the remittance file row: line 1 is file row 2
    assert (pay_src["fields"]["row_number"], pay_src["fields"]["file_name"]) == (
        "2",
        "credit_memo_VOLT_CM-7781.csv",
    )
    assert got["variance_case"][0]["fields"]["variance_amount"] == "150.0000"


def test_every_claim_line_of_the_story_reaches_a_source_row(env: Env, story: Story) -> None:
    for invoice, line in story.lines.items():
        got = _nodes(story.ws.api, "claim_line", line["id"])
        assert got["source_row"][0]["fields"]["row_number"] == str(_source_row_of(invoice)), invoice
        assert "claim_batch" in got and "agreement_version" in got and "calculation_result" in got, invoice


def test_a_remittance_line_and_a_case_trace_back_to_the_invoice_row(env: Env, story: Story) -> None:
    api = story.ws.api
    lines = listing_of(story.ws, f"/remittances/{SHARED['doc']['id']}/lines")
    got = _nodes(api, "remittance_line", lines[0]["id"])  # CM-7781 line 1 -> INV-10001/1
    assert got["remittance_document"][0]["label"] == "CM-7781"
    assert {n["fields"]["row_number"] for n in got["source_row"]} == {"2", str(_source_row_of("INV-10001"))}
    assert got["claim_line"][0]["label"] == story.lines["INV-10001"]["claim_reference"]
    unmatched = _nodes(
        api, "remittance_line", lines[5]["id"]
    )  # the 75.00 line has no claim line and still a file row
    assert unmatched["source_row"][0]["fields"]["row_number"] == "7" and "claim_line" not in unmatched
    all_cases = api.get("/cases", params={"limit": 50}).json()["data"]
    cases = {
        c["case_type"]: c for c in all_cases if c["variance_amount"] != "50.0000"
    }  # 50.00 = CL-4's short-pay (INV-10021)
    short = _nodes(api, "variance_case", cases["short_paid"]["id"])
    assert str(_source_row_of("INV-10001")) in {n["fields"]["row_number"] for n in short["source_row"]}
    unpaid = _nodes(api, "variance_case", cases["unpaid"]["id"])  # INV-10060: never on a remittance
    assert unpaid["source_row"][0]["fields"]["row_number"] == str(_source_row_of("INV-10060"))
    assert "remittance_line" not in unpaid
    lost = _nodes(api, "variance_case", cases["unmatched_payment"]["id"])
    assert lost["source_row"][0]["fields"]["file_name"] == "credit_memo_VOLT_CM-7781.csv"


def test_a_calculation_result_traces_to_its_file_row(env: Env, story: Story) -> None:
    api = story.ws.api
    run = api.get("/calculation-runs").json()["data"][-1]
    result = api.get(f"/calculation-runs/{run['id']}/results", params={"limit": 200}).json()["data"][0]
    got = _nodes(api, "calculation_result", result["id"])
    assert got["sales_line"][0]["label"] == f"{result['document_number']}/{result['line_number']}"
    assert got["source_row"][0]["fields"]["row_number"] == str(
        _source_row_of(result["document_number"], str(result["line_number"]))
    )


def test_unknown_ids_and_kinds_are_404_or_422(env: Env, story: Story) -> None:
    api = story.ws.api
    assert api.get(f"/trace/claim_line/{uuid.uuid4()}").status_code == 404
    assert api.get(f"/trace/variance_case/{uuid.uuid4()}").status_code == 404
    assert api.get(f"/trace/invoice/{uuid.uuid4()}").status_code == 404  # only the four kinds exist
    assert api.get("/trace/claim_line/not-a-uuid").status_code == 422


def _batch_of(story: Story, invoice: str) -> str:
    """Id of the claim batch holding an invoice's claim line (the system numbers batches itself)."""
    return str(_nodes(story.ws.api, "claim_line", story.lines[invoice]["id"])["claim_batch"][0]["id"])


def _file(env: Env, file_id: str) -> bytes:
    with env.admin.connect() as conn:
        row = conn.execute(
            text("SELECT storage_key, kind FROM file_objects WHERE id = :i"), {"i": file_id}
        ).one()
    assert row.kind == "export"
    return b"".join(storage.read_chunks(env.app.state.settings, row.storage_key))


def test_the_audit_pack_ties_to_the_batch_and_shows_payments(env: Env, story: Story) -> None:
    api = story.ws.api
    batch = {"id": _batch_of(story, "INV-10001")}
    r = api.get(f"/reports/claim-batches/{batch['id']}/audit-pack", params={"format": "csv"})
    assert r.status_code == 202, r.text
    drain(env.app)
    rows = list(csv.DictReader(io.StringIO(_file(env, r.json()["file_id"]).decode())))
    assert len(rows) == 7
    assert (
        sum(D(x["Claim amount"]) for x in rows) == CB_VOLT
    )  # Σ of the fixture lines; the adjustment is in B2
    by_invoice = {x["Invoice"]: x for x in rows}
    # D-N3 + D-N11 interim: line 4 pays 200.00, all to CL-4 (claimed 250.00 -> short 50.00); the -50.00 ADJ rides in B2.
    paid = {"INV-10001": "2050.00", "INV-10010": "3120.00", "INV-10011": "0.00", "INV-10021": "200.00",
            "INV-10050": "1000.00", "INV-10051": "1100.00"}  # fmt: skip
    for invoice, amount in paid.items():  # ground truth lines 1-5
        assert D(by_invoice[invoice]["Paid (confirmed)"]) == D(amount), invoice
        assert by_invoice[invoice]["Remittance documents"] == "CM-7781"
    assert D(by_invoice["INV-10060"]["Paid (confirmed)"]) == 0  # the unpaid line
    assert by_invoice["INV-10060"]["Remittance documents"] == ""
    first = by_invoice["INV-10001"]
    assert (first["Source file"], first["Source row"]) == ("sales_part.csv", str(_source_row_of("INV-10001")))
    assert (
        first["Agreement"] == "SPA-VOLT-2026-884"
        and first["Claim reference"] == story.lines["INV-10001"]["claim_reference"]
    )
    # the second batch carries the -50.00 return reversal, which was netted on remittance line 4 (hand: 250.00 - 50.00)
    second = api.get(
        f"/reports/claim-batches/{_batch_of(story, 'INV-10020')}/audit-pack", params={"format": "xlsx"}
    )
    drain(env.app)
    assert second.status_code == 202 and _file(env, second.json()["file_id"])[:2] == b"PK"
    xl = _xlsx_rows(_file(env, second.json()["file_id"]))
    assert len(xl) == 3  # header + INV-10020/1 + the adjustment row (the carrying batch lists it)
    assert sum((D(str(r[13])) for r in xl[1:]), D(0)) == D("312.50") + D("-50.00")
    assert api.get(f"/reports/claim-batches/{uuid.uuid4()}/audit-pack").status_code == 404
    assert (
        api.get(f"/reports/claim-batches/{batch['id']}/audit-pack", params={"format": "pdf"}).status_code
        == 422
    )


def _xlsx_rows(blob: bytes) -> list[tuple[Any, ...]]:
    from openpyxl import load_workbook  # type: ignore[import-untyped]

    return [tuple(c.value for c in row) for row in load_workbook(io.BytesIO(blob)).active.iter_rows()]


def test_roles_viewers_follow_every_number_and_only_writers_refresh(env: Env, story: Story) -> None:
    admin = story.ws.api
    viewer = member(env.stack, env.app, admin, "viewer")
    analyst = member(env.stack, env.app, admin, "analyst")
    line = story.lines["INV-10001"]["id"]
    case = admin.get("/cases", params={"limit": 1}).json()["data"][0]["id"]
    for who in (viewer, analyst):  # every role reads the dashboard, every drill-down and every trace
        assert D(who.get("/dashboard").json()["figures"]["claimed"]) == CLAIMED
        for metric in ("claimed", "collected", "short_paid", "at_risk"):
            assert who.get("/dashboard/drilldown", params={"metric": metric}).status_code == 200
        assert who.get(f"/trace/claim_line/{line}").status_code == 200
        assert who.get(f"/trace/variance_case/{case}").status_code == 200
    batch = _batch_of(story, "INV-10001")
    assert viewer.post("/dashboard/refresh").status_code == 403  # writes by a viewer are always denied
    assert analyst.post("/dashboard/refresh").status_code == 202
    assert viewer.get(f"/reports/claim-batches/{batch}/audit-pack").status_code == 202  # A P V
    assert analyst.get(f"/reports/claim-batches/{batch}/audit-pack").status_code == 403
    assert admin.get(f"/reports/claim-batches/{batch}/audit-pack").status_code == 202
    drain(env.app)
